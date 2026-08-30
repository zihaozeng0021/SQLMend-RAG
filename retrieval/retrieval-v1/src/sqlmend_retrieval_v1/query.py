"""The frozen safe query serializer and its online-only projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .io import sha256_text, write_jsonl
from .models import OnlineQuery


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
FORBIDDEN_ONLINE_FIELDS = frozenset(
    {
        "candidate_ranks",
        "case_flags",
        "error_category",
        "evidence",
        "expected_behavior",
        "expected_root_cause",
        "fixed_sql",
        "gold_answer",
        "primary_evidence_chunk_id",
        "qrels",
        "reference_answer",
        "reference_explanation",
        "reference_fix",
        "reference_fix_sql",
        "relevance",
        "root_cause",
        "schema_context",
        "seed_data",
        "setup_sql",
        "source_link",
        "verification",
    }
)


@dataclass(frozen=True, slots=True)
class SerializedQuery:
    query_id: str
    source_fields_used: tuple[str, ...]
    serialized_text: str
    serialized_text_sha256: str
    serializer_version: str = SERIALIZER_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_fields_used"] = list(self.source_fields_used)
        return value


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
        raise ValueError("query_id must be a non-empty string")
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

    observed: list[str] = []
    for field, label in (
        ("error_message", "Error message"),
        ("error_code", "Error code"),
        ("sqlstate", "SQLSTATE"),
        ("error_symbol", "Error symbol"),
    ):
        value = _normalize(record.get(field))
        if value is not None:
            observed.append(f"{label}: {value}")
            used.append(field)
    if observed:
        sections.append("Observed error or behavior:\n" + "\n".join(observed))

    sql = _normalize(record.get("sql"))
    if sql is not None:
        sections.append(f"SQL:\n{sql}")
        used.append("sql")
    if not sections:
        raise ValueError(f"Query {query_id} has no whitelisted user fields")
    serialized = "\n\n".join(sections)
    return SerializedQuery(
        query_id=query_id,
        source_fields_used=tuple(used),
        serialized_text=serialized,
        serialized_text_sha256=sha256_text(serialized),
    )


def project_online_queries(records: Iterable[dict[str, Any]]) -> list[OnlineQuery]:
    result: list[OnlineQuery] = []
    for record in sorted(records, key=lambda item: item["query_id"]):
        serialized = serialize_query(record)
        result.append(
            OnlineQuery(
                query_id=serialized.query_id,
                dialect=_normalize(record.get("dialect")),
                version=_normalize(record.get("version")),
                serialized_text=serialized.serialized_text,
                user_problem=_normalize(record.get("user_problem")),
                sql=_normalize(record.get("sql")),
                error_message=_normalize(record.get("error_message")),
                error_code=_normalize(record.get("error_code")),
                sqlstate=_normalize(record.get("sqlstate")),
                error_symbol=_normalize(record.get("error_symbol")),
            )
        )
    return result


def write_serialized_queries(records: Iterable[dict[str, Any]], path) -> None:
    serialized = [serialize_query(record).to_dict() for record in sorted(records, key=lambda item: item["query_id"])]
    write_jsonl(path, serialized)
