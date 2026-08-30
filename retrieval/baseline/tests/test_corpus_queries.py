from __future__ import annotations

import hashlib
import json

import pytest

from sqlmend_retrieval.corpus import CorpusValidationError, render_passage, validate_corpus
from sqlmend_retrieval.queries import FORBIDDEN_FIELDS, serialize_query


def _write_jsonl(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_corpus_validation_and_deterministic_order(tmp_path):
    path = tmp_path / "corpus.jsonl"
    records = [
        {"chunk_id": "b", "dialect": "mysql", "text": "body b"},
        {"chunk_id": "a", "dialect": "postgresql", "text": "body a"},
    ]
    digest = _write_jsonl(path, records)
    report = validate_corpus(path, expected_sha256=digest, expected_records=2)
    assert [record["chunk_id"] for record in report["records"]] == ["a", "b"]
    assert report["unique_chunk_ids"] == 2


def test_wrong_corpus_hash_fails(tmp_path):
    path = tmp_path / "corpus.jsonl"
    _write_jsonl(path, [{"chunk_id": "a", "dialect": "sqlite", "text": "x"}])
    with pytest.raises(CorpusValidationError, match="SHA-256 mismatch"):
        validate_corpus(path, expected_sha256="0" * 64, expected_records=1)


@pytest.mark.parametrize(
    "records,match",
    [
        ([{"chunk_id": "a", "dialect": "oracle", "text": "x"}], "illegal dialect"),
        (
            [
                {"chunk_id": "a", "dialect": "sqlite", "text": "x"},
                {"chunk_id": "a", "dialect": "sqlite", "text": "y"},
            ],
            "Duplicate chunk_id",
        ),
        ([{"chunk_id": "a", "dialect": "sqlite", "text": ""}], "empty text"),
    ],
)
def test_corpus_rejects_invalid_records(tmp_path, records, match):
    path = tmp_path / "corpus.jsonl"
    digest = _write_jsonl(path, records)
    with pytest.raises(CorpusValidationError, match=match):
        validate_corpus(path, expected_sha256=digest, expected_records=len(records))


def test_corpus_rejects_malformed_jsonl(tmp_path):
    path = tmp_path / "corpus.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(CorpusValidationError, match="Malformed JSON"):
        validate_corpus(path, expected_sha256=digest, expected_records=1)


def test_passage_uses_only_corpus_fields():
    record = {
        "chunk_id": "x",
        "title": "Title",
        "section": "Section",
        "text": "Body",
        "reference_fix_sql": "SECRET",
        "source_link": "SECRET-LINK",
    }
    rendered = render_passage(record)
    assert rendered == "Title: Title\nSection: Section\nText:\nBody"
    assert "SECRET" not in rendered


def _query():
    return {
        "query_id": "DEV0001",
        "dialect": "postgresql",
        "version": "15",
        "user_problem": "Why are duplicate rows returned?",
        "error_message": "SQLSTATE 42803",
        "sql": "SELECT a\r\nFROM t;",
        "expected_behavior": "gold behavior",
        "root_cause": "gold cause",
        "reference_fix_sql": "SELECT DISTINCT a FROM t",
        "evidence": [{"chunk_id": "secret"}],
    }


def test_query_serializer_whitelist_and_sql_fidelity():
    serialized = serialize_query(_query())
    assert serialized.source_fields_used == (
        "dialect",
        "version",
        "user_problem",
        "error_message",
        "sql",
    )
    assert "SELECT a\nFROM t;" in serialized.serialized_text
    assert "gold" not in serialized.serialized_text
    assert "secret" not in serialized.serialized_text
    assert "Unknown" not in serialized.serialized_text


def test_adding_gold_and_source_metadata_never_changes_serialization():
    base = {"query_id": "DEV0002", "user_problem": "How do I fix this?", "sql": "SELECT 1;"}
    expected = serialize_query(base)
    augmented = dict(base)
    for field in FORBIDDEN_FIELDS:
        augmented[field] = {"secret": field}
    actual = serialize_query(augmented)
    assert actual.serialized_text == expected.serialized_text
    assert actual.serialized_text_sha256 == expected.serialized_text_sha256


def test_missing_fields_omit_entire_sections():
    value = serialize_query({"query_id": "DEV0003", "user_problem": "A sufficiently clear question"})
    assert value.serialized_text == "Question:\nA sufficiently clear question"
    assert "Dialect:" not in value.serialized_text
    assert "SQL:" not in value.serialized_text

