from __future__ import annotations

import pytest

from sqlmend_retrieval.bootstrap import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_CONFIDENCE_LEVEL,
    DEFAULT_RANDOM_SEED,
    bootstrap_confidence_interval,
    paired_bootstrap_comparison,
    required_pairwise_comparisons,
)
from sqlmend_retrieval.metrics import PRIMARY_BOOTSTRAP_METRICS
from sqlmend_retrieval.slices import (
    DIALECTS,
    build_query_slices,
    case_flag_slices,
    dialect_slices,
    error_category_slices,
    evaluate_slices,
    validate_dialect_query_counts,
)


def _query(
    query_id: str,
    dialect: str,
    category: str,
    *,
    dialect_sensitive: bool,
    version_sensitive: bool,
    documented_error: bool,
    plausible_but_wrong: bool,
) -> dict[str, object]:
    return {
        "query_id": query_id,
        "dialect": dialect,
        "error_category": category,
        "case_flags": {
            "requires_dialect_reasoning": dialect_sensitive,
            "requires_version_reasoning": version_sensitive,
            "has_documented_error": documented_error,
            "plausible_but_wrong": plausible_but_wrong,
        },
    }


QUERIES = [
    _query(
        "Q1",
        "postgresql",
        "syntax_error",
        dialect_sensitive=True,
        version_sensitive=False,
        documented_error=True,
        plausible_but_wrong=False,
    ),
    _query(
        "Q2",
        "mysql",
        "type_or_casting",
        dialect_sensitive=False,
        version_sensitive=True,
        documented_error=False,
        plausible_but_wrong=True,
    ),
]


def test_bootstrap_defaults_are_formal_fixed_configuration() -> None:
    assert DEFAULT_BOOTSTRAP_SAMPLES == 10_000
    assert DEFAULT_RANDOM_SEED == 42
    assert DEFAULT_CONFIDENCE_LEVEL == 0.95

    first = bootstrap_confidence_interval([0.0, 0.5, 1.0])
    second = bootstrap_confidence_interval([0.0, 0.5, 1.0])
    assert first == second
    assert first["bootstrap_samples"] == 10_000
    assert first["random_seed"] == 42
    assert first["confidence_level"] == 0.95
    assert first["ci95_lower"] <= first["mean"] <= first["ci95_upper"]


def test_paired_bootstrap_aligns_query_ids_and_counts_wins() -> None:
    values_a = {"Q2": 0.0, "Q1": 1.0, "Q3": 0.5}
    values_b = {"Q1": 0.0, "Q2": 1.0, "Q3": 0.5}
    comparison = paired_bootstrap_comparison(
        values_a,
        values_b,
        system_a="Dense",
        system_b="BM25",
        metric="graded_nDCG@10",
    )
    assert comparison == paired_bootstrap_comparison(
        dict(reversed(list(values_a.items()))),
        dict(reversed(list(values_b.items()))),
        system_a="Dense",
        system_b="BM25",
        metric="graded_nDCG@10",
    )
    assert comparison["mean_difference"] == pytest.approx(0.0)
    assert comparison["queries_a_wins"] == 1
    assert comparison["queries_b_wins"] == 1
    assert comparison["ties"] == 1


def test_required_pairwise_comparison_table_is_complete() -> None:
    per_system: dict[str, dict[str, dict[str, float]]] = {}
    for system, offset in (("BM25", 0.0), ("Dense", 0.1), ("Hybrid", 0.2)):
        per_system[system] = {
            query_id: {
                metric: base + offset
                for metric in PRIMARY_BOOTSTRAP_METRICS
            }
            for query_id, base in (("Q1", 0.2), ("Q2", 0.4))
        }
    rows = required_pairwise_comparisons(per_system, n_samples=100)
    assert len(rows) == 3 * len(PRIMARY_BOOTSTRAP_METRICS)
    assert {(row["system_a"], row["system_b"]) for row in rows} == {
        ("Dense", "BM25"),
        ("Hybrid", "BM25"),
        ("Hybrid", "Dense"),
    }
    assert {row["metric"] for row in rows} == set(PRIMARY_BOOTSTRAP_METRICS)


def test_slice_helpers_use_explicit_schema_fields_and_keep_empty_dialects() -> None:
    dialect_groups = dialect_slices(QUERIES)
    assert tuple(dialect_groups) == DIALECTS
    assert dialect_groups["postgresql"] == ("Q1",)
    assert dialect_groups["sqlite"] == ()

    category_groups = error_category_slices(QUERIES)
    assert category_groups == {
        "syntax_error": ("Q1",),
        "type_or_casting": ("Q2",),
    }

    flag_groups = case_flag_slices(QUERIES)
    assert flag_groups["dialect-sensitive"] == ("Q1",)
    assert flag_groups["non-dialect-sensitive"] == ("Q2",)
    assert flag_groups["version-sensitive"] == ("Q2",)
    assert flag_groups["non-version-sensitive"] == ("Q1",)

    slices = build_query_slices(QUERIES)
    assert len(slices) == 5 + 2 + 8
    assert any(item.slice_value == "sqlite" and item.query_count == 0 for item in slices)
    with pytest.raises(ValueError, match="Expected 50"):
        validate_dialect_query_counts(dialect_groups)


def test_slice_evaluation_reports_required_columns_and_empty_behavior() -> None:
    run = {"Q1": ["d1"], "Q2": ["d2"]}
    qrels = {"Q1": {"d1": 2}, "Q2": {"d2": 2}}
    rows = evaluate_slices(
        run,
        qrels,
        QUERIES,
        retriever="bm25",
        confidence_interval_metrics=("graded_nDCG@10",),
        bootstrap_samples=50,
    )
    postgresql = next(
        row
        for row in rows
        if row["slice_name"] == "dialect" and row["slice_value"] == "postgresql"
    )
    assert postgresql["query_count"] == 1
    assert postgresql["graded_nDCG@10"] == 1.0
    assert postgresql["MRR@10_rel2"] == 1.0
    assert postgresql["pooled_Recall@10_rel2"] == 1.0
    assert postgresql["Judged@30"] == pytest.approx(1.0 / 30.0)
    assert postgresql["confidence_intervals"]["graded_nDCG@10"]["ci95_lower"] == 1.0

    sqlite = next(
        row
        for row in rows
        if row["slice_name"] == "dialect" and row["slice_value"] == "sqlite"
    )
    assert sqlite["query_count"] == 0
    assert sqlite["graded_nDCG@10"] is None
    assert sqlite["Judged@30"] is None
    assert sqlite["estimate_warning"] == "empty slice; metrics are undefined"


def test_missing_explicit_case_flag_is_not_inferred() -> None:
    bad_query = dict(QUERIES[0])
    bad_query["case_flags"] = {"requires_dialect_reasoning": True}
    with pytest.raises(ValueError, match="explicit boolean"):
        case_flag_slices([bad_query])
