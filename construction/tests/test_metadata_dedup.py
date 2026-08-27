from __future__ import annotations

from sqlmend_pipeline.dedup import deduplicate_records, jaccard_similarity, shingles
from sqlmend_pipeline.metadata import (
    contains_error_code,
    contains_sql,
    infer_explicit_release_version,
    normalize_dialect,
    parse_version_label,
)


def record(identifier: str, text: str, dialect: str = "postgresql", version: str = "18.6") -> dict:
    return {
        "document_id": identifier,
        "dialect": dialect,
        "version": version,
        "version_min": version,
        "version_max": version,
        "version_status": "exact",
        "text": text,
    }


def test_dialect_normalization_enforces_five_value_vocabulary() -> None:
    assert normalize_dialect("Postgres") == "postgresql"
    assert normalize_dialect("MySQL Community Edition") == "mysql"
    assert normalize_dialect("SQLite3") == "sqlite"


def test_dialect_normalization_rejects_disallowed_database() -> None:
    import pytest

    with pytest.raises(ValueError):
        normalize_dialect("oracle")


def test_version_parser_exact_range_family_current_and_unknown() -> None:
    assert parse_version_label("8.4.11")["version_status"] == "exact"
    assert parse_version_label("10.6-10.11")["version_status"] == "range"
    assert parse_version_label("8.0.x")["version_max"] == "8.0.x"
    assert parse_version_label("current")["version_status"] == "current"
    assert parse_version_label(None)["version_status"] == "unknown"


def test_release_version_inference_requires_explicit_context() -> None:
    assert infer_explicit_release_version("Release 3_53_4 notes")["version"] == "3.53.4"
    assert infer_explicit_release_version("The value 3.53.4 is an example") is None


def test_sql_and_dialect_error_detection() -> None:
    assert contains_sql("SELECT * FROM t WHERE id = 1")
    assert contains_error_code("SQLSTATE 23505 unique_violation", "postgresql")
    assert contains_error_code("SQLITE_CONSTRAINT_UNIQUE", "sqlite")
    assert contains_error_code("Binder Error: no matching function", "duckdb")


def test_exact_deduplication_uses_normalized_hash() -> None:
    rows = [record("a", "SELECT  *  FROM t;"), record("b", "select * from t;")]
    kept, report = deduplicate_records(rows, lambda row: row["text"], "document_id")
    assert [row["document_id"] for row in kept] == ["a"]
    assert report["exact_duplicate_count"] == 1


def test_near_duplicate_jaccard_behavior_is_explainable() -> None:
    base = " ".join(f"token{i}" for i in range(80))
    changed = base.replace("token40", "replacement")
    assert jaccard_similarity(shingles(base), shingles(changed)) > 0.80
    kept, report = deduplicate_records(
        [record("a", base), record("b", changed)],
        lambda row: row["text"],
        "document_id",
        near_threshold=0.80,
    )
    assert len(kept) == 1
    assert report["near_duplicate_count"] == 1


def test_dedup_preserves_cross_dialect_and_cross_version_records() -> None:
    text = " ".join(f"word{i}" for i in range(70))
    rows = [
        record("pg18", text, "postgresql", "18.6"),
        record("pg14", text, "postgresql", "14.24"),
        record("mysql", text, "mysql", "8.4.11"),
    ]
    kept, report = deduplicate_records(rows, lambda row: row["text"], "document_id")
    assert len(kept) == 3
    assert report["exact_duplicate_count"] == 0


def test_distinct_error_symbols_are_not_near_deduplicated() -> None:
    shared = " ".join("same explanation for the SQL operation" for _ in range(20))
    rows = [record("a", f"ER_FIRST {shared}"), record("b", f"ER_SECOND {shared}")]
    kept, _ = deduplicate_records(rows, lambda row: row["text"], "document_id", near_threshold=0.5)
    assert len(kept) == 2

