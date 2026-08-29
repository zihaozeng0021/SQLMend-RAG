from __future__ import annotations

import json
import math

import pytest

from sqlmend_retrieval.qrels import (
    QrelEntry,
    QrelsError,
    convert_qrels_jsonl_to_trec,
    format_trec_qrels,
    load_qrels_jsonl,
    merge_supplemental_qrels,
    parse_trec_qrels,
)
from sqlmend_retrieval.trec import (
    TrecRunEntry,
    TrecRunError,
    format_trec_run,
    parse_trec_run,
    read_trec_run,
    write_trec_run,
)


def test_trec_run_round_trip_is_canonical_and_deterministic(tmp_path):
    entries = [
        TrecRunEntry("Q2", "c3", 1, -0.0, "formal_v1"),
        TrecRunEntry("Q1", "c2", 2, 1.25, "formal_v1"),
        TrecRunEntry("Q1", "c1", 1, 2.0, "formal_v1"),
    ]
    expected = (
        "Q1 Q0 c1 1 2.000000000000 formal_v1\n"
        "Q1 Q0 c2 2 1.250000000000 formal_v1\n"
        "Q2 Q0 c3 1 0.000000000000 formal_v1\n"
    )
    assert format_trec_run(entries) == expected

    first = tmp_path / "first.trec"
    second = tmp_path / "second.trec"
    write_trec_run(first, entries)
    write_trec_run(second, reversed(entries))
    assert first.read_bytes() == second.read_bytes() == expected.encode()
    assert read_trec_run(first) == [
        TrecRunEntry("Q1", "c1", 1, 2.0, "formal_v1"),
        TrecRunEntry("Q1", "c2", 2, 1.25, "formal_v1"),
        TrecRunEntry("Q2", "c3", 1, 0.0, "formal_v1"),
    ]


def test_parse_valid_trec_run_and_validate_known_chunks():
    text = (
        "DEV0001 Q0 c1 1 3.500000000000 bm25_v1\n"
        "DEV0001 Q0 c2 2 1.000000000000 bm25_v1\n"
    )
    entries = parse_trec_run(
        text,
        known_chunk_ids={"c1", "c2"},
        exact_results_per_query=2,
        expected_run_tag="bm25_v1",
    )
    assert [entry.chunk_id for entry in entries] == ["c1", "c2"]
    assert all(math.isfinite(entry.score) for entry in entries)


@pytest.mark.parametrize(
    "text, match",
    [
        (
            "Q Q0 c1 1 1.000000000000 tag\nQ Q0 c1 2 0.500000000000 tag\n",
            "duplicate query/chunk",
        ),
        ("Q Q0 missing 1 1.000000000000 tag\n", "unknown chunk_id"),
        ("Q Q0 c1 1 nan tag\n", "12-decimal"),
        ("Q Q0 c1 1 inf tag\n", "12-decimal"),
        ("Q Q0 c1 1 1.0 tag\n", "12-decimal"),
        ("Q Q0 c1 2 1.000000000000 tag\n", "continuous from 1"),
        (
            "Q Q0 c1 1 1.000000000000 tag\nQ Q0 c2 1 0.500000000000 tag\n",
            "duplicate rank",
        ),
    ],
)
def test_invalid_trec_runs_fail(text, match):
    known = {"c1", "c2"}
    with pytest.raises(TrecRunError, match=match):
        parse_trec_run(text, known_chunk_ids=known)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_writer_rejects_nonfinite_scores(score):
    with pytest.raises(TrecRunError, match="finite"):
        format_trec_run([TrecRunEntry("Q", "c", 1, score, "tag")])


def test_qrels_jsonl_conversion_retains_zero_one_and_two(tmp_path):
    source = tmp_path / "qrels.jsonl"
    source.write_text(
        '{"query_id":"Q2","chunk_id":"c0","relevance":0,"metadata":"ignored"}\n'
        '{"query_id":"Q1","chunk_id":"c2","relevance":2}\n'
        '{"query_id":"Q1","chunk_id":"c1","relevance":1}\n',
        encoding="utf-8",
    )
    source_before = source.read_bytes()
    destination = tmp_path / "qrels.trec"

    converted = convert_qrels_jsonl_to_trec(
        source,
        destination,
        known_chunk_ids={"c0", "c1", "c2"},
        require_all_labels=True,
    )

    assert source.read_bytes() == source_before
    assert {qrel.relevance for qrel in converted} == {0, 1, 2}
    assert destination.read_text(encoding="utf-8") == (
        "Q1 0 c1 1\nQ1 0 c2 2\nQ2 0 c0 0\n"
    )
    assert parse_trec_qrels(
        destination.read_text(encoding="utf-8"), require_all_labels=True
    ) == [
        QrelEntry("Q1", "c1", 1),
        QrelEntry("Q1", "c2", 2),
        QrelEntry("Q2", "c0", 0),
    ]


def test_qrels_duplicate_detection_in_jsonl_and_trec(tmp_path):
    source = tmp_path / "duplicates.jsonl"
    source.write_text(
        '{"query_id":"Q1","chunk_id":"c1","relevance":0}\n'
        '{"query_id":"Q1","chunk_id":"c1","relevance":2}\n',
        encoding="utf-8",
    )
    with pytest.raises(QrelsError, match="duplicate qrel"):
        load_qrels_jsonl(source)
    with pytest.raises(QrelsError, match="duplicate qrel"):
        parse_trec_qrels("Q1 0 c1 0\nQ1 0 c1 2\n")


def test_qrels_reject_invalid_label_unknown_chunk_and_missing_labels(tmp_path):
    source = tmp_path / "invalid.jsonl"
    source.write_text(
        '{"query_id":"Q1","chunk_id":"c1","relevance":3}\n', encoding="utf-8"
    )
    with pytest.raises(QrelsError, match="one of"):
        load_qrels_jsonl(source)
    with pytest.raises(QrelsError, match="unknown chunk_id"):
        parse_trec_qrels("Q1 0 missing 0\n", known_chunk_ids={"c1"})
    with pytest.raises(QrelsError, match="missing"):
        format_trec_qrels(
            [QrelEntry("Q1", "c1", 2)], require_all_labels=True
        )


def test_supplemental_qrels_merge_only_current_unjudged_run_pairs(tmp_path):
    base = [
        QrelEntry("Q1", "c1", 0),
        QrelEntry("Q1", "c2", 1),
        QrelEntry("Q1", "c3", 2),
    ]
    supplemental = tmp_path / "pool_expansion_judgments.jsonl"
    supplemental.write_text(
        '{"query_id":"Q1","chunk_id":"c4","relevance":2}\n',
        encoding="utf-8",
    )
    runs = {
        "bm25": [TrecRunEntry("Q1", "c4", 1, 1.0, "bm25")],
        "dense": [TrecRunEntry("Q1", "c3", 1, 1.0, "dense")],
    }

    merged, metadata = merge_supplemental_qrels(
        base, supplemental, runs, known_chunk_ids={"c1", "c2", "c3", "c4"}
    )

    assert base == [
        QrelEntry("Q1", "c1", 0),
        QrelEntry("Q1", "c2", 1),
        QrelEntry("Q1", "c3", 2),
    ]
    assert merged == [*base, QrelEntry("Q1", "c4", 2)]
    assert metadata["supplemental_qrel_count"] == 1
    assert metadata["effective_qrel_count"] == 4


@pytest.mark.parametrize(
    "record, match",
    [
        ({"query_id": "Q1", "chunk_id": "c1", "relevance": 2}, "conflict"),
        ({"query_id": "Q1", "chunk_id": "c5", "relevance": 2}, "outside"),
    ],
)
def test_supplemental_qrels_reject_conflicts_and_outside_pool(tmp_path, record, match):
    base = [
        QrelEntry("Q1", "c1", 0),
        QrelEntry("Q1", "c2", 1),
        QrelEntry("Q1", "c3", 2),
    ]
    supplemental = tmp_path / "pool_expansion_judgments.jsonl"
    supplemental.write_text(json.dumps(record) + "\n", encoding="utf-8")
    runs = {"bm25": [TrecRunEntry("Q1", "c4", 1, 1.0, "bm25")]}

    with pytest.raises(QrelsError, match=match):
        merge_supplemental_qrels(
            base,
            supplemental,
            runs,
            known_chunk_ids={"c1", "c2", "c3", "c4", "c5"},
        )
