"""Strict whitelist query discovery, serialization, and audit output."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .hashing import sha256_file, sha256_text
from .schemas import SerializedQuery

SERIALIZER_VERSION = "sqlmend-query-v1"
ALLOWED_SOURCE_FIELDS = frozenset(
    {
        "dialect",
        "version",
        "user_problem",
        "sql",
        "error_message",
        "error_code",
        "sqlstate",
        "error_symbol",
    }
)
FORBIDDEN_FIELDS = frozenset(
    {
        "reference_fix",
        "reference_fix_sql",
        "fixed_sql",
        "reference_answer",
        "reference_explanation",
        "gold_answer",
        "root_cause",
        "expected_root_cause",
        "expected_fix",
        "expected_behavior",
        "error_category",
        "primary_evidence",
        "primary_evidence_chunk_id",
        "evidence",
        "relevant_chunk_ids",
        "source_link",
        "generation_seed",
        "relevance",
        "qrels",
        "expected_result",
        "semantic_oracle",
        "verification",
        "verification_details",
        "judgment_origin",
        "schema_context",
        "setup_sql",
        "seed_data",
        "case_flags",
    }
)


class QueryValidationError(ValueError):
    pass


def load_queries(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                raise QueryValidationError(f"Blank line at {path}:{line_number}")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise QueryValidationError(f"Malformed JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise QueryValidationError(f"Expected object at {path}:{line_number}")
            query_id = record.get("query_id")
            if not isinstance(query_id, str) or not query_id:
                raise QueryValidationError(f"Missing query_id at {path}:{line_number}")
            if query_id in seen:
                raise QueryValidationError(f"Duplicate query_id: {query_id}")
            seen.add(query_id)
            result.append(record)
    return sorted(result, key=lambda item: item["query_id"])


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        rendered = str(value)
    else:
        return None
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    return rendered if rendered.strip() else None


def serialize_query(record: dict[str, Any]) -> SerializedQuery:
    query_id = record.get("query_id")
    if not isinstance(query_id, str) or not query_id:
        raise QueryValidationError("query_id must be a non-empty string")
    sections: list[str] = []
    used: list[str] = []

    dialect = _normalize(record.get("dialect"))
    if dialect is not None:
        sections.append(f"Dialect: {dialect}")
        used.append("dialect")
    version = _normalize(record.get("version"))
    if version is not None:
        sections.append(f"Version: {version}")
        used.append("version")
    question = _normalize(record.get("user_problem"))
    if question is not None:
        sections.append(f"Question:\n{question}")
        used.append("user_problem")

    observed_lines: list[str] = []
    for field, label in (
        ("error_message", "Error message"),
        ("error_code", "Error code"),
        ("sqlstate", "SQLSTATE"),
        ("error_symbol", "Error symbol"),
    ):
        value = _normalize(record.get(field))
        if value is not None:
            observed_lines.append(f"{label}: {value}")
            used.append(field)
    if observed_lines:
        sections.append("Observed error or behavior:\n" + "\n".join(observed_lines))

    sql = _normalize(record.get("sql"))
    if sql is not None:
        sections.append(f"SQL:\n{sql}")
        used.append("sql")
    if not sections:
        raise QueryValidationError(f"Query {query_id} has no whitelisted user fields")
    serialized = "\n\n".join(sections)
    return SerializedQuery(
        query_id=query_id,
        source_fields_used=tuple(used),
        serialized_text=serialized,
        serialized_text_sha256=sha256_text(serialized),
    )


def serialize_queries(records: Iterable[dict[str, Any]]) -> list[SerializedQuery]:
    return [serialize_query(record) for record in sorted(records, key=lambda item: item["query_id"])]


def write_serialized_queries(records: Iterable[SerializedQuery], path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda item: item.query_id)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in ordered:
            stream.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "path": path.as_posix(),
        "record_count": len(ordered),
        "sha256": sha256_file(path),
        "serializer_version": SERIALIZER_VERSION,
    }


def query_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    dialects = Counter(record.get("dialect") for record in records)
    dialect_sensitive = sum(
        bool(record.get("case_flags", {}).get("requires_dialect_reasoning")) for record in records
    )
    version_sensitive = sum(
        bool(record.get("case_flags", {}).get("requires_version_reasoning")) for record in records
    )
    return {
        "query_count": len(records),
        "dialect_counts": dict(sorted(dialects.items())),
        "dialect_sensitive_count": dialect_sensitive,
        "version_sensitive_count": version_sensitive,
    }

