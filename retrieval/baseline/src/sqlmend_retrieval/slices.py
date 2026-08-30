"""Explicit query-slice construction and evaluation helpers.

Slice membership is read only from annotation fields.  In particular, case
flags are never inferred from SQL, error messages, or free-form query text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .bootstrap import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_CONFIDENCE_LEVEL,
    DEFAULT_RANDOM_SEED,
    bootstrap_metric_confidence_intervals,
)
from .metrics import PRIMARY_BOOTSTRAP_METRICS, REQUIRED_METRIC_NAMES, evaluate_run

DIALECTS = ("postgresql", "mysql", "sqlite", "mariadb", "duckdb")

# schema field -> (positive report label, negative report label)
CASE_FLAG_LABELS: dict[str, tuple[str, str]] = {
    "requires_dialect_reasoning": (
        "dialect-sensitive",
        "non-dialect-sensitive",
    ),
    "requires_version_reasoning": (
        "version-sensitive",
        "non-version-sensitive",
    ),
    "has_documented_error": (
        "documented-error",
        "non-documented-error",
    ),
    "plausible_but_wrong": (
        "plausible-but-wrong",
        "non-plausible-but-wrong",
    ),
}


@dataclass(frozen=True)
class QuerySlice:
    """A stable slice definition and its ordered member query IDs."""

    slice_name: str
    slice_value: str
    query_ids: tuple[str, ...]
    source_field: str | None = None

    @property
    def query_count(self) -> int:
        return len(self.query_ids)


def _query_records(queries: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(queries, (str, bytes, Mapping)):
        raise TypeError("queries must be an iterable of query records")
    records = list(queries)
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("Every query record must be a mapping")
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            raise ValueError("Every query record must have a non-empty query_id")
        if query_id in seen:
            raise ValueError(f"Duplicate query_id in slice input: {query_id!r}")
        seen.add(query_id)
    return records


def dialect_slices(
    queries: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Return all five required dialect slices, including empty ones."""

    records = _query_records(queries)
    members: dict[str, list[str]] = {dialect: [] for dialect in DIALECTS}
    for record in records:
        dialect = record.get("dialect")
        if dialect not in members:
            raise ValueError(f"Unknown or missing dialect for {record['query_id']!r}: {dialect!r}")
        members[dialect].append(record["query_id"])
    return {dialect: tuple(sorted(query_ids)) for dialect, query_ids in members.items()}


def validate_dialect_query_counts(
    slices: Mapping[str, Sequence[str]], *, expected_per_dialect: int = 50
) -> None:
    """Fail if the five formal dialect slices do not have the expected size."""

    if (
        isinstance(expected_per_dialect, bool)
        or not isinstance(expected_per_dialect, int)
        or expected_per_dialect < 0
    ):
        raise ValueError("expected_per_dialect must be a non-negative integer")
    if set(slices) != set(DIALECTS):
        raise ValueError(
            f"Dialect slices must be exactly {list(DIALECTS)!r}; got {sorted(slices)!r}"
        )
    mismatches = {
        dialect: len(slices[dialect])
        for dialect in DIALECTS
        if len(slices[dialect]) != expected_per_dialect
    }
    if mismatches:
        raise ValueError(
            f"Expected {expected_per_dialect} queries per dialect; observed {mismatches}"
        )


def error_category_slices(
    queries: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Return one slice for every error category present in the data."""

    records = _query_records(queries)
    members: dict[str, list[str]] = {}
    for record in records:
        category = record.get("error_category")
        if not isinstance(category, str) or not category:
            raise ValueError(
                f"Missing error_category for query {record['query_id']!r}; "
                "slice labels must not be inferred"
            )
        members.setdefault(category, []).append(record["query_id"])
    return {
        category: tuple(sorted(members[category]))
        for category in sorted(members)
    }


def case_flag_slices(
    queries: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Return positive and negative slices for each required explicit flag."""

    records = _query_records(queries)
    members: dict[str, list[str]] = {
        label: [] for labels in CASE_FLAG_LABELS.values() for label in labels
    }
    for record in records:
        case_flags = record.get("case_flags")
        if not isinstance(case_flags, Mapping):
            raise ValueError(
                f"Missing case_flags for query {record['query_id']!r}; "
                "slice labels must not be inferred"
            )
        for field, (positive_label, negative_label) in CASE_FLAG_LABELS.items():
            if field not in case_flags or not isinstance(case_flags[field], bool):
                raise ValueError(
                    f"case_flags.{field} must be an explicit boolean for "
                    f"query {record['query_id']!r}"
                )
            label = positive_label if case_flags[field] else negative_label
            members[label].append(record["query_id"])
    return {label: tuple(sorted(query_ids)) for label, query_ids in members.items()}


def build_query_slices(
    queries: Iterable[Mapping[str, Any]],
) -> list[QuerySlice]:
    """Build dialect, observed error-category, and required case-flag slices."""

    records = _query_records(queries)
    dialect_groups = dialect_slices(records)
    category_groups = error_category_slices(records)
    flag_groups = case_flag_slices(records)

    slices = [
        QuerySlice("dialect", value, query_ids, source_field="dialect")
        for value, query_ids in dialect_groups.items()
    ]
    slices.extend(
        QuerySlice(
            "error_category", value, query_ids, source_field="error_category"
        )
        for value, query_ids in category_groups.items()
    )
    for field, (positive_label, negative_label) in CASE_FLAG_LABELS.items():
        slices.append(
            QuerySlice("case_flag", positive_label, flag_groups[positive_label], field)
        )
        slices.append(
            QuerySlice("case_flag", negative_label, flag_groups[negative_label], field)
        )
    return slices


def generate_slices(queries: Iterable[Mapping[str, Any]]) -> list[QuerySlice]:
    """Alias for :func:`build_query_slices`."""

    return build_query_slices(queries)


def _undefined_metrics() -> dict[str, None]:
    return {metric: None for metric in REQUIRED_METRIC_NAMES}


def evaluate_slices(
    run: Mapping[str, Iterable[Any]] | Iterable[Any],
    qrels: Mapping[Any, Any] | Iterable[Any],
    queries: Iterable[Mapping[str, Any]],
    *,
    retriever: str,
    confidence_interval_metrics: Sequence[str] | None = None,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    random_seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> list[dict[str, Any]]:
    """Evaluate every required slice and retain empty/small-sample slices.

    Pass ``confidence_interval_metrics=PRIMARY_BOOTSTRAP_METRICS`` to attach
    the formal query-bootstrap CIs.  They are optional here so callers writing
    a consolidated report can bootstrap once at the desired layer.
    """

    if not isinstance(retriever, str) or not retriever:
        raise ValueError("retriever must be a non-empty string")
    requested_ci_metrics = (
        tuple(confidence_interval_metrics)
        if confidence_interval_metrics is not None
        else ()
    )
    unknown_ci_metrics = set(requested_ci_metrics) - set(REQUIRED_METRIC_NAMES)
    if unknown_ci_metrics:
        raise ValueError(f"Unknown confidence-interval metrics: {sorted(unknown_ci_metrics)}")

    rows: list[dict[str, Any]] = []
    for query_slice in build_query_slices(queries):
        row: dict[str, Any] = {
            "slice_name": query_slice.slice_name,
            "slice_value": query_slice.slice_value,
            "source_field": query_slice.source_field,
            "query_count": query_slice.query_count,
            "retriever": retriever,
        }
        if query_slice.query_count == 0:
            row.update(_undefined_metrics())
            row["confidence_intervals"] = {
                metric: None for metric in requested_ci_metrics
            }
            row["estimate_warning"] = "empty slice; metrics are undefined"
        else:
            evaluation = evaluate_run(
                run, qrels, query_ids=query_slice.query_ids
            )
            row.update(evaluation["overall"])
            row["confidence_intervals"] = (
                bootstrap_metric_confidence_intervals(
                    evaluation["per_query"],
                    requested_ci_metrics,
                    n_samples=bootstrap_samples,
                    seed=random_seed,
                    confidence_level=confidence_level,
                )
                if requested_ci_metrics
                else {}
            )
            row["estimate_warning"] = (
                "small sample; estimates may be unstable"
                if query_slice.query_count < 30
                else None
            )
        rows.append(row)
    return rows


def slice_metrics(
    run: Mapping[str, Iterable[Any]] | Iterable[Any],
    qrels: Mapping[Any, Any] | Iterable[Any],
    queries: Iterable[Mapping[str, Any]],
    *,
    retriever: str,
) -> list[dict[str, Any]]:
    """Convenience alias for slice evaluation without confidence intervals."""

    return evaluate_slices(run, qrels, queries, retriever=retriever)


__all__ = [
    "CASE_FLAG_LABELS",
    "DIALECTS",
    "PRIMARY_BOOTSTRAP_METRICS",
    "QuerySlice",
    "build_query_slices",
    "case_flag_slices",
    "dialect_slices",
    "error_category_slices",
    "evaluate_slices",
    "generate_slices",
    "slice_metrics",
    "validate_dialect_query_counts",
]
