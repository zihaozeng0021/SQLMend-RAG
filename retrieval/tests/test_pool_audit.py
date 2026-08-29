from __future__ import annotations

import json

import pytest

from sqlmend_retrieval.pool_audit import (
    FORMAL_SYSTEM_IDS,
    PoolAuditError,
    audit_pool,
    write_pool_audit,
)
from sqlmend_retrieval.qrels import QrelEntry
from sqlmend_retrieval.trec import TrecRunEntry, TrecRunError


def _entry(system: str, chunk_id: str, rank: int, query_id: str = "DEV0001"):
    return TrecRunEntry(query_id, chunk_id, rank, float(31 - rank), f"{system}_v1")


def _fixture_with_one_unjudged_pair():
    bm25_ids = [f"bm{i:02d}" for i in range(1, 31)]
    dense_ids = [f"de{i:02d}" for i in range(1, 31)]
    hybrid_ids = [f"hy{i:02d}" for i in range(1, 31)]
    bm25_ids[7] = "shared_unjudged"
    hybrid_ids[12] = "shared_unjudged"

    ids = sorted(set(bm25_ids + dense_ids + hybrid_ids))
    corpus = [
        {
            "chunk_id": chunk_id,
            "dialect": "postgresql",
            "version": "16",
            "title": f"Title {chunk_id}",
            "section": "SELECT",
            "text": f"Corpus text for {chunk_id}",
            # Annotation/provenance-like values must never enter the snapshot.
            "relevance": 2,
            "source_url": "https://example.invalid/not-for-the-prompt",
        }
        for chunk_id in ids
    ]
    runs = {
        "bm25_formal": [
            _entry("bm25_formal", chunk_id, rank)
            for rank, chunk_id in enumerate(bm25_ids, start=1)
        ],
        "dense_formal": [
            _entry("dense_formal", chunk_id, rank)
            for rank, chunk_id in enumerate(dense_ids, start=1)
        ],
        "hybrid_rrf_formal": [
            _entry("hybrid_rrf_formal", chunk_id, rank)
            for rank, chunk_id in enumerate(hybrid_ids, start=1)
        ],
    }
    qrels = [
        QrelEntry("DEV0001", chunk_id, 0)
        for chunk_id in ids
        if chunk_id != "shared_unjudged"
    ]
    return runs, qrels, corpus


def test_audit_keeps_missing_qrel_unjudged_and_merges_system_evidence():
    runs, qrels, corpus = _fixture_with_one_unjudged_pair()
    result = audit_pool(runs, qrels, corpus)

    assert result["evaluation_integrity_status"] == "BLOCKED"
    assert result["pool_expansion_required"] is True
    assert result["pool_expansion_record_count"] == 1
    assert result["unjudged_top30_occurrence_count"] == 2

    bm25 = result["per_system"]["bm25_formal"]
    dense = result["per_system"]["dense_formal"]
    hybrid = result["per_system"]["hybrid_rrf_formal"]
    assert bm25["Judged@5"] == 1.0
    assert bm25["Judged@10"] == pytest.approx(9 / 10)
    assert bm25["Judged@20"] == pytest.approx(19 / 20)
    assert bm25["Judged@30"] == pytest.approx(29 / 30)
    assert all(dense[f"Judged@{cutoff}"] == 1.0 for cutoff in (5, 10, 20, 30))
    assert hybrid["Judged@10"] == 1.0
    assert hybrid["Judged@20"] == pytest.approx(19 / 20)
    assert result["overall"]["Judged@5"] == 1.0
    assert result["overall"]["Judged@10"] == pytest.approx(29 / 30)
    assert result["overall"]["Judged@20"] == pytest.approx(58 / 60)
    assert result["overall"]["Judged@30"] == pytest.approx(88 / 90)

    record = result["pool_expansion_records"][0]
    assert list(record) == [
        "query_id",
        "chunk_id",
        "retrieved_by",
        "ranks",
        "scores",
        "reason",
        "relevance",
        "judgment_status",
        "chunk_snapshot",
    ]
    assert record["query_id"] == "DEV0001"
    assert record["chunk_id"] == "shared_unjudged"
    assert record["retrieved_by"] == ["bm25_formal", "hybrid_rrf_formal"]
    assert record["ranks"] == {
        "bm25_formal": 8,
        "dense_formal": None,
        "hybrid_rrf_formal": 13,
    }
    assert record["scores"] == {
        "bm25_formal": 23.0,
        "dense_formal": None,
        "hybrid_rrf_formal": 18.0,
    }
    assert record["reason"] == "unjudged_in_formal_top30"
    assert record["relevance"] is None
    assert record["judgment_status"] == (
        "human_or_separate_machine_judgment_required"
    )
    assert record["chunk_snapshot"] == {
        "dialect": "postgresql",
        "version": "16",
        "title": "Title shared_unjudged",
        "section": "SELECT",
        "text": "Corpus text for shared_unjudged",
    }
    assert "source_url" not in record["chunk_snapshot"]
    assert "relevance" not in record["chunk_snapshot"]


def test_artifacts_are_byte_deterministic_and_summary_omits_prompt_records(tmp_path):
    runs, qrels, corpus = _fixture_with_one_unjudged_pair()
    first = tmp_path / "first"
    second = tmp_path / "second"

    result = write_pool_audit(first, runs, qrels, corpus)
    permuted_runs = {
        system: list(reversed(runs[system]))
        for system in reversed(FORMAL_SYSTEM_IDS)
    }
    write_pool_audit(second, permuted_runs, reversed(qrels), reversed(corpus))

    first_jsonl = first / "pool_expansion_required.jsonl"
    first_summary = first / "pool_expansion_summary.json"
    assert first_jsonl.read_bytes() == (
        second / "pool_expansion_required.jsonl"
    ).read_bytes()
    assert first_summary.read_bytes() == (
        second / "pool_expansion_summary.json"
    ).read_bytes()
    assert first_jsonl.read_bytes().endswith(b"\n")
    assert b"\r\n" not in first_jsonl.read_bytes()

    lines = first_jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == result["pool_expansion_records"][0]
    summary = json.loads(first_summary.read_text(encoding="utf-8"))
    assert "pool_expansion_records" not in summary
    assert summary["pool_expansion_record_count"] == 1
    assert summary["evaluation_integrity_status"] == "BLOCKED"
    assert summary["pool_expansion_required"] is True


def test_all_explicit_qrels_produce_pass_and_empty_expansion_file(tmp_path):
    runs, qrels, corpus = _fixture_with_one_unjudged_pair()
    qrels.append(QrelEntry("DEV0001", "shared_unjudged", 0))

    result = write_pool_audit(tmp_path, runs, qrels, corpus)

    assert result["evaluation_integrity_status"] == "PASS"
    assert result["pool_expansion_required"] is False
    assert result["pool_expansion_record_count"] == 0
    assert result["pool_expansion_records"] == []
    assert result["unjudged_top30_occurrence_count"] == 0
    for system in FORMAL_SYSTEM_IDS:
        assert result["per_system"][system]["Judged@30"] == 1.0
    assert result["overall"]["Judged@30"] == 1.0
    assert (tmp_path / "pool_expansion_required.jsonl").read_bytes() == b""
    summary = json.loads(
        (tmp_path / "pool_expansion_summary.json").read_text(encoding="utf-8")
    )
    assert summary["evaluation_integrity_status"] == "PASS"
    assert summary["pool_expansion_required"] is False


def test_formal_runs_must_have_complete_top30_and_shared_query_coverage():
    runs, qrels, corpus = _fixture_with_one_unjudged_pair()
    runs["bm25_formal"] = runs["bm25_formal"][:-1]
    with pytest.raises(TrecRunError, match="at least 30"):
        audit_pool(runs, qrels, corpus)

    runs, qrels, corpus = _fixture_with_one_unjudged_pair()
    runs["dense_formal"] = [
        TrecRunEntry("DEV9999", entry.chunk_id, entry.rank, entry.score, entry.run_tag)
        for entry in runs["dense_formal"]
    ]
    with pytest.raises(PoolAuditError, match="different query coverage"):
        audit_pool(runs, qrels, corpus)


def test_unknown_run_chunk_is_rejected_instead_of_losing_snapshot():
    runs, qrels, corpus = _fixture_with_one_unjudged_pair()
    original = runs["dense_formal"][0]
    runs["dense_formal"][0] = TrecRunEntry(
        original.query_id, "not_in_corpus", original.rank, original.score, original.run_tag
    )
    with pytest.raises(TrecRunError, match="unknown chunk_id"):
        audit_pool(runs, qrels, corpus)
