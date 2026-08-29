"""Frozen corpus loading, validation, and deterministic passage rendering."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .hashing import sha256_file, sha256_text

EXPECTED_CORPUS_SHA256 = "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3"
EXPECTED_CORPUS_RECORDS = 12_000
EXPECTED_CORPUS_WORDS = 1_663_145
EXPECTED_CORPUS_UNIQUE_WORDS = 35_646
ALLOWED_DIALECTS = frozenset({"postgresql", "mysql", "sqlite", "mariadb", "duckdb"})
PASSAGE_TEMPLATE_VERSION = "sqlmend-passage-v1"
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*|\d+(?:\.\d+)*|[\w]+", re.UNICODE)


class CorpusValidationError(ValueError):
    pass


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                raise CorpusValidationError(f"Blank JSONL line at {path}:{line_number}")
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise CorpusValidationError(f"Malformed JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise CorpusValidationError(f"Expected an object at {path}:{line_number}")
            records.append(value)
    return records


def validate_corpus(
    path: Path,
    *,
    expected_sha256: str = EXPECTED_CORPUS_SHA256,
    expected_records: int = EXPECTED_CORPUS_RECORDS,
    expected_words: int | None = None,
) -> dict[str, Any]:
    observed_hash = sha256_file(path)
    if expected_sha256 and observed_hash != expected_sha256:
        raise CorpusValidationError(
            f"Corpus SHA-256 mismatch: observed {observed_hash}, required {expected_sha256}"
        )
    records = load_jsonl(path)
    if expected_records and len(records) != expected_records:
        raise CorpusValidationError(
            f"Corpus record count is {len(records)}, required {expected_records}"
        )
    seen: set[str] = set()
    dialect_counts: Counter[str] = Counter()
    total_words = 0
    unique_words: set[str] = set()
    for index, record in enumerate(records, start=1):
        chunk_id = record.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise CorpusValidationError(f"Record {index} has no non-empty chunk_id")
        if chunk_id in seen:
            raise CorpusValidationError(f"Duplicate chunk_id: {chunk_id}")
        seen.add(chunk_id)
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CorpusValidationError(f"Chunk {chunk_id} has empty text")
        words = _WORD_RE.findall(text)
        total_words += len(words)
        unique_words.update(word.casefold() for word in words)
        dialect = record.get("dialect")
        if dialect not in ALLOWED_DIALECTS:
            raise CorpusValidationError(f"Chunk {chunk_id} has illegal dialect {dialect!r}")
        dialect_counts[dialect] += 1
    if expected_words is not None and total_words != expected_words:
        raise CorpusValidationError(
            f"Corpus word count is {total_words}, required {expected_words}"
        )
    ordered_ids = sorted(seen)
    return {
        "corpus_path": path.as_posix(),
        "corpus_sha256": observed_hash,
        "record_count": len(records),
        "unique_chunk_ids": len(seen),
        "dialect_counts": dict(sorted(dialect_counts.items())),
        "total_word_count": total_words,
        "approximate_unique_word_count": len(unique_words),
        "allowed_dialects": sorted(ALLOWED_DIALECTS),
        "chunk_order": "ascending_chunk_id",
        "chunk_order_sha256": sha256_text("\n".join(ordered_ids) + "\n"),
        "records": sorted(records, key=lambda item: item["chunk_id"]),
    }


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.strip() else None


def render_passage(record: dict[str, Any]) -> str:
    """Render only corpus-owned fields; no annotation metadata is accepted."""
    parts: list[str] = []
    title = _text(record.get("title"))
    section = _text(record.get("section"))
    body = _text(record.get("text"))
    if title is not None:
        parts.append(f"Title: {title}")
    if section is not None:
        parts.append(f"Section: {section}")
    if body is None:
        raise CorpusValidationError(f"Chunk {record.get('chunk_id')} has empty text")
    parts.append(f"Text:\n{body}")
    return "\n".join(parts)


def passages(records: Iterable[dict[str, Any]]) -> list[str]:
    return [render_passage(record) for record in records]
