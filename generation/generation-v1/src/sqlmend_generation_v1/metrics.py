"""Pure metric helpers for the Phase 10 generation comparison.

This module intentionally knows nothing about the development references or
qrels.  It operates only on already-produced wrappers and offline judgment
records, which keeps it safe to import from tests and presentation code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
from statistics import fmean
from typing import Any


NOT_APPLICABLE = "N/A"


def percentile(values: Iterable[float], quantile: float) -> float:
    """Return a linearly interpolated percentile for finite numeric values.

    The definition matches NumPy's default linear percentile calculation, but
    avoids adding a heavyweight dependency to the generation release.
    """

    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(_finite_float(value, "percentile value") for value in values)
    if not ordered:
        raise ValueError("at least one value is required")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def latency_summary(values_ms: Iterable[float]) -> dict[str, float | int]:
    """Summarize wall-clock generation latency in milliseconds."""

    values = [_finite_float(value, "latency") for value in values_ms]
    if any(value < 0.0 for value in values):
        raise ValueError("latency values must be non-negative")
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0}
    return {
        "count": len(values),
        "mean": fmean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
    }


def task_success(judgment: Mapping[str, Any]) -> bool:
    """Task success requires all four semantic compatibility checks."""

    return all(
        judgment.get(field) is True
        for field in (
            "root_cause_correct",
            "sql_repair_correct",
            "dialect_compatible",
            "version_compatible",
        )
    )


def citation_ids(answer: Any) -> list[str]:
    """Extract citation passage IDs without accepting invented structures.

    The formal schema uses a list of passage-ID strings.  Mapping items with a
    conventional ``passage_id``/``citation_id``/``id`` field are accepted for
    backwards-compatible auditing, but malformed entries remain visible as an
    invalid sentinel instead of silently disappearing.
    """

    if not isinstance(answer, Mapping):
        return []
    citations = answer.get("citations")
    if citations is None:
        return []
    if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
        return ["__INVALID_CITATION_CONTAINER__"]
    result: list[str] = []
    for item in citations:
        value: Any = item
        if isinstance(item, Mapping):
            value = item.get("passage_id", item.get("citation_id", item.get("id")))
        if not isinstance(value, str) or not value.strip() or value.strip() != value:
            result.append("__INVALID_CITATION_ITEM__")
        else:
            result.append(value)
    return result


def citation_validity(
    answer: Any,
    provided_passage_ids: Iterable[str],
) -> dict[str, Any]:
    """Deterministically verify citations against this query's supplied IDs.

    An answer with no citations has validity 1.0: it invents no source.  Missing
    support is measured separately by citation coverage.  Duplicate citations
    are retained because every emitted citation occurrence must be valid.
    """

    provided = frozenset(provided_passage_ids)
    cited = citation_ids(answer)
    valid = [item for item in cited if item in provided]
    invalid = [item for item in cited if item not in provided]
    score = 1.0 if not cited else len(valid) / len(cited)
    return {
        "score": score,
        "citation_count": len(cited),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "cited_passage_ids": cited,
        "invalid_passage_ids": invalid,
    }


def context_retrieval_metrics(
    provided_passage_ids: Iterable[str],
    query_qrels: Mapping[str, int | float],
    *,
    relevance_threshold: float = 1.0,
) -> dict[str, Any]:
    """Compute rel>=1 context precision and query hit deterministically."""

    passage_ids = list(provided_passage_ids)
    if len(passage_ids) != len(set(passage_ids)):
        raise ValueError("provided passage IDs must be unique")
    missing = [passage_id for passage_id in passage_ids if passage_id not in query_qrels]
    relevant = [
        passage_id
        for passage_id in passage_ids
        if _finite_float(query_qrels.get(passage_id, 0.0), "qrel")
        >= relevance_threshold
    ]
    precision = len(relevant) / len(passage_ids) if passage_ids else 0.0
    return {
        "context_precision": precision,
        "context_query_hit": bool(relevant),
        "provided_count": len(passage_ids),
        "relevant_count": len(relevant),
        "relevant_passage_ids": relevant,
        "unjudged_passage_ids": missing,
        "fully_judged": not missing,
        "relevance_threshold": relevance_threshold,
    }


def aggregate_system_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    rag_system: bool,
) -> dict[str, Any]:
    """Aggregate one system's complete Phase 10 metric set.

    Every formal wrapper remains in the denominator.  Judge or generation
    failures are represented by the row's conservative false/zero values and
    are never filtered here.
    """

    if not rows:
        raise ValueError("cannot aggregate an empty system")
    count = len(rows)

    def rate(field: str) -> float:
        return sum(row.get(field) is True for row in rows) / count

    def mean_score(field: str) -> float:
        values = [_bounded_score(row.get(field, 0.0), field) for row in rows]
        return fmean(values)

    generation_contract_success_count = sum(
        row.get("status") == "success" for row in rows
    )
    generation_contract_failure_count = count - generation_contract_success_count
    generation_retry_count = sum(
        int(row.get("generation_retry_count", 0)) for row in rows
    )
    generation_attempt_count = sum(
        int(row.get("generation_attempt_count", 1)) for row in rows
    )
    generation_recovered_after_retry_count = sum(
        row.get("status") == "success"
        and int(row.get("generation_retry_count", 0)) > 0
        for row in rows
    )
    result: dict[str, Any] = {
        "formal_result_count": count,
        "success_count_semantics": "generation_contract_success",
        "success_count": generation_contract_success_count,
        "failure_count": generation_contract_failure_count,
        "generation_contract_success_count": generation_contract_success_count,
        "generation_contract_failure_count": generation_contract_failure_count,
        "generation_contract_success_rate": (
            generation_contract_success_count / count
        ),
        "generation_attempt_count": generation_attempt_count,
        "generation_retry_count": generation_retry_count,
        "generation_recovered_after_retry_count": (
            generation_recovered_after_retry_count
        ),
        "task_success_rate": rate("task_success"),
        "root_cause_accuracy": rate("root_cause_correct"),
        "sql_repair_correctness": rate("sql_repair_correct"),
        "dialect_compatibility": rate("dialect_compatible"),
        "version_compatibility": rate("version_compatible"),
        "structured_output_validity": rate("structured_output_valid"),
        "answer_relevance": mean_score("answer_relevance"),
        "latency_ms": latency_summary(
            row.get("latency_wall_ms", 0.0) for row in rows
        ),
    }
    if rag_system:
        result.update(
            {
                "citation_validity": mean_score("citation_validity"),
                "citation_coverage": mean_score("citation_coverage"),
                "faithfulness": mean_score("faithfulness"),
                "context_precision": mean_score("context_precision"),
                "context_query_hit_rate": rate("context_query_hit"),
                "context_fully_judged_rate": rate("context_fully_judged"),
            }
        )
    else:
        # RAG-only measures are semantically undefined for closed-book answers.
        result.update(
            {
                "citation_validity": NOT_APPLICABLE,
                "citation_coverage": NOT_APPLICABLE,
                "faithfulness": NOT_APPLICABLE,
                "context_precision": NOT_APPLICABLE,
                "context_query_hit_rate": NOT_APPLICABLE,
                "context_fully_judged_rate": NOT_APPLICABLE,
            }
        )
    return result


def paired_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize paired task-success movement without dropping ties/failures."""

    if not rows:
        raise ValueError("cannot summarize an empty comparison")
    improved = regressed = both_success = both_failure = 0
    for row in rows:
        g0 = bool(_system_view(row, "g0").get("task_success"))
        g1 = bool(_system_view(row, "g1").get("task_success"))
        if g1 and not g0:
            improved += 1
        elif g0 and not g1:
            regressed += 1
        elif g0:
            both_success += 1
        else:
            both_failure += 1
    count = len(rows)
    absolute_delta = (improved - regressed) / count
    return {
        "query_count": count,
        "paired_count_semantics": "offline_task_success",
        "g1_improved_count": improved,
        "g1_regressed_count": regressed,
        "both_succeeded_count": both_success,
        "neither_succeeded_count": both_failure,
        "both_task_success_count": both_success,
        "neither_task_success_count": both_failure,
        "task_success_absolute_delta": absolute_delta,
        "task_success_percentage_point_delta": absolute_delta * 100.0,
    }


def _system_view(row: Mapping[str, Any], system: str) -> Mapping[str, Any]:
    value = row.get(system)
    return value if isinstance(value, Mapping) else {}


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not bool")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _bounded_score(value: Any, field: str) -> float:
    result = _finite_float(value, field)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be in [0, 1]")
    return result


__all__ = [
    "NOT_APPLICABLE",
    "aggregate_system_metrics",
    "citation_ids",
    "citation_validity",
    "context_retrieval_metrics",
    "latency_summary",
    "paired_summary",
    "percentile",
    "task_success",
]
