from __future__ import annotations

import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .constants import ALLOWED_DIALECTS
from .manifest import load_source_manifest
from .metadata import contains_version_or_compatibility, version_scope
from .utils import iter_jsonl, read_json, word_count, word_tokens, write_json_atomic, write_jsonl_atomic


def _percentage(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 4) if denominator else 0.0


def _counter_rows(counter: Counter[Any], key_name: str) -> list[dict[str, Any]]:
    return [{key_name: str(key), "count": value} for key, value in sorted(counter.items(), key=lambda item: str(item[0]))]


def _safe_jsonl_count(path: Path) -> int:
    return sum(1 for _ in iter_jsonl(path)) if path.exists() else 0


def _load_raw_metadata(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    index_path = root / "data" / "raw" / "collection_index.jsonl"
    if index_path.exists():
        paths = [root / row["raw_path"] for row in iter_jsonl(index_path)]
    else:
        paths = [
            path
            for dialect in ALLOWED_DIALECTS
            for path in sorted((root / "data" / "raw" / dialect).glob("*.json"))
        ]
    for path in paths:
        try:
            raw = read_json(path, {})
            records.append(
                {
                    key: raw.get(key)
                    for key in (
                        "document_id",
                        "source_id",
                        "dialect",
                        "version",
                        "version_min",
                        "version_max",
                        "version_status",
                    )
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    return records


def calculate_statistics(
    chunks: list[dict[str, Any]],
    raw_documents: list[dict[str, Any]] | None = None,
    cleaned_document_count: int = 0,
    collection_report: dict[str, Any] | None = None,
    document_duplicate_report: dict[str, Any] | None = None,
    chunk_duplicate_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_documents = raw_documents or []
    collection_report = collection_report or {}
    document_duplicate_report = document_duplicate_report or {}
    chunk_duplicate_report = chunk_duplicate_report or {}
    sizes = [word_count(chunk.get("text", "")) for chunk in chunks]
    unique_words: set[str] = set()
    for chunk in chunks:
        unique_words.update(token.casefold() for token in word_tokens(chunk.get("text", "")))
    dialect_chunks = Counter(chunk.get("dialect") for chunk in chunks)
    dialect_documents: dict[str, set[str]] = defaultdict(set)
    for chunk in chunks:
        dialect_documents[chunk.get("dialect")].add(chunk.get("document_id"))
    source_types = Counter(chunk.get("source_type") for chunk in chunks)
    versions = Counter(
        (chunk.get("dialect"), version_scope(chunk), chunk.get("version_status")) for chunk in chunks
    )
    topics = Counter((chunk.get("dialect"), chunk.get("topic", "unknown")) for chunk in chunks)
    known_versions = sum(chunk.get("version_status") != "unknown" for chunk in chunks)
    known_dialects = sum(chunk.get("dialect") in ALLOWED_DIALECTS for chunk in chunks)
    sql_count = sum(bool(chunk.get("contains_sql")) for chunk in chunks)
    error_count = sum(bool(chunk.get("contains_error_code")) for chunk in chunks)
    compatibility_count = sum(
        bool(
            chunk["contains_version_or_compatibility"]
            if "contains_version_or_compatibility" in chunk
            else contains_version_or_compatibility(chunk.get("text", ""))
        )
        for chunk in chunks
    )
    exact_removed = int(document_duplicate_report.get("exact_duplicate_count", 0)) + int(
        chunk_duplicate_report.get("exact_duplicate_count", 0)
    )
    near_removed = int(document_duplicate_report.get("near_duplicate_count", 0)) + int(
        chunk_duplicate_report.get("near_duplicate_count", 0)
    )
    return {
        "raw_document_count": len(raw_documents),
        "cleaned_document_count": cleaned_document_count,
        "final_chunk_count": len(chunks),
        "total_word_count": sum(sizes),
        "approximate_unique_word_count": len(unique_words),
        "average_chunk_word_count": round(statistics.fmean(sizes), 4) if sizes else 0.0,
        "median_chunk_word_count": round(statistics.median(sizes), 4) if sizes else 0.0,
        "minimum_chunk_word_count": min(sizes) if sizes else 0,
        "maximum_chunk_word_count": max(sizes) if sizes else 0,
        "chunks_per_dialect": {dialect: dialect_chunks.get(dialect, 0) for dialect in ALLOWED_DIALECTS},
        "documents_per_dialect": {dialect: len(dialect_documents[dialect]) for dialect in ALLOWED_DIALECTS},
        "chunks_per_source_type": dict(sorted(source_types.items(), key=lambda item: str(item[0]))),
        "chunks_per_version": [
            {"dialect": dialect, "version_or_range": version, "version_status": status, "chunks": count}
            for (dialect, version, status), count in sorted(versions.items())
        ],
        "chunks_per_topic": [
            {"dialect": dialect, "topic": topic, "chunks": count}
            for (dialect, topic), count in sorted(topics.items())
        ],
        "version_known_percentage": _percentage(known_versions, len(chunks)),
        "dialect_known_percentage": _percentage(known_dialects, len(chunks)),
        "exact_duplicate_count_removed": exact_removed,
        "near_duplicate_count_removed": near_removed,
        "sql_chunk_count": sql_count,
        "sql_chunk_percentage": _percentage(sql_count, len(chunks)),
        "error_chunk_count": error_count,
        "error_chunk_percentage": _percentage(error_count, len(chunks)),
        "version_or_compatibility_chunk_count": compatibility_count,
        "version_or_compatibility_chunk_percentage": _percentage(compatibility_count, len(chunks)),
        "failed_source_count": int(collection_report.get("failed_source_count", 0)),
        "failed_url_count": int(collection_report.get("failed_url_count", 0)),
        "inaccessible_source_count": int(collection_report.get("inaccessible_source_count", 0)),
    }


def _write_source_coverage(
    path: Path,
    manifest: dict[str, Any],
    raw_documents: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    collection_report: dict[str, Any],
) -> None:
    raw_counts = Counter(row.get("source_id") for row in raw_documents)
    chunk_counts = Counter(row.get("source_id") for row in chunks)
    document_sets: dict[str, set[str]] = defaultdict(set)
    for row in chunks:
        document_sets[row.get("source_id")].add(row.get("document_id"))
    failure_counts = Counter(row.get("source_id") for row in collection_report.get("failures", []))
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source_id",
        "source_name",
        "dialect",
        "source_type",
        "authority_class",
        "version",
        "version_min",
        "version_max",
        "version_status",
        "base_url",
        "raw_documents",
        "final_documents",
        "final_chunks",
        "failed_urls",
        "license_or_terms_note",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for source in manifest["sources"]:
            source_id = source["id"]
            writer.writerow(
                {
                    "source_id": source_id,
                    "source_name": source["source_name"],
                    "dialect": source["dialect"],
                    "source_type": source["source_type"],
                    "authority_class": source["authority_class"],
                    "version": source.get("version"),
                    "version_min": source.get("version_min"),
                    "version_max": source.get("version_max"),
                    "version_status": source.get("version_status", "unknown"),
                    "base_url": source["base_url"],
                    "raw_documents": raw_counts[source_id],
                    "final_documents": len(document_sets[source_id]),
                    "final_chunks": chunk_counts[source_id],
                    "failed_urls": failure_counts[source_id],
                    "license_or_terms_note": source["license_or_terms_note"],
                }
            )


def _write_version_coverage(path: Path, chunks: list[dict[str, Any]]) -> None:
    groups: Counter[tuple[str, str, str, str, str]] = Counter()
    documents: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    for chunk in chunks:
        key = (
            chunk["dialect"],
            str(chunk.get("version") or ""),
            str(chunk.get("version_min") or ""),
            str(chunk.get("version_max") or ""),
            chunk.get("version_status", "unknown"),
        )
        groups[key] += 1
        documents[key].add(chunk["document_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["dialect", "version", "version_min", "version_max", "version_status", "documents", "chunks"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key, count in sorted(groups.items()):
            writer.writerow(dict(zip(fields[:5], key)) | {"documents": len(documents[key]), "chunks": count})


def _markdown_report(stats: dict[str, Any]) -> str:
    lines = [
        "#SQLMendRAG corpus statistics",
        "",
        "This file is automatically generated by the pipeline; the number is based on `corpus_statistics.json` in the same directory.",
        "",
        "##Overview",
        "",
        "| indicator | value |",
        "|---|---:|",
        f"| Original document | {stats['raw_document_count']:,} |",
        f"| Cleaned document | {stats['cleaned_document_count']:,} |",
        f"| final chunks | {stats['final_chunk_count']:,} |",
        f"| Total word count | {stats['total_word_count']:,} |",
        f"| Approximate unique word count | {stats['approximate_unique_word_count']:,} |",
        f"| Average chunk word count | {stats['average_chunk_word_count']:.2f} |",
        f"| Median chunk word count | {stats['median_chunk_word_count']:.2f} |",
        f"| Shortest / Longest | {stats['minimum_chunk_word_count']} / {stats['maximum_chunk_word_count']} |",
        f"| Version information is known | {stats['version_known_percentage']:.2f}% |",
        f"| SQL related chunk | {stats['sql_chunk_percentage']:.2f}% |",
        f"| Error information related chunk | {stats['error_chunk_percentage']:.2f}% |",
        f"| Version/compatibility related chunk | {stats['version_or_compatibility_chunk_percentage']:.2f}% |",
        "",
        "## Dialect distribution",
        "",
        "| dialect | document | chunks | proportion |",
        "|---|---:|---:|---:|",
    ]
    total = stats["final_chunk_count"]
    for dialect in ALLOWED_DIALECTS:
        count = stats["chunks_per_dialect"][dialect]
        lines.append(
            f"| {dialect} | {stats['documents_per_dialect'][dialect]:,} | {count:,} | {_percentage(count, total):.2f}% |"
        )
    lines.extend(
        [
            "",
            "## Deduplication and collection exceptions",
            "",
            f"- Remove exact duplicates {stats['exact_duplicate_count_removed']:,} at document level and chunk level.",
            f"- Remove nearly duplicate {stats['near_duplicate_count_removed']:,} items in total.",
            f"- failed sources {stats['failed_source_count']}, failed URLs {stats['failed_url_count']}, of which {stats['inaccessible_source_count']} were inaccessible sources.",
            "",
            "For detailed source and version distribution, see `source_coverage.csv` and `version_coverage.csv`.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_inspection_sample(chunks: list[dict[str, Any]], path: Path, size: int = 100, seed: int = 20260827) -> dict[str, Any]:
    randomizer = random.Random(seed)
    by_dialect: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        by_dialect[chunk["dialect"]].append(chunk)
    sample: list[dict[str, Any]] = []
    per_dialect = size // len(ALLOWED_DIALECTS)
    for dialect in ALLOWED_DIALECTS:
        candidates = sorted(by_dialect[dialect], key=lambda row: row["chunk_id"])
        sample.extend(randomizer.sample(candidates, min(per_dialect, len(candidates))))
    if len(sample) < min(size, len(chunks)):
        selected_ids = {row["chunk_id"] for row in sample}
        remaining = [row for row in chunks if row["chunk_id"] not in selected_ids]
        sample.extend(randomizer.sample(remaining, min(size - len(sample), len(remaining))))
    randomizer.shuffle(sample)
    rows = [
        {
            "sample_index": index + 1,
            "chunk_id": chunk["chunk_id"],
            "dialect": chunk["dialect"],
            "version": chunk.get("version"),
            "source_url": chunk["source_url"],
            "section": chunk.get("section"),
            "contains_sql": chunk.get("contains_sql"),
            "contains_error_code": chunk.get("contains_error_code"),
            "text": chunk["text"],
        }
        for index, chunk in enumerate(sample)
    ]
    write_jsonl_atomic(path, rows)
    return {"sample_size": len(rows), "seed": seed, "per_dialect_target": per_dialect}


def generate_statistics(root: str | Path = ".") -> dict[str, Any]:
    root = Path(root).resolve()
    chunks = list(iter_jsonl(root / "data" / "processed" / "corpus.jsonl"))
    raw_documents = _load_raw_metadata(root)
    cleaned_count = _safe_jsonl_count(root / "data" / "interim" / "cleaned_documents.jsonl")
    collection_report = read_json(root / "reports" / "collection_report.json", {})
    document_duplicates = read_json(root / "reports" / "document_duplicate_report.json", {})
    chunk_duplicates = read_json(root / "reports" / "chunk_duplicate_report.json", {})
    stats = calculate_statistics(
        chunks,
        raw_documents,
        cleaned_count,
        collection_report,
        document_duplicates,
        chunk_duplicates,
    )
    inspection = generate_inspection_sample(chunks, root / "reports" / "inspection_sample.jsonl")
    stats["inspection_sample"] = inspection
    write_json_atomic(root / "reports" / "corpus_statistics.json", stats)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "corpus_statistics.md").write_text(_markdown_report(stats), encoding="utf-8", newline="\n")
    manifest = load_source_manifest(root / "config" / "sources.yaml")
    _write_source_coverage(root / "reports" / "source_coverage.csv", manifest, raw_documents, chunks, collection_report)
    _write_version_coverage(root / "reports" / "version_coverage.csv", chunks)
    return stats
