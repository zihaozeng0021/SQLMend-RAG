from __future__ import annotations

import gc
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from .constants import ALLOWED_DIALECTS
from .dedup import deduplicate_records
from .metadata import (
    classify_topic,
    contains_error_code,
    contains_sql,
    contains_version_or_compatibility,
    infer_explicit_release_version,
)
from .utils import iter_jsonl, lexical_tokens, load_yaml, sha256_text, word_count, write_json_atomic, write_jsonl_atomic

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+(?=[A-Z0-9`\[])|\n{2,}")
ATOMIC_SECTION_RE = re.compile(r"\b(?:syntax|synopsis|signature|parameters?|returns?|return value|description)\b", re.I)


def _split_words(text: str, maximum: int, overlap: int = 0) -> list[str]:
    tokens = text.split()
    if len(tokens) <= maximum:
        return [text.strip()]
    step = max(1, maximum - overlap)
    return [" ".join(tokens[start : start + maximum]) for start in range(0, len(tokens), step) if tokens[start : start + maximum]]


def _split_prose(text: str, maximum: int) -> list[str]:
    if word_count(text) <= maximum:
        return [text]
    sentences = [sentence.strip() for sentence in SENTENCE_SPLIT_RE.split(text) if sentence.strip()]
    if len(sentences) <= 1:
        return _split_words(text, maximum)
    pieces: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        size = word_count(sentence)
        if size > maximum:
            if current:
                pieces.append(" ".join(current))
                current, current_words = [], 0
            pieces.extend(_split_words(sentence, maximum))
        elif current and current_words + size > maximum:
            pieces.append(" ".join(current))
            current, current_words = [sentence], size
        else:
            current.append(sentence)
            current_words += size
    if current:
        pieces.append(" ".join(current))
    return pieces


def _protected_units(blocks: list[dict[str, str]], maximum: int, keep_code: bool, keep_tables: bool) -> list[str]:
    units: list[str] = []
    for block in blocks:
        block_type = block.get("type", "prose")
        text = block.get("text", "").strip()
        if not text:
            continue
        if block_type == "table" and keep_tables and word_count(text) > maximum:
            lines = [line for line in text.splitlines() if line.strip()]
            heading = lines[0] if lines and lines[0].startswith("Table:") else "Table:"
            rows = lines[1:] if lines and lines[0].startswith("Table:") else lines
            table_parts: list[str] = []
            current_rows: list[str] = []
            current_words = word_count(heading)
            for row in rows:
                row_words = word_count(row)
                if current_rows and current_words + row_words > maximum:
                    table_parts.append(heading + "\n" + "\n".join(current_rows))
                    current_rows = []
                    current_words = word_count(heading)
                current_rows.append(row)
                current_words += row_words
            if current_rows:
                table_parts.append(heading + "\n" + "\n".join(current_rows))
            units.extend(table_parts)
            continue
        protected = (block_type == "code" and keep_code) or (block_type == "table" and keep_tables)
        if protected:
            # Bind an example/table to its immediately preceding explanation. Atomic
            # material may exceed max size rather than being cut into incoherent pieces.
            if units and word_count(units[-1]) + word_count(text) <= maximum * 2:
                units[-1] = units[-1] + "\n\n" + text
            else:
                units.append(text)
        else:
            units.extend(_split_prose(text, maximum))
    return units


def _render_chunk(title: str, section: str, body: str) -> str:
    context = f"Title: {title}"
    if section and section != title:
        context += f"\nSection: {section}"
    return f"{context}\n\n{body.strip()}".strip()


def _overlap_context(text: str, overlap: int) -> str:
    """Create an explicitly labelled, preferably sentence-aligned overlap."""
    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part.strip()]
    selected: list[str] = []
    selected_words = 0
    for sentence in reversed(sentences):
        size = word_count(sentence)
        if selected and selected_words + size > overlap:
            break
        if not selected and size > overlap * 2:
            words = sentence.split()
            selected = [" ".join(words[-overlap:])]
            break
        selected.insert(0, sentence)
        selected_words += size
    tail = " ".join(selected).strip()
    return f"Context carried from the preceding passage: {tail}" if tail else ""


def _chunk_body(text: str) -> str:
    """Remove the synthetic title/section prefix before joining siblings."""
    parts = text.split("\n\n", 1)
    return parts[1].strip() if len(parts) == 2 else text.strip()


def _same_merge_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Protect URL/version boundaries and distinct error records."""
    if left.get("source_url") != right.get("source_url"):
        return False
    if left.get("source_type") == "error_reference" or right.get("source_type") == "error_reference":
        return False
    if left.get("contains_error_code") or right.get("contains_error_code"):
        return False
    return all(
        left.get(field) == right.get(field)
        for field in ("version", "version_min", "version_max", "version_status")
    )


def _merge_adjacent_small_chunks(
    document: dict[str, Any], chunks: list[dict[str, Any]], minimum: int, maximum: int
) -> list[dict[str, Any]]:
    """Attach short sibling fragments such as signatures and parameter notes.

    Documentation generators frequently make Signature, Parameters, and Return
    Value separate sibling headings.  Keeping each as a tiny passage loses the
    relationship the RAG system needs, so adjacent fragments are joined while
    respecting source URL, version, and error-record boundaries.
    """
    output: list[dict[str, Any]] = []
    for chunk in chunks:
        if not output:
            output.append(chunk)
            continue
        previous = output[-1]
        # Treat the last short chunk as pending and attach the following sibling.
        # Do not repeatedly append every new short sibling to an already complete
        # chunk, which would create an oversized API catalogue passage.
        previous_body = _chunk_body(previous["text"])
        current_body = _chunk_body(chunk["text"])
        continuation = bool(re.match(r"^[a-z]", current_body))
        should_merge = word_count(previous_body) < minimum or continuation
        combined_words = word_count(previous_body) + word_count(current_body)
        if not should_merge or combined_words > maximum * 2 or not _same_merge_scope(previous, chunk):
            output.append(chunk)
            continue

        left_section = str(previous.get("section") or document.get("title") or "")
        right_section = str(chunk.get("section") or document.get("title") or "")
        if left_section == right_section:
            section = left_section
        else:
            base_section = left_section.split(" + adjacent subsection", 1)[0]
            section = f"{base_section} + adjacent subsections"
        body = _chunk_body(previous["text"])
        right_body = _chunk_body(chunk["text"])
        if right_section != left_section:
            body += f"\n\nRelated subsection: {right_section}\n\n{right_body}"
        else:
            body += f"\n\n{right_body}"
        rendered = _render_chunk(document.get("title") or "Untitled documentation", section, body)
        if word_count(body) > maximum * 2:
            output.append(chunk)
            continue
        section_document = dict(document)
        section_document["source_url"] = previous.get("source_url")
        output[-1] = make_chunk(section_document, section, rendered, "structure", 0, 0)
    return output


def structure_chunks(document: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    target = int(config["target_words"])
    minimum = int(config["minimum_words"])
    maximum = int(config["maximum_words"])
    overlap = int(config.get("overlap_words", 0))
    chunks: list[dict[str, Any]] = []
    for section_index, section in enumerate(document.get("sections", [])):
        units = _protected_units(
            section.get("blocks", []),
            maximum,
            bool(config.get("keep_code_blocks_intact", True)),
            bool(config.get("keep_tables_intact", True)),
        )
        bodies: list[str] = []
        current: list[str] = []
        current_words = 0
        pending_overlap = ""

        def flush() -> None:
            nonlocal current, current_words, pending_overlap
            if not current:
                return
            bodies.append("\n\n".join(current).strip())
            pending_overlap = ""
            if overlap > 0:
                last = current[-1]
                if "```" not in last and not last.startswith("Table:"):
                    pending_overlap = _overlap_context(last, overlap)
            current = []
            current_words = 0

        for unit in units:
            size = word_count(unit)
            if current and current_words >= minimum and current_words + size > target:
                flush()
            if current and current_words + size > maximum:
                flush()
            if not current and pending_overlap:
                overlap_words = word_count(pending_overlap)
                if overlap_words + size <= maximum:
                    current = [pending_overlap]
                    current_words = overlap_words
                pending_overlap = ""
            current.append(unit)
            current_words += size
            if current_words >= maximum:
                flush()
        flush()
        if len(bodies) >= 2 and word_count(bodies[-1]) < minimum:
            merged = bodies[-2] + "\n\n" + bodies[-1]
            if word_count(merged) <= maximum * 2:
                bodies[-2:] = [merged]
        for part_index, body in enumerate(bodies):
            rendered = _render_chunk(document.get("title") or "Untitled documentation", section.get("section") or "", body)
            section_document = dict(document)
            if section.get("source_url"):
                section_document["source_url"] = section["source_url"]
            chunks.append(
                make_chunk(section_document, section.get("section") or "", rendered, "structure", section_index, part_index)
            )
    merged = _merge_adjacent_small_chunks(document, chunks, minimum, maximum)
    # A tiny standalone navigation/overview fragment has little retrieval value.
    # Keep short records only when they are an intentional atomic unit.
    return [
        chunk
        for chunk in merged
        if word_count(_chunk_body(chunk["text"])) >= minimum
        or chunk.get("contains_sql")
        or chunk.get("contains_error_code")
        or "```" in chunk["text"]
        or "\nTable:\n" in chunk["text"]
        or ATOMIC_SECTION_RE.search(str(chunk.get("section") or ""))
    ]


def fixed_chunks(document: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    size = int(config["size_words"])
    overlap = int(config.get("overlap_words", 0))
    all_text = "\n\n".join(
        f"{section.get('section', '')}\n" + "\n\n".join(block["text"] for block in section.get("blocks", []))
        for section in document.get("sections", [])
    )
    pieces = _split_words(all_text, size, overlap)
    return [
        make_chunk(
            document,
            "Fixed-size baseline",
            _render_chunk(document.get("title") or "Untitled documentation", "Fixed-size baseline", piece),
            "fixed",
            0,
            index,
        )
        for index, piece in enumerate(pieces)
        if word_count(piece) >= int(config.get("minimum_words", 20))
    ]


def _chunk_version(document: dict[str, Any], section: str, text: str) -> dict[str, Any]:
    metadata = {
        "version": document.get("version"),
        "version_min": document.get("version_min"),
        "version_max": document.get("version_max"),
        "version_status": document.get("version_status", "unknown"),
    }
    if document.get("source_type") == "release_notes":
        # Only titles/headings are authoritative enough for chunk-level version
        # refinement.  Searching arbitrary body text can mistake a dependency,
        # benchmark, or client-library version for the database release.
        inferred = infer_explicit_release_version(str(document.get("title") or ""))
        if not inferred:
            inferred = infer_explicit_release_version(section)
        if inferred:
            metadata.update(inferred)
    return metadata


def make_chunk(
    document: dict[str, Any],
    section: str,
    text: str,
    strategy: str,
    section_index: int,
    part_index: int,
) -> dict[str, Any]:
    dialect = document["dialect"]
    version = _chunk_version(document, section, text)
    identity = "|".join(
        [document["document_id"], strategy, str(section_index), str(part_index), section, sha256_text(text)]
    )
    chunk_id = f"smr_{dialect}_{sha256_text(identity)[:24]}"
    source_type = document.get("source_type") or "other"
    return {
        "chunk_id": chunk_id,
        "document_id": document["document_id"],
        "dialect": dialect,
        "vendor_or_project": document.get("vendor_or_project"),
        **version,
        "source_type": source_type,
        "source_name": document.get("source_name"),
        "source_url": document.get("source_url"),
        "title": document.get("title"),
        "section": section or document.get("title"),
        "text": text,
        "contains_sql": contains_sql(text),
        "contains_error_code": contains_error_code(text, dialect),
        "retrieved_at": document.get("retrieved_at"),
        "content_hash": sha256_text(text),
        "source_id": document.get("source_id"),
        "authority_class": document.get("authority_class"),
        "topic": classify_topic(section, text, source_type),
        "contains_version_or_compatibility": contains_version_or_compatibility(text),
        "chunking_strategy": strategy,
    }


def select_balanced(chunks: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not config.get("enabled", True):
        return sorted(chunks, key=lambda row: row["chunk_id"]), {"enabled": False}
    target = int(config.get("target_chunks_per_dialect", 2400))
    minimum_total = int(config.get("minimum_total_chunks", 10000))
    by_dialect: dict[str, list[dict[str, Any]]] = {dialect: [] for dialect in ALLOWED_DIALECTS}
    for chunk in chunks:
        by_dialect[chunk["dialect"]].append(chunk)

    selected: list[dict[str, Any]] = []
    remaining: dict[str, deque[dict[str, Any]]] = {}
    for dialect in ALLOWED_DIALECTS:
        groups: dict[str, dict[tuple[str, str, str], deque[dict[str, Any]]]] = defaultdict(lambda: defaultdict(deque))
        for chunk in sorted(by_dialect[dialect], key=lambda row: row["chunk_id"]):
            version_label = str(chunk.get("version") or chunk.get("version_status"))
            key = (str(chunk["source_id"]), chunk["source_type"], version_label)
            groups[chunk["topic"]][key].append(chunk)
        topic_queues: dict[str, deque[dict[str, Any]]] = {}
        for topic, subgroups in groups.items():
            subgroup_queues = [subgroups[key] for key in sorted(subgroups)]
            topic_items: deque[dict[str, Any]] = deque()
            while subgroup_queues:
                next_subgroups = []
                for subgroup in subgroup_queues:
                    if subgroup:
                        topic_items.append(subgroup.popleft())
                    if subgroup:
                        next_subgroups.append(subgroup)
                subgroup_queues = next_subgroups
            topic_queues[topic] = topic_items
        ordered = []
        active_topics = sorted(topic_queues)
        while active_topics:
            next_topics = []
            for topic in active_topics:
                queue = topic_queues[topic]
                if queue:
                    ordered.append(queue.popleft())
                if queue:
                    next_topics.append(topic)
            active_topics = next_topics
        selected.extend(ordered[:target])
        remaining[dialect] = deque(ordered[target:])

    # If a sparse dialect leaves the first pass below the hard total, fill from the
    # currently least represented dialect that still has useful unused material.
    while len(selected) < minimum_total:
        counts = {dialect: sum(row["dialect"] == dialect for row in selected) for dialect in ALLOWED_DIALECTS}
        choices = [dialect for dialect in ALLOWED_DIALECTS if remaining[dialect]]
        if not choices:
            break
        dialect = min(choices, key=lambda name: (counts[name], name))
        selected.append(remaining[dialect].popleft())
    selected.sort(key=lambda row: (row["dialect"], row["chunk_id"]))
    return selected, {
        "enabled": True,
        "target_chunks_per_dialect": target,
        "minimum_total_chunks": minimum_total,
        "available_by_dialect": {dialect: len(by_dialect[dialect]) for dialect in ALLOWED_DIALECTS},
        "selected_by_dialect": {
            dialect: sum(row["dialect"] == dialect for row in selected) for dialect in ALLOWED_DIALECTS
        },
        "selection_method": "deterministic hierarchical round-robin: topics first, then source/source type/version within each topic",
    }


def chunk_all(root: str | Path = ".", config_path: str | Path = "config/chunking.yaml") -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_yaml(root / config_path)
    documents = list(iter_jsonl(root / "data" / "interim" / "deduplicated_documents.jsonl"))
    structure = [chunk for document in documents for chunk in structure_chunks(document, config["structure_aware"])]
    structure_count = len(structure)
    deduped, chunk_duplicates = deduplicate_records(
        structure,
        lambda row: row["text"],
        "chunk_id",
        float(config.get("deduplication", {}).get("near_duplicate_threshold", 0.94)),
        int(config.get("deduplication", {}).get("minimum_near_duplicate_words", 50)),
    )
    del structure
    selected, balance_report = select_balanced(deduped, config.get("balancing", {}))
    deduped_count = len(deduped)
    del deduped
    gc.collect()
    write_jsonl_atomic(root / "data" / "processed" / "corpus.jsonl", selected)

    fixed_count = 0
    if config.get("fixed_size_baseline", {}).get("enabled", True):
        fixed = [chunk for document in documents for chunk in fixed_chunks(document, config["fixed_size_baseline"])]
        fixed_deduped, _ = deduplicate_records(
            fixed,
            lambda row: row["text"],
            "chunk_id",
            float(config.get("deduplication", {}).get("near_duplicate_threshold", 0.94)),
            int(config.get("deduplication", {}).get("minimum_near_duplicate_words", 50)),
        )
        fixed_selected, _ = select_balanced(fixed_deduped, config.get("balancing", {}))
        fixed_count = write_jsonl_atomic(root / "data" / "processed" / "corpus_fixed.jsonl", fixed_selected)

    write_json_atomic(root / "reports" / "chunk_duplicate_report.json", chunk_duplicates)
    report = {
        "document_count": len(documents),
        "candidate_structure_chunk_count": structure_count,
        "deduplicated_structure_chunk_count": deduped_count,
        "final_chunk_count": len(selected),
        "fixed_baseline_chunk_count": fixed_count,
        "exact_duplicates_removed": chunk_duplicates["exact_duplicate_count"],
        "near_duplicates_removed": chunk_duplicates["near_duplicate_count"],
        "balancing": balance_report,
    }
    write_json_atomic(root / "reports" / "chunking_report.json", report)
    return report
