from __future__ import annotations

import math

import pytest

from sqlmend_retrieval_v1.evaluation import (
    EVALUATION_LABEL,
    METRIC_NAMES,
    build_query_slices,
    evaluate_query,
    evaluate_system,
)
from sqlmend_retrieval_v1.models import CandidatePassage, OnlineQuery, RunEntry
from sqlmend_retrieval_v1.pool import (
    FORMAL_SYSTEM_IDS,
    PoolAuditError,
    audit_pool,
    pool_expansion_artifact_data,
)


def _candidate(
    chunk_id: str,
    *,
    dialect: str = "mysql",
    version_status: str = "general",
    text: str = "General SQL diagnostic guidance.",
    version: str | None = None,
    version_min: str | None = None,
    version_max: str | None = None,
    source_type: str = "documentation",
) -> CandidatePassage:
    return CandidatePassage(
        chunk_id=chunk_id,
        dialect=dialect,
        version=version,
        version_min=version_min,
        version_max=version_max,
        version_status=version_status,
        source_type=source_type,
        title=f"Title {chunk_id}",
        section="Diagnostics",
        text=text,
        baseline_rank=1,
        baseline_score=1.0,
    )


def _rows(query_id: str, chunk_ids: list[str], tag: str = "test") -> list[RunEntry]:
    return [
        RunEntry(query_id, chunk_id, rank, 1.0 / rank, tag)
        for rank, chunk_id in enumerate(chunk_ids, start=1)
    ]


def test_evaluate_query_preserves_unjudged_and_classifies_compatibility() -> None:
    query = OnlineQuery("q1", "mysql", "8.0", "safe serialized query")
    corpus = {
        "maria": _candidate("maria", dialect="mariadb"),
        "new9": _candidate(
            "new9",
            text="This feature is introduced in version 9.0",
        ),
        "unknown": _candidate("unknown", version_status="unknown"),
        "current": _candidate("current", version_status="current"),
        "pg_unknown": _candidate(
            "pg_unknown", dialect="postgresql", version_status="unknown"
        ),
        "direct": _candidate("direct"),
        "outside": _candidate("outside"),
    }
    ranking = _rows(
        "q1", ["maria", "new9", "unknown", "current", "pg_unknown", "direct"]
    )
    # new9 has no qrel: it is unjudged, not relevance zero.
    qrels = {
        "maria": 0,
        "unknown": 1,
        "current": 0,
        "pg_unknown": 0,
        "direct": 2,
        "outside": 2,
    }

    metrics = evaluate_query(ranking, qrels, query, corpus)

    observed_dcg = 1.0 / math.log2(4.0) + 3.0 / math.log2(7.0)
    ideal_dcg = 3.0 + 3.0 / math.log2(3.0) + 1.0 / math.log2(4.0)
    assert tuple(metrics) == METRIC_NAMES
    assert metrics["graded_nDCG@10"] == pytest.approx(observed_dcg / ideal_dcg)
    assert metrics["MRR@10_rel2"] == pytest.approx(1.0 / 6.0)
    assert metrics["pooled_Recall@10_rel2"] == 0.5
    assert metrics["HitRate@5_rel2"] == 0.0
    assert metrics["Judged@5"] == 4 / 5
    assert metrics["Judged@10"] == 5 / 10
    assert metrics["Judged@20"] == 5 / 20
    assert metrics["Judged@30"] == 5 / 30
    # mysql/mariadb is related for ranking but still an explicit wrong dialect.
    assert metrics["Wrong-Dialect@5"] == 2 / 5
    assert metrics["Wrong-Version@5"] == 1 / 5
    assert metrics["Unknown-Version@5"] == 1 / 5
    assert metrics["Unresolved-Current@5"] == 1 / 5

    explicitly_zero = evaluate_query(ranking, {**qrels, "new9": 0}, query, corpus)
    assert explicitly_zero["graded_nDCG@10"] == metrics["graded_nDCG@10"]
    assert explicitly_zero["Judged@5"] == 1.0


def test_system_evaluation_has_required_offline_slices() -> None:
    queries = [
        OnlineQuery("q1", "mysql", "8.0", "safe one"),
        OnlineQuery("q2", "postgresql", "16", "safe two"),
    ]
    corpus = {
        "d1": _candidate("d1", dialect="mysql"),
        "d2": _candidate("d2", dialect="postgresql"),
    }
    run = _rows("q1", ["d1"], "sys") + _rows("q2", ["d2"], "sys")
    qrels = {"q1": {"d1": 2}, "q2": {"d2": 2}}
    cases = [
        {
            "query_id": "q1",
            # This conflicting raw field must not determine the dialect slice.
            "dialect": "sqlite",
            "case_flags": {
                "requires_dialect_reasoning": True,
                "requires_version_reasoning": True,
            },
            "reference_fix": "offline-only and ignored",
        },
        {
            "query_id": "q2",
            "dialect": "sqlite",
            "case_flags": {
                "requires_dialect_reasoning": False,
                "requires_version_reasoning": False,
            },
        },
    ]

    result = evaluate_system(
        run,
        qrels,
        queries,
        corpus,
        cases,
        system_id="sys",
    )

    assert result["evaluation_label"] == EVALUATION_LABEL
    assert result["query_count"] == 2
    assert result["overall"]["graded_nDCG@10"] == 1.0
    rows = {(row["slice_name"], row["slice_value"]): row for row in result["slices"]}
    assert rows[("dialect", "mysql")]["query_count"] == 1
    assert rows[("dialect", "postgresql")]["query_count"] == 1
    assert rows[("dialect", "sqlite")]["query_count"] == 0
    assert rows[("dialect", "sqlite")]["graded_nDCG@10"] is None
    assert rows[("case_flag", "dialect-sensitive")]["query_count"] == 1
    assert rows[("case_flag", "version-sensitive")]["query_count"] == 1


def test_case_flags_must_be_explicit_booleans_and_are_not_in_online_query() -> None:
    query = OnlineQuery("q1", "mysql", "8.0", "safe")
    assert not hasattr(query, "case_flags")
    with pytest.raises(ValueError, match="requires_version_reasoning"):
        build_query_slices(
            [query],
            [
                {
                    "query_id": "q1",
                    "case_flags": {"requires_dialect_reasoning": True},
                }
            ],
        )


def _pool_fixture() -> tuple[
    tuple[str, ...],
    dict[str, list[RunEntry]],
    dict[str, dict[str, int]],
    dict[str, CandidatePassage],
]:
    system_ids = tuple(f"system_{index}" for index in range(5))
    chunk_ids = [f"d{index:02d}" for index in range(1, 31)]
    corpus = {chunk_id: _candidate(chunk_id) for chunk_id in chunk_ids}
    runs = {
        system_id: _rows("q1", chunk_ids, tag=f"tag_{index}")
        for index, system_id in enumerate(system_ids)
    }
    qrels = {
        "q1": {
            chunk_id: (2 if chunk_id == "d01" else 0)
            for chunk_id in chunk_ids
            if chunk_id != "d07"
        }
    }
    return system_ids, runs, qrels, corpus


def test_default_pool_system_ids_are_the_five_v1_comparators() -> None:
    assert FORMAL_SYSTEM_IDS == (
        "hybrid_rrf_frozen_control_v1",
        "hybrid_rrf_dialect_aware_v1",
        "hybrid_rrf_version_aware_v1",
        "hybrid_rrf_dialect_version_aware_v1",
        "hybrid_rrf_dialect_version_lexical_rerank_v1",
    )


def test_five_system_pool_audit_deduplicates_missing_pairs_without_labeling() -> None:
    system_ids, runs, qrels, corpus = _pool_fixture()

    result = audit_pool(runs, qrels, corpus, system_ids=system_ids)

    assert result["evaluation_integrity_status"] == "BLOCKED"
    assert result["pool_expansion_required"] is True
    assert result["unjudged_top30_occurrence_count"] == 5
    assert result["pool_expansion_record_count"] == 1
    assert result["overall"]["Judged@5"] == 1.0
    assert result["overall"]["Judged@10"] == 9 / 10
    assert result["overall"]["Judged@20"] == 19 / 20
    assert result["overall"]["Judged@30"] == 29 / 30
    record = result["pool_expansion_records"][0]
    assert record["query_id"] == "q1"
    assert record["chunk_id"] == "d07"
    assert record["retrieved_by"] == list(system_ids)
    assert record["ranks"] == {system_id: 7 for system_id in system_ids}
    assert record["relevance"] is None
    assert record["chunk_snapshot"]["version_status"] == "general"
    assert "baseline_rank" not in record["chunk_snapshot"]

    records, summary = pool_expansion_artifact_data(result)
    assert records == result["pool_expansion_records"]
    assert "pool_expansion_records" not in summary
    records[0]["relevance"] = 0
    assert result["pool_expansion_records"][0]["relevance"] is None


def test_pool_expansion_data_is_independent_of_input_iteration_order() -> None:
    system_ids, runs, qrels, corpus = _pool_fixture()
    expected = audit_pool(runs, qrels, corpus, system_ids=system_ids)
    reversed_runs = {
        system_id: list(reversed(runs[system_id])) for system_id in reversed(system_ids)
    }
    reversed_qrels = {"q1": dict(reversed(list(qrels["q1"].items())))}
    reversed_corpus = dict(reversed(list(corpus.items())))

    observed = audit_pool(
        reversed_runs,
        reversed_qrels,
        reversed_corpus,
        system_ids=system_ids,
    )
    assert observed == expected


def test_pool_passes_only_when_every_top30_pair_is_judged() -> None:
    system_ids, runs, qrels, corpus = _pool_fixture()
    qrels["q1"]["d07"] = 0
    result = audit_pool(runs, qrels, corpus, system_ids=system_ids)
    assert result["evaluation_integrity_status"] == "PASS"
    assert result["pool_expansion_required"] is False
    assert result["overall"]["Judged@30"] == 1.0
    assert result["pool_expansion_records"] == []


def test_pool_rejects_incomplete_formal_runs() -> None:
    system_ids, runs, qrels, corpus = _pool_fixture()
    runs[system_ids[0]] = runs[system_ids[0]][:-1]
    with pytest.raises(PoolAuditError, match="expected 30"):
        audit_pool(runs, qrels, corpus, system_ids=system_ids)
