"""Deterministic query-level bootstrap confidence intervals.

Pairwise comparisons resample aligned query differences, so each bootstrap
draw preserves the pairing between systems.  Defaults are fixed by the formal
baseline specification: 10,000 samples, seed 42, and a 95% percentile CI.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import Any

from .metrics import PRIMARY_BOOTSTRAP_METRICS

DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_RANDOM_SEED = 42
DEFAULT_CONFIDENCE_LEVEL = 0.95

REQUIRED_SYSTEM_COMPARISONS = (
    ("dense", "bm25"),
    ("hybrid", "bm25"),
    ("hybrid", "dense"),
)


def _validate_config(
    n_samples: int, seed: int, confidence_level: float
) -> tuple[int, int, float]:
    if isinstance(n_samples, bool) or not isinstance(n_samples, int) or n_samples <= 0:
        raise ValueError("n_samples must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not isinstance(confidence_level, (int, float)) or isinstance(confidence_level, bool):
        raise ValueError("confidence_level must be numeric")
    confidence_level = float(confidence_level)
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between 0 and 1")
    return n_samples, seed, confidence_level


def _finite_values(values: Sequence[float] | Mapping[str, float]) -> list[float]:
    ordered = (
        [values[key] for key in sorted(values)]
        if isinstance(values, Mapping)
        else list(values)
    )
    if not ordered:
        raise ValueError("At least one query value is required for bootstrap")
    result: list[float] = []
    for value in ordered:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Bootstrap values must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Bootstrap values must be finite")
        result.append(number)
    return result


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    """Linearly interpolated percentile (the common ``(n - 1) * p`` rule)."""

    if not sorted_values:
        raise ValueError("Cannot take a percentile of an empty sequence")
    if probability <= 0.0:
        return float(sorted_values[0])
    if probability >= 1.0:
        return float(sorted_values[-1])
    position = (len(sorted_values) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return float(sorted_values[lower_index])
    fraction = position - lower_index
    lower = sorted_values[lower_index]
    upper = sorted_values[upper_index]
    return float(lower + (upper - lower) * fraction)


def _bootstrap_means(values: Sequence[float], n_samples: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    query_count = len(values)
    means: list[float] = []
    append = means.append
    for _ in range(n_samples):
        total = math.fsum(values[rng.randrange(query_count)] for _ in range(query_count))
        append(total / query_count)
    means.sort()
    return means


def bootstrap_confidence_interval(
    values: Sequence[float] | Mapping[str, float],
    *,
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> dict[str, float | int]:
    """Return a deterministic percentile CI for a query-macro mean."""

    n_samples, seed, confidence_level = _validate_config(
        n_samples, seed, confidence_level
    )
    numeric = _finite_values(values)
    bootstrap_means = _bootstrap_means(numeric, n_samples, seed)
    alpha = (1.0 - confidence_level) / 2.0
    lower = _percentile(bootstrap_means, alpha)
    upper = _percentile(bootstrap_means, 1.0 - alpha)
    return {
        "mean": math.fsum(numeric) / len(numeric),
        "ci95_lower": lower,
        "ci95_upper": upper,
        "confidence_level": confidence_level,
        "bootstrap_samples": n_samples,
        "random_seed": seed,
        "query_count": len(numeric),
    }


def bootstrap_mean_ci(
    values: Sequence[float] | Mapping[str, float],
    *,
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> dict[str, float | int]:
    """Alias for :func:`bootstrap_confidence_interval`."""

    return bootstrap_confidence_interval(
        values,
        n_samples=n_samples,
        seed=seed,
        confidence_level=confidence_level,
    )


def _aligned_values(
    values_a: Sequence[float] | Mapping[str, float],
    values_b: Sequence[float] | Mapping[str, float],
) -> tuple[list[float], list[float]]:
    if isinstance(values_a, Mapping) != isinstance(values_b, Mapping):
        raise TypeError("Paired inputs must both be mappings or both be sequences")
    if isinstance(values_a, Mapping) and isinstance(values_b, Mapping):
        keys_a = set(values_a)
        keys_b = set(values_b)
        if keys_a != keys_b:
            missing_from_a = sorted(keys_b - keys_a)
            missing_from_b = sorted(keys_a - keys_b)
            raise ValueError(
                "Paired systems must cover the same query IDs; "
                f"missing from A={missing_from_a}, missing from B={missing_from_b}"
            )
        query_ids = sorted(keys_a)
        numeric_a = _finite_values([values_a[query_id] for query_id in query_ids])
        numeric_b = _finite_values([values_b[query_id] for query_id in query_ids])
    else:
        numeric_a = _finite_values(values_a)
        numeric_b = _finite_values(values_b)
        if len(numeric_a) != len(numeric_b):
            raise ValueError("Paired systems must have the same number of query values")
    return numeric_a, numeric_b


def paired_bootstrap_comparison(
    values_a: Sequence[float] | Mapping[str, float],
    values_b: Sequence[float] | Mapping[str, float],
    *,
    system_a: str,
    system_b: str,
    metric: str,
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    tie_tolerance: float = 0.0,
) -> dict[str, Any]:
    """Compare ``system_a - system_b`` with paired query resampling."""

    n_samples, seed, confidence_level = _validate_config(
        n_samples, seed, confidence_level
    )
    if not system_a or not system_b or not metric:
        raise ValueError("system_a, system_b, and metric must be non-empty")
    if tie_tolerance < 0.0 or not math.isfinite(tie_tolerance):
        raise ValueError("tie_tolerance must be a finite non-negative number")

    numeric_a, numeric_b = _aligned_values(values_a, values_b)
    differences = [a - b for a, b in zip(numeric_a, numeric_b, strict=True)]
    bootstrap_means = _bootstrap_means(differences, n_samples, seed)
    alpha = (1.0 - confidence_level) / 2.0
    lower = _percentile(bootstrap_means, alpha)
    upper = _percentile(bootstrap_means, 1.0 - alpha)
    wins_a = sum(difference > tie_tolerance for difference in differences)
    wins_b = sum(difference < -tie_tolerance for difference in differences)
    ties = len(differences) - wins_a - wins_b
    return {
        "system_a": system_a,
        "system_b": system_b,
        "metric": metric,
        "mean_difference": math.fsum(differences) / len(differences),
        "ci95_lower": lower,
        "ci95_upper": upper,
        "queries_a_wins": wins_a,
        "queries_b_wins": wins_b,
        "ties": ties,
        "query_count": len(differences),
        "bootstrap_samples": n_samples,
        "random_seed": seed,
        "confidence_level": confidence_level,
    }


def paired_bootstrap(
    values_a: Sequence[float] | Mapping[str, float],
    values_b: Sequence[float] | Mapping[str, float],
    *,
    system_a: str = "system_a",
    system_b: str = "system_b",
    metric: str = "metric",
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    tie_tolerance: float = 0.0,
) -> dict[str, Any]:
    """Alias for :func:`paired_bootstrap_comparison`."""

    return paired_bootstrap_comparison(
        values_a,
        values_b,
        system_a=system_a,
        system_b=system_b,
        metric=metric,
        n_samples=n_samples,
        seed=seed,
        confidence_level=confidence_level,
        tie_tolerance=tie_tolerance,
    )


def bootstrap_metric_confidence_intervals(
    per_query_metrics: Mapping[str, Mapping[str, float]],
    metric_names: Sequence[str] = PRIMARY_BOOTSTRAP_METRICS,
    *,
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> dict[str, dict[str, float | int]]:
    """Compute macro-mean CIs from a per-query metric table."""

    if not per_query_metrics:
        raise ValueError("At least one query is required for bootstrap")
    query_ids = sorted(per_query_metrics)
    intervals: dict[str, dict[str, float | int]] = {}
    for metric in metric_names:
        try:
            values = {query_id: per_query_metrics[query_id][metric] for query_id in query_ids}
        except KeyError as exc:
            raise KeyError(f"Missing per-query bootstrap metric: {exc.args[0]!r}") from exc
        intervals[metric] = bootstrap_confidence_interval(
            values,
            n_samples=n_samples,
            seed=seed,
            confidence_level=confidence_level,
        )
    return intervals


def _resolve_system_name(systems: Mapping[str, Any], requested: str) -> str:
    matches = [name for name in systems if name.casefold() == requested.casefold()]
    if len(matches) != 1:
        raise KeyError(f"Required system {requested!r} is missing or ambiguous")
    return matches[0]


def _metric_values(system_data: Mapping[str, Any], metric: str) -> dict[str, float]:
    # Accept either {query_id: {metric: value}} or {metric: {query_id: value}}.
    if "per_query" in system_data and isinstance(system_data["per_query"], Mapping):
        return _metric_values(system_data["per_query"], metric)
    if metric in system_data and isinstance(system_data[metric], Mapping):
        return dict(system_data[metric])
    values: dict[str, float] = {}
    for query_id, metrics in system_data.items():
        if not isinstance(metrics, Mapping) or metric not in metrics:
            raise KeyError(f"Missing metric {metric!r} for query {query_id!r}")
        values[query_id] = metrics[metric]
    return values


def pairwise_comparisons(
    per_system_metrics: Mapping[str, Mapping[str, Any]],
    *,
    comparisons: Sequence[tuple[str, str]] = REQUIRED_SYSTEM_COMPARISONS,
    metric_names: Sequence[str] = PRIMARY_BOOTSTRAP_METRICS,
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> list[dict[str, Any]]:
    """Generate the complete required system-by-metric comparison table."""

    rows: list[dict[str, Any]] = []
    for requested_a, requested_b in comparisons:
        system_a = _resolve_system_name(per_system_metrics, requested_a)
        system_b = _resolve_system_name(per_system_metrics, requested_b)
        for metric in metric_names:
            values_a = _metric_values(per_system_metrics[system_a], metric)
            values_b = _metric_values(per_system_metrics[system_b], metric)
            rows.append(
                paired_bootstrap_comparison(
                    values_a,
                    values_b,
                    system_a=system_a,
                    system_b=system_b,
                    metric=metric,
                    n_samples=n_samples,
                    seed=seed,
                    confidence_level=confidence_level,
                )
            )
    return rows


def required_pairwise_comparisons(
    per_system_metrics: Mapping[str, Mapping[str, Any]],
    *,
    n_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_RANDOM_SEED,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> list[dict[str, Any]]:
    """Convenience wrapper for the three required pairs and four metrics."""

    return pairwise_comparisons(
        per_system_metrics,
        n_samples=n_samples,
        seed=seed,
        confidence_level=confidence_level,
    )


__all__ = [
    "DEFAULT_BOOTSTRAP_SAMPLES",
    "DEFAULT_CONFIDENCE_LEVEL",
    "DEFAULT_RANDOM_SEED",
    "REQUIRED_SYSTEM_COMPARISONS",
    "bootstrap_confidence_interval",
    "bootstrap_mean_ci",
    "bootstrap_metric_confidence_intervals",
    "paired_bootstrap",
    "paired_bootstrap_comparison",
    "pairwise_comparisons",
    "required_pairwise_comparisons",
]
