from __future__ import annotations

import pytest

from sqlmend_pipeline.metadata import (
    classify_topic,
    contains_error_code,
    contains_sql,
    contains_version_or_compatibility,
    enrich_document,
    infer_explicit_release_version,
    normalize_dialect,
    parse_version_label,
    version_scope,
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Postgres", "postgresql"),
        (" PGSQL ", "postgresql"),
        ("MySQL Community Edition", "mysql"),
        ("SQLite3", "sqlite"),
        ("Maria DB", "mariadb"),
        ("DuckDB", "duckdb"),
    ],
)
def test_dialect_aliases_are_normalized(label: str, expected: str) -> None:
    assert normalize_dialect(label) == expected


def test_unknown_dialect_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported dialect"):
        normalize_dialect("SQL Server")


@pytest.mark.parametrize(
    ("label", "status", "version_min", "version_max"),
    [
        (None, "unknown", None, None),
        ("latest", "current", None, None),
        ("v16.4", "exact", "16.4", "16.4"),
        ("3.45.2", "exact", "3.45.2", "3.45.2"),
        ("11.4.x", "range", "11.4.0", "11.4.x"),
        ("18", "range", "18.0", "18.x"),
        ("8.0-8.4", "range", "8.0", "8.4"),
        ("8.0 to 8.4", "range", "8.0", "8.4"),
        ("8.0–8.4", "range", "8.0", "8.4"),
    ],
)
def test_version_labels_are_parsed_without_inventing_precision(
    label: str | None, status: str, version_min: str | None, version_max: str | None
) -> None:
    parsed = parse_version_label(label)

    assert parsed["version_status"] == status
    assert parsed["version_min"] == version_min
    assert parsed["version_max"] == version_max


def test_unrecognized_version_label_stays_unknown() -> None:
    assert parse_version_label("rolling snapshot") == {
        "version": "rolling snapshot",
        "version_min": None,
        "version_max": None,
        "version_status": "unknown",
    }


def test_release_version_inference_requires_release_context() -> None:
    assert infer_explicit_release_version("MariaDB release notes 11_4_5") == {
        "version": "11.4.5",
        "version_min": "11.4.5",
        "version_max": "11.4.5",
        "version_status": "exact",
    }
    assert infer_explicit_release_version("The example adds 11.4 and 5.2") is None


def test_release_version_inference_ignores_docbook_chapter_numbers() -> None:
    inferred = infer_explicit_release_version(
        "E.25. Release 14 > E.25. Release 14 > E.25.2. Migration to Version 14"
    )

    assert inferred == {
        "version": "14",
        "version_min": "14.0",
        "version_max": "14.x",
        "version_status": "range",
    }


def test_release_version_inference_supports_version_before_label_and_source_path() -> None:
    assert infer_explicit_release_version("MariaDB 11.4.10 Release Notes")["version"] == "11.4.10"
    assert infer_explicit_release_version("release-notes/mariadb-11_4_5.md")["version"] == "11.4.5"
    assert infer_explicit_release_version("release-notes/community-server/10.11/overview.md")["version"] == "10.11"
    assert infer_explicit_release_version("MariaDB 10.0.15 Fusion-io Changelog")["version"] == "10.0.15"
    assert infer_explicit_release_version("Announcing DuckDB 1.4.0 LTS")["version"] == "1.4.0"
    assert infer_explicit_release_version("DuckDB 1.5.3: Not an Ordinary Patch Release")["version"] == "1.5.3"
    assert infer_explicit_release_version("MariaDB 5.1.44b Release Notes") is None


def test_enrichment_infers_only_explicit_release_note_version(document_factory) -> None:
    release = document_factory(
        dialect="Maria DB",
        source_type="release_notes",
        version=None,
        version_min=None,
        version_max=None,
        version_status="unknown",
        logical_source_path="release-notes/mariadb-11_4_5.md",
        title="MariaDB release 11.4.5",
    )
    generic = document_factory(
        version=None,
        version_min=None,
        version_max=None,
        version_status="unknown",
        logical_source_path="functions/math-11.4-example.md",
        title="Math examples",
    )

    enriched_release = enrich_document(release)
    enriched_generic = enrich_document(generic)

    assert enriched_release["dialect"] == "mariadb"
    assert enriched_release["version_status"] == "exact"
    assert enriched_release["version"] == "11.4.5"
    assert enriched_release["version_inference"]
    assert enriched_generic["version_status"] == "unknown"
    assert enriched_generic["version"] is None
    assert enriched_generic["version_inference"] is None


@pytest.mark.parametrize(
    ("dialect", "text"),
    [
        ("postgresql", "ERROR: duplicate key, SQLSTATE 23505"),
        ("mysql", "ER_DUP_ENTRY SQLSTATE 23000"),
        ("sqlite", "SQLITE_CONSTRAINT_UNIQUE is returned"),
        ("mariadb", "ERROR 1064 (42000) near SELECT"),
        ("duckdb", "Binder Error: Referenced column not found"),
    ],
)
def test_error_detection_is_dialect_aware(dialect: str, text: str) -> None:
    assert contains_error_code(text, dialect)


def test_sql_version_and_topic_classifiers() -> None:
    assert contains_sql("```sql\nSELECT * FROM t;\n```")
    assert contains_sql("SELECT * FROM t;")
    assert not contains_sql("Rows are read from a table where the predicate matches.")
    assert contains_error_code("Error symbol: OBSOLETE_ER_IB_MSG_689", "mysql")
    assert not contains_sql("This prose has no database statement keyword.")
    assert contains_version_or_compatibility("Removed in version 11.0; migrate before upgrading.")
    assert classify_topic("Window functions", "row_number is a function", "official_docs") == "functions"
    assert classify_topic("Anything", "Anything", "release_notes") == "release_notes"
    assert version_scope({"version_status": "range", "version_min": "8.0", "version_max": "8.4"}) == "8.0..8.4"
