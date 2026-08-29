from __future__ import annotations

import math

import pytest

from sqlmend_retrieval.metrics import (
    REQUIRED_METRIC_NAMES,
    dcg_at_k,
    evaluate_query,
    evaluate_run,
    graded_ndcg_at_k,
    hit_rate_at_k,
    judged_at_k,
    pooled_recall_at_k,
    precision_at_k,
    ranked_relevances,
    reciprocal_rank_at_k,
)


def test_graded_dcg_and_ndcg_use_exponential_gain() -> None:
    expected_dcg = 3.0 + 1.0 / math.log2(3.0)
    assert dcg_at_k([2, 1, 0], 10) == pytest.approx(expected_dcg, abs=1e-12)
    assert graded_ndcg_at_k(["d2", "d1", "d0"], {"d2": 2, "d1": 1, "d0": 0}) == pytest.approx(
        1.0, abs=1e-12
    )

    actual = graded_ndcg_at_k(["d1", "d2", "d0"], {"d2": 2, "d1": 1, "d0": 0})
    expected = (1.0 + 3.0 / math.log2(3.0)) / expected_dcg
    assert actual == pytest.approx(expected, abs=1e-12)


def test_rank_one_and_rank_five_direct_evidence() -> None:
    judgments = {"direct": 2, "other_direct": 2, "support": 1}
    assert reciprocal_rank_at_k(["direct"], judgments, 10) == 1.0

    ranking = ["x1", "x2", "support", "x4", "direct"]
    assert reciprocal_rank_at_k(ranking, judgments, 10) == pytest.approx(0.2)
    assert pooled_recall_at_k(ranking, judgments, 5) == pytest.approx(0.5)
    assert precision_at_k(ranking, judgments, 5) == pytest.approx(0.2)
    assert hit_rate_at_k(ranking, judgments, 5) == 1.0


def test_no_relevant_result_and_multiple_direct_results() -> None:
    judgments = {"a": 2, "b": 2, "support": 1, "negative": 0}
    misses = ["negative", "unjudged"]
    assert reciprocal_rank_at_k(misses, judgments, 10) == 0.0
    assert pooled_recall_at_k(misses, judgments, 20) == 0.0
    assert precision_at_k(misses, judgments, 5) == 0.0
    assert hit_rate_at_k(misses, judgments, 10) == 0.0

    assert pooled_recall_at_k(["a", "b"], judgments, 5) == 1.0
    assert precision_at_k(["a", "b"], judgments, 5) == pytest.approx(0.4)


def test_short_ranking_uses_fixed_k_denominators() -> None:
    ranking = ["direct", "negative"]
    judgments = {"direct": 2, "negative": 0}
    assert precision_at_k(ranking, judgments, 5) == pytest.approx(1.0 / 5.0)
    assert judged_at_k(ranking, judgments, 5) == pytest.approx(2.0 / 5.0)
    assert judged_at_k(ranking, judgments, 30) == pytest.approx(2.0 / 30.0)


def test_unjudged_documents_remain_distinct_from_relevance_zero() -> None:
    judgments = {"explicit_zero": 0, "direct": 2}
    ranking = ["unjudged", "explicit_zero", "direct"]
    assert ranked_relevances(ranking, judgments) == (None, 0, 2)
    assert judged_at_k(ranking, judgments, 3) == pytest.approx(2.0 / 3.0)
    assert reciprocal_rank_at_k(ranking, judgments, 10) == pytest.approx(1.0 / 3.0)
    assert "unjudged" not in judgments


def test_equal_scores_do_not_change_supplied_rank_order() -> None:
    ranking = [
        {"chunk_id": "first", "score": 1.0},
        {"chunk_id": "second", "score": 1.0},
    ]
    assert reciprocal_rank_at_k(ranking, {"first": 0, "second": 2}, 10) == 0.5


def test_rel2_and_rel1plus_metrics_are_separate() -> None:
    metrics = evaluate_query(["support", "direct"], {"support": 1, "direct": 2})
    assert set(metrics) == set(REQUIRED_METRIC_NAMES)
    assert metrics["MRR@10_rel2"] == 0.5
    assert metrics["MRR@10_rel1plus"] == 1.0
    assert metrics["Precision@5_rel2"] == pytest.approx(0.2)
    assert metrics["Precision@5_rel1plus"] == pytest.approx(0.4)


def test_evaluate_run_returns_per_query_and_macro_outputs() -> None:
    run = {
        "Q1": ["a"],
        "Q2": ["miss", "b"],
    }
    qrels = {
        "Q1": {"a": 2},
        "Q2": {"b": 2},
        "Q3": {"c": 2},
    }
    result = evaluate_run(run, qrels)
    assert result["evaluation_label"] == "machine-proposed development evaluation"
    assert result["query_count"] == 3
    assert set(result["per_query"]) == {"Q1", "Q2", "Q3"}
    assert result["per_query"]["Q3"]["MRR@10_rel2"] == 0.0
    assert result["overall"]["MRR@10_rel2"] == pytest.approx((1.0 + 0.5) / 3.0)


def test_invalid_duplicate_ranking_and_relevance_are_rejected() -> None:
    with pytest.raises(ValueError, match="same document"):
        evaluate_query(["a", "a"], {"a": 2})
    with pytest.raises(ValueError, match="Unsupported relevance"):
        evaluate_query(["a"], {"a": 3})


def test_trec_and_qrel_record_objects_are_accepted_directly() -> None:
    from sqlmend_retrieval.qrels import QrelEntry
    from sqlmend_retrieval.trec import TrecRunEntry

    run = [
        TrecRunEntry("Q1", "miss", 2, 0.5, "fixture"),
        TrecRunEntry("Q1", "direct", 1, 1.0, "fixture"),
    ]
    qrels = [
        QrelEntry("Q1", "miss", 0),
        QrelEntry("Q1", "direct", 2),
    ]
    result = evaluate_run(run, qrels)
    assert result["per_query"]["Q1"]["MRR@10_rel2"] == 1.0
