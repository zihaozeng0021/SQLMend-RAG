from __future__ import annotations

import math

import pytest

from sqlmend_generation_v1.metrics import (
    NOT_APPLICABLE,
    aggregate_system_metrics,
    citation_validity,
    context_retrieval_metrics,
    latency_summary,
    paired_summary,
    percentile,
    task_success,
)


def test_percentile_and_latency_use_all_values() -> None:
    assert percentile([10, 20, 30, 40], 0.50) == 25.0
    assert percentile([10, 20, 30, 40], 0.95) == pytest.approx(38.5)
    summary = latency_summary([10, 20, 30, 40])
    assert summary == {
        "count": 4,
        "mean": 25.0,
        "p50": 25.0,
        "p95": pytest.approx(38.5),
    }
    with pytest.raises(ValueError, match="finite"):
        latency_summary([math.inf])


def test_task_success_requires_all_four_semantic_conditions() -> None:
    decision = {
        "root_cause_correct": True,
        "sql_repair_correct": True,
        "dialect_compatible": True,
        "version_compatible": True,
    }
    assert task_success(decision)
    decision["version_compatible"] = False
    assert not task_success(decision)


def test_citation_validity_only_admits_supplied_passage_ids() -> None:
    audit = citation_validity(
        {"citations": ["p1", "invented", {"passage_id": "p2"}]},
        ["p1", "p2"],
    )
    assert audit["score"] == pytest.approx(2 / 3)
    assert audit["invalid_passage_ids"] == ["invented"]
    # No citation is not an invented citation; coverage is evaluated elsewhere.
    assert citation_validity({"citations": []}, ["p1"])["score"] == 1.0


def test_context_metrics_use_qrels_rel_at_least_one() -> None:
    result = context_retrieval_metrics(
        ["p0", "p1", "p2"],
        {"p0": 0, "p1": 1, "p2": 2},
    )
    assert result["context_precision"] == pytest.approx(2 / 3)
    assert result["context_query_hit"] is True
    assert result["relevant_passage_ids"] == ["p1", "p2"]
    assert result["fully_judged"] is True

    missing = context_retrieval_metrics(["unknown"], {})
    assert missing["context_precision"] == 0.0
    assert missing["unjudged_passage_ids"] == ["unknown"]
    assert missing["fully_judged"] is False


def _row(success: bool, latency: float) -> dict[str, object]:
    return {
        "status": "success" if success else "failed",
        "task_success": success,
        "root_cause_correct": success,
        "sql_repair_correct": success,
        "dialect_compatible": success,
        "version_compatible": success,
        "structured_output_valid": success,
        "answer_relevance": 1.0 if success else 0.0,
        "latency_wall_ms": latency,
        "citation_validity": 1.0,
        "citation_coverage": 0.8,
        "faithfulness": 0.9,
        "context_precision": 0.6,
        "context_query_hit": success,
        "context_fully_judged": True,
    }


def test_aggregate_keeps_failures_in_denominator_and_g0_rag_is_na() -> None:
    g0 = aggregate_system_metrics([_row(True, 100), _row(False, 300)], rag_system=False)
    assert g0["formal_result_count"] == 2
    assert g0["failure_count"] == 1
    assert g0["success_count_semantics"] == "generation_contract_success"
    assert g0["generation_contract_success_count"] == 1
    assert g0["generation_contract_failure_count"] == 1
    assert g0["generation_contract_success_rate"] == 0.5
    assert g0["task_success_rate"] == 0.5
    assert g0["structured_output_validity"] == 0.5
    assert g0["latency_ms"]["mean"] == 200.0
    assert g0["faithfulness"] == NOT_APPLICABLE
    assert g0["context_precision"] == NOT_APPLICABLE

    g1 = aggregate_system_metrics([_row(True, 100), _row(False, 300)], rag_system=True)
    assert g1["citation_validity"] == 1.0
    assert g1["context_query_hit_rate"] == 0.5


def test_aggregate_separates_shape_contract_and_retry_recovery() -> None:
    recovered = _row(True, 100)
    recovered.update(
        {"generation_attempt_count": 2, "generation_retry_count": 1}
    )
    citation_contract_failure = _row(False, 300)
    citation_contract_failure.update(
        {
            "structured_output_valid": True,
            "generation_attempt_count": 3,
            "generation_retry_count": 2,
        }
    )

    metrics = aggregate_system_metrics(
        [recovered, citation_contract_failure], rag_system=True
    )
    assert metrics["structured_output_validity"] == 1.0
    assert metrics["generation_contract_success_rate"] == 0.5
    assert metrics["generation_attempt_count"] == 5
    assert metrics["generation_retry_count"] == 3
    assert metrics["generation_recovered_after_retry_count"] == 1


def test_paired_summary_counts_every_query() -> None:
    rows = [
        {"g0": {"task_success": False}, "g1": {"task_success": True}},
        {"g0": {"task_success": True}, "g1": {"task_success": False}},
        {"g0": {"task_success": True}, "g1": {"task_success": True}},
        {"g0": {"task_success": False}, "g1": {"task_success": False}},
    ]
    summary = paired_summary(rows)
    assert summary["query_count"] == 4
    assert summary["g1_improved_count"] == 1
    assert summary["g1_regressed_count"] == 1
    assert summary["paired_count_semantics"] == "offline_task_success"
    assert summary["both_task_success_count"] == 1
    assert summary["neither_task_success_count"] == 1
    assert summary["task_success_absolute_delta"] == 0.0
