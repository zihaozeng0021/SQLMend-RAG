from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .metadata import version_scope
from .utils import iter_jsonl, normalize_for_hash, word_tokens, write_json_atomic, write_jsonl_atomic

ERROR_TOKEN_RE = re.compile(
    r"\b(?:SQLSTATE\s*[=:]?\s*)?([0-9A-Z]{5})\b|\b(?:ER_|SQLITE_)[A-Z0-9_]+\b|\bERROR\s+\d{3,5}\b",
    re.I,
)


def shingles(text: str, size: int = 5) -> set[str]:
    tokens = [token.casefold() for token in word_tokens(text)]
    if len(tokens) < size:
        return set(tokens)
    return {" ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def hashed_shingles(text: str, size: int = 5) -> set[int]:
    """Memory-efficient 64-bit representation of normalized word shingles."""
    tokens = [token.casefold() for token in word_tokens(text)]
    values = tokens if len(tokens) < size else (
        " ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)
    )
    return {
        int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")
        for value in values
    }


def jaccard_similarity(left: set[Any], right: set[Any]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def simhash64(features: set[Any]) -> int:
    if not features:
        return 0
    vector = [0] * 64
    for feature in features:
        value = feature if isinstance(feature, int) else int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            vector[bit] += 1 if value & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(vector):
        if weight >= 0:
            result |= 1 << bit
    return result


def _bands(value: int, bands: int = 8) -> list[tuple[int, int]]:
    width = 64 // bands
    mask = (1 << width) - 1
    return [(index, (value >> (index * width)) & mask) for index in range(bands)]


def distinctive_error_tokens(text: str) -> set[str]:
    return {match.group(0).upper().replace(" ", "") for match in ERROR_TOKEN_RE.finditer(text)}


def dedup_scope(record: dict[str, Any]) -> tuple[str, str]:
    # Similar content in another SQL dialect or a meaningfully different version is
    # deliberately not merged. This boundary is part of the explainable policy.
    return str(record.get("dialect")), version_scope(record)


def deduplicate_records(
    records: Iterable[dict[str, Any]],
    text_getter: Callable[[dict[str, Any]], str],
    id_field: str,
    near_threshold: float = 0.94,
    min_near_words: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    kept_lengths: list[int] = []
    kept_shingles: list[set[int]] = []
    kept_errors: list[set[str]] = []
    exact_seen: dict[tuple[str, str, str], int] = {}
    buckets: dict[tuple[tuple[str, str], int, int], list[int]] = defaultdict(list)
    exact_removed: list[dict[str, Any]] = []
    near_removed: list[dict[str, Any]] = []
    by_dialect_version: Counter[tuple[str, str, str]] = Counter()

    for record in records:
        text = text_getter(record)
        scope = dedup_scope(record)
        normalized = normalize_for_hash(text)
        exact_key = (scope[0], scope[1], hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        if exact_key in exact_seen:
            duplicate_of = kept[exact_seen[exact_key]][id_field]
            exact_removed.append({"removed_id": record[id_field], "kept_id": duplicate_of, "scope": scope})
            by_dialect_version[(scope[0], scope[1], "exact")] += 1
            continue

        features = hashed_shingles(text)
        errors = distinctive_error_tokens(text)
        duplicate_index: int | None = None
        duplicate_similarity = 0.0
        if len(word_tokens(text)) >= min_near_words:
            fingerprint = simhash64(features)
            candidates: set[int] = set()
            for band_index, band_value in _bands(fingerprint):
                candidates.update(buckets.get((scope, band_index, band_value), []))
            for candidate_index in sorted(candidates):
                candidate_length = kept_lengths[candidate_index]
                length_ratio = min(len(normalized), candidate_length) / max(len(normalized), candidate_length, 1)
                if length_ratio < 0.80:
                    continue
                candidate_errors = kept_errors[candidate_index]
                if errors and candidate_errors and errors != candidate_errors:
                    continue
                similarity = jaccard_similarity(features, kept_shingles[candidate_index])
                if similarity >= near_threshold:
                    duplicate_index = candidate_index
                    duplicate_similarity = similarity
                    break
            if duplicate_index is not None:
                near_removed.append(
                    {
                        "removed_id": record[id_field],
                        "kept_id": kept[duplicate_index][id_field],
                        "scope": scope,
                        "similarity": round(duplicate_similarity, 6),
                        "method": "Jaccard similarity over normalized 5-word shingles; SimHash band candidates",
                    }
                )
                by_dialect_version[(scope[0], scope[1], "near")] += 1
                continue

        index = len(kept)
        kept.append(record)
        kept_lengths.append(len(normalized))
        kept_shingles.append(features)
        kept_errors.append(errors)
        exact_seen[exact_key] = index
        fingerprint = simhash64(features)
        for band_index, band_value in _bands(fingerprint):
            buckets[(scope, band_index, band_value)].append(index)

    breakdown = [
        {"dialect": dialect, "version_scope": version, "kind": kind, "removed": count}
        for (dialect, version, kind), count in sorted(by_dialect_version.items())
    ]
    report = {
        "input_count": len(kept) + len(exact_removed) + len(near_removed),
        "output_count": len(kept),
        "exact_duplicate_count": len(exact_removed),
        "near_duplicate_count": len(near_removed),
        "near_duplicate_threshold": near_threshold,
        "near_duplicate_method": "Jaccard similarity over normalized 5-word shingles, with deterministic 64-bit SimHash banding for candidates",
        "scope_policy": "Compare only within the same dialect and version scope; preserve cross-dialect and meaningfully versioned records",
        "by_dialect_and_version": breakdown,
        "exact_examples": exact_removed[:100],
        "near_examples": near_removed[:100],
    }
    return kept, report


def document_text(document: dict[str, Any]) -> str:
    return "\n".join(
        block["text"] for section in document.get("sections", []) for block in section.get("blocks", [])
    )


def deduplicate_documents(root: str | Path = ".", near_threshold: float = 0.94) -> dict[str, Any]:
    root = Path(root).resolve()
    documents = list(iter_jsonl(root / "data" / "interim" / "enriched_documents.jsonl"))
    kept, report = deduplicate_records(documents, document_text, "document_id", near_threshold)
    write_jsonl_atomic(root / "data" / "interim" / "deduplicated_documents.jsonl", kept)
    write_json_atomic(root / "reports" / "document_duplicate_report.json", report)
    return report


def find_residual_near_duplicates(
    records: list[dict[str, Any]],
    text_getter: Callable[[dict[str, Any]], str],
    threshold: float = 0.94,
) -> dict[str, Any]:
    _, report = deduplicate_records(
        records, text_getter, next((field for field in ("chunk_id", "document_id") if field in records[0]), "chunk_id"), threshold
    ) if records else ([], {"near_duplicate_count": 0, "exact_duplicate_count": 0, "input_count": 0})
    residual = report["near_duplicate_count"]
    total = report["input_count"]
    return {
        "estimated_pair_count": residual,
        "estimated_record_rate": residual / total if total else 0.0,
        "threshold": threshold,
        "method": report.get("near_duplicate_method"),
    }
