from __future__ import annotations

from sqlmend_pipeline.dedup import (
    deduplicate_records,
    distinctive_error_tokens,
    jaccard_similarity,
    shingles,
)


def _record(identifier: str, dialect: str = "postgresql", version: str = "16") -> dict:
    return {
        "document_id": identifier,
        "dialect": dialect,
        "version": version,
        "version_min": version,
        "version_max": version,
        "version_status": "exact",
    }


def test_shingles_and_jaccard_are_explainable() -> None:
    left = shingles("select one two three four five six", size=3)
    right = shingles("select one two three four five seven", size=3)

    assert "select one two" in left
    assert 0 < jaccard_similarity(left, right) < 1
    assert jaccard_similarity(set(), set()) == 1.0


def test_exact_dedup_normalizes_case_and_whitespace_within_scope() -> None:
    records = [
        _record("first") | {"text": "SELECT  *\nFROM users;"},
        _record("second") | {"text": "  select *\nFROM users;  "},
    ]

    kept, report = deduplicate_records(records, lambda row: row["text"], "document_id")

    assert [row["document_id"] for row in kept] == ["first"]
    assert report["exact_duplicate_count"] == 1
    assert report["exact_examples"][0]["removed_id"] == "second"
    assert report["by_dialect_and_version"] == [
        {"dialect": "postgresql", "version_scope": "16", "kind": "exact", "removed": 1}
    ]


def test_near_duplicate_detection_removes_minor_prose_variant() -> None:
    shared = [f"token{i}" for i in range(100)]
    first = " ".join(shared)
    second_tokens = shared.copy()
    second_tokens[50] = "replacement"
    second = " ".join(second_tokens)
    records = [_record("first") | {"text": first}, _record("second") | {"text": second}]

    kept, report = deduplicate_records(
        records,
        lambda row: row["text"],
        "document_id",
        near_threshold=0.85,
        min_near_words=20,
    )

    assert [row["document_id"] for row in kept] == ["first"]
    assert report["near_duplicate_count"] == 1
    assert report["near_examples"][0]["method"].startswith("Jaccard similarity")


def test_exact_text_is_preserved_across_dialects_and_meaningful_versions() -> None:
    text = "SELECT value FROM records WHERE enabled = TRUE;"
    records = [
        _record("pg16", "postgresql", "16") | {"text": text},
        _record("mysql8", "mysql", "8.4") | {"text": text},
        _record("pg17", "postgresql", "17") | {"text": text},
    ]

    kept, report = deduplicate_records(records, lambda row: row["text"], "document_id")

    assert [row["document_id"] for row in kept] == ["pg16", "mysql8", "pg17"]
    assert report["exact_duplicate_count"] == 0
    assert "same dialect and version scope" in report["scope_policy"]


def test_distinct_error_codes_prevent_near_duplicate_removal() -> None:
    prefix = " ".join(f"word{i}" for i in range(80))
    records = [
        _record("unique") | {"text": f"{prefix} SQLSTATE 23505 duplicate key error"},
        _record("foreign") | {"text": f"{prefix} SQLSTATE 23503 foreign key error"},
    ]

    kept, report = deduplicate_records(
        records,
        lambda row: row["text"],
        "document_id",
        near_threshold=0.80,
        min_near_words=20,
    )

    assert len(kept) == 2
    assert report["near_duplicate_count"] == 0
    assert distinctive_error_tokens(records[0]["text"]) != distinctive_error_tokens(records[1]["text"])
