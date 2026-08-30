"""Incremental online-latency benchmark for retrieval v1.

The frozen Hybrid latency remains the only measured end-to-end reference.
Retrieval-v1 measures only the extra metadata and lexical ranking work over
already-loaded queries, candidates, configuration, and lexical statistics.
Consequently every new system's end-to-end number is explicitly an estimate:
the frozen measured reference plus one or more measured increments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from pathlib import Path
import time
from typing import Any

from .io import load_json, write_json
from .models import OnlineQuery
from .ranking import CandidateState, rank_metadata_aware
from .reranker import CorpusLexicalIndex, build_corpus_lexical_index, rank_field_aware


EVALUATION_LABEL = "machine-proposed development evaluation"
LATENCY_SCHEMA_VERSION = "sqlmend-retrieval-v1-latency-v1"
DEFAULT_QUERY_COUNT = 250
DEFAULT_REPETITIONS = 1
DEFAULT_WARMUP_QUERIES = 3
FROZEN_SYSTEM_ID = "hybrid_rrf_frozen_control_v1"
DIALECT_SYSTEM_ID = "hybrid_rrf_dialect_aware_v1"
VERSION_SYSTEM_ID = "hybrid_rrf_version_aware_v1"
COMBINED_SYSTEM_ID = "hybrid_rrf_dialect_version_aware_v1"
FINAL_SYSTEM_ID = "hybrid_rrf_dialect_version_lexical_rerank_v1"

STAGE_SYSTEM_IDS = {
    "dialect_metadata_rerank": DIALECT_SYSTEM_ID,
    "version_metadata_rerank": VERSION_SYSTEM_ID,
    "dialect_version_metadata_rerank": COMBINED_SYSTEM_ID,
    "lexical_reranker": FINAL_SYSTEM_ID,
}
_SUMMARY_FIELDS = ("mean_ms", "p50_ms", "p95_ms")


class LatencyBenchmarkError(ValueError):
    """Raised when inputs cannot support a comparable latency benchmark."""


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LatencyBenchmarkError(f"{field} must be a positive integer")
    return value


def _finite_nonnegative(value: Any, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise LatencyBenchmarkError(f"{field} must be a finite non-negative number")
    return 0.0 if float(value) == 0.0 else float(value)


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    """Linear percentile compatible with NumPy's default interpolation."""

    if not sorted_values:
        raise LatencyBenchmarkError("latency samples cannot be empty")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] + fraction * (
        sorted_values[upper] - sorted_values[lower]
    )


def summarize_latency_samples(samples_ms: Sequence[float]) -> dict[str, float | int]:
    """Return stable mean/P50/P95 statistics for millisecond samples."""

    if isinstance(samples_ms, (str, bytes)):
        raise TypeError("samples_ms must be a numeric sequence")
    values = [
        _finite_nonnegative(value, f"latency sample {index}")
        for index, value in enumerate(samples_ms)
    ]
    if not values:
        raise LatencyBenchmarkError("latency samples cannot be empty")
    ordered = sorted(values)
    return {
        "mean_ms": math.fsum(values) / len(values),
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "sample_count": len(values),
    }


def load_frozen_hybrid_reference(
    latency_path: str | Path,
    *,
    expected_query_count: int = DEFAULT_QUERY_COUNT,
    source_artifact: str = "retrieval/baseline/evaluation/latency.json",
) -> dict[str, Any]:
    """Load and validate the immutable measured Hybrid total-latency summary."""

    query_count = _positive_integer(expected_query_count, "expected_query_count")
    path = Path(latency_path)
    payload = load_json(path)
    if payload.get("evaluation_label") != EVALUATION_LABEL:
        raise LatencyBenchmarkError("frozen latency evaluation label is unexpected")
    if payload.get("query_count") != query_count:
        raise LatencyBenchmarkError(
            f"frozen latency query_count is {payload.get('query_count')!r}; "
            f"expected {query_count}"
        )
    try:
        total = payload["warm_query_latency"]["hybrid"]["total"]
    except (KeyError, TypeError) as exc:
        raise LatencyBenchmarkError("frozen Hybrid total-latency summary is missing") from exc
    if not isinstance(total, Mapping):
        raise LatencyBenchmarkError("frozen Hybrid total-latency summary must be a mapping")
    summary = {
        field: _finite_nonnegative(total.get(field), f"frozen Hybrid {field}")
        for field in _SUMMARY_FIELDS
    }
    sample_count = _positive_integer(total.get("sample_count"), "frozen sample_count")
    if sample_count < query_count:
        raise LatencyBenchmarkError("frozen latency has fewer samples than queries")
    if not isinstance(source_artifact, str) or not source_artifact:
        raise LatencyBenchmarkError("source_artifact must be a non-empty string")
    return {
        "system_id": FROZEN_SYSTEM_ID,
        "latency_type": "frozen_measured_reference",
        "description": "frozen measured reference; copied without remeasurement",
        "source_artifact": source_artifact,
        "query_count": query_count,
        "sample_count": sample_count,
        "total_latency_ms": summary,
    }


def _validate_reference(
    reference: Mapping[str, Any], *, expected_query_count: int
) -> dict[str, Any]:
    if not isinstance(reference, Mapping):
        raise TypeError("frozen_reference must be a mapping")
    if reference.get("system_id") != FROZEN_SYSTEM_ID:
        raise LatencyBenchmarkError("frozen reference system_id is unexpected")
    if reference.get("latency_type") != "frozen_measured_reference":
        raise LatencyBenchmarkError("baseline total must be labeled frozen_measured_reference")
    if reference.get("query_count") != expected_query_count:
        raise LatencyBenchmarkError("frozen reference query count differs from benchmark")
    total = reference.get("total_latency_ms")
    if not isinstance(total, Mapping):
        raise LatencyBenchmarkError("frozen reference total_latency_ms is missing")
    for field in _SUMMARY_FIELDS:
        _finite_nonnegative(total.get(field), f"frozen reference {field}")
    return dict(reference)


def _validate_preloaded_inputs(
    candidates: Mapping[str, Sequence[CandidateState]],
    online_queries: Mapping[str, OnlineQuery],
    configs: Mapping[str, Mapping[str, Any]],
    lexical_index: CorpusLexicalIndex,
    *,
    expected_query_count: int,
) -> tuple[str, ...]:
    if not isinstance(candidates, Mapping) or not isinstance(online_queries, Mapping):
        raise TypeError("candidates and online_queries must be preloaded mappings")
    if set(candidates) != set(online_queries):
        raise LatencyBenchmarkError("candidate and online-query coverage differs")
    if len(online_queries) != expected_query_count:
        raise LatencyBenchmarkError(
            f"preloaded query count is {len(online_queries)}, expected {expected_query_count}"
        )
    for query_id in sorted(online_queries):
        query = online_queries[query_id]
        if not isinstance(query, OnlineQuery) or query.query_id != query_id:
            raise LatencyBenchmarkError(f"invalid preloaded OnlineQuery for {query_id!r}")
        states = candidates[query_id]
        if not isinstance(states, Sequence) or not states:
            raise LatencyBenchmarkError(f"query {query_id!r} has no preloaded candidates")
        if any(not isinstance(state, CandidateState) for state in states):
            raise LatencyBenchmarkError(
                f"query {query_id!r} contains a non-CandidateState candidate"
            )
    if not isinstance(configs, Mapping):
        raise TypeError("configs must be a preloaded system-config mapping")
    missing_configs = sorted(set(STAGE_SYSTEM_IDS.values()) - set(configs))
    if missing_configs:
        raise LatencyBenchmarkError(f"missing system configs: {missing_configs!r}")
    if not isinstance(lexical_index, CorpusLexicalIndex):
        raise TypeError("lexical_index must be preloaded before online measurement")
    return tuple(sorted(online_queries))


def _measure_stage(
    query_ids: Sequence[str],
    operation: Callable[[str], Any],
    *,
    repetitions: int,
    warmup_queries: int,
    clock: Callable[[], float],
) -> dict[str, float | int]:
    # Warmup is deliberately outside the measured samples.
    for query_id in query_ids[:warmup_queries]:
        operation(query_id)

    samples_ms: list[float] = []
    for _ in range(repetitions):
        for query_id in query_ids:
            started = clock()
            operation(query_id)
            finished = clock()
            elapsed = (finished - started) * 1000.0
            if elapsed < 0.0:
                raise LatencyBenchmarkError("benchmark clock moved backwards")
            samples_ms.append(0.0 if elapsed == 0.0 else elapsed)
    return summarize_latency_samples(samples_ms)


def _summary_only(summary: Mapping[str, Any]) -> dict[str, float]:
    return {
        field: _finite_nonnegative(summary.get(field), field)
        for field in _SUMMARY_FIELDS
    }


def _componentwise_sum(*summaries: Mapping[str, Any]) -> dict[str, float]:
    return {
        field: math.fsum(_finite_nonnegative(summary.get(field), field) for summary in summaries)
        for field in _SUMMARY_FIELDS
    }


def benchmark_incremental_latency(
    candidates: Mapping[str, Sequence[CandidateState]],
    online_queries: Mapping[str, OnlineQuery],
    configs: Mapping[str, Mapping[str, Any]],
    lexical_index: CorpusLexicalIndex,
    frozen_reference: Mapping[str, Any],
    *,
    expected_query_count: int = DEFAULT_QUERY_COUNT,
    repetitions: int = DEFAULT_REPETITIONS,
    warmup_queries: int = DEFAULT_WARMUP_QUERIES,
    clock: Callable[[], float] = time.perf_counter,
    clock_label: str = "time.perf_counter monotonic high-resolution clock",
    lexical_index_build_ms: float | None = None,
) -> dict[str, Any]:
    """Measure incremental online work after every reusable input is loaded.

    Each stage is warmed independently, then invoked at least once for every
    query.  Lexical measurement excludes dialect+version scoring; that prior
    stage is measured separately and is added when estimating the final system.
    """

    query_count = _positive_integer(expected_query_count, "expected_query_count")
    repeat_count = _positive_integer(repetitions, "repetitions")
    warmup_count = _positive_integer(warmup_queries, "warmup_queries")
    if not callable(clock):
        raise TypeError("clock must be callable")
    if not isinstance(clock_label, str) or not clock_label:
        raise LatencyBenchmarkError("clock_label must be a non-empty string")
    query_ids = _validate_preloaded_inputs(
        candidates,
        online_queries,
        configs,
        lexical_index,
        expected_query_count=query_count,
    )
    if warmup_count > query_count:
        raise LatencyBenchmarkError("warmup_queries cannot exceed the query count")
    reference = _validate_reference(
        frozen_reference, expected_query_count=query_count
    )

    # This is setup for the lexical operator, not a timed query.  It consumes
    # the same safe fields as the online metadata stage and no evaluation data.
    _, _, combined_metadata = rank_metadata_aware(
        candidates,
        online_queries,
        configs[COMBINED_SYSTEM_ID],
    )

    def metadata_operation(system_id: str) -> Callable[[str], Any]:
        config = configs[system_id]

        def run_one(query_id: str) -> Any:
            return rank_metadata_aware(
                {query_id: candidates[query_id]},
                {query_id: online_queries[query_id]},
                config,
            )

        return run_one

    final_config = configs[FINAL_SYSTEM_ID]

    def lexical_operation(query_id: str) -> Any:
        return rank_field_aware(
            {query_id: candidates[query_id]},
            {query_id: online_queries[query_id]},
            {query_id: combined_metadata[query_id]},
            lexical_index,
            gamma=float(final_config["gamma"]),
            run_tag=str(final_config["run_tag"]),
            output_depth=int(final_config["output_depth"]),
        )

    operations = {
        "dialect_metadata_rerank": metadata_operation(DIALECT_SYSTEM_ID),
        "version_metadata_rerank": metadata_operation(VERSION_SYSTEM_ID),
        "dialect_version_metadata_rerank": metadata_operation(COMBINED_SYSTEM_ID),
        "lexical_reranker": lexical_operation,
    }
    measured: dict[str, dict[str, Any]] = {}
    for stage_name, operation in operations.items():
        stats = _measure_stage(
            query_ids,
            operation,
            repetitions=repeat_count,
            warmup_queries=warmup_count,
            clock=clock,
        )
        measured[stage_name] = {
            "system_id": STAGE_SYSTEM_IDS[stage_name],
            "latency_type": "measured_increment",
            "measurement_scope": (
                "lexical reranker only; dialect+version increment excluded"
                if stage_name == "lexical_reranker"
                else "metadata reranking increment over preloaded frozen RRF candidates"
            ),
            "query_count": query_count,
            "repetitions": repeat_count,
            **stats,
        }

    reference_total = reference["total_latency_ms"]
    dialect_increment = _summary_only(measured["dialect_metadata_rerank"])
    version_increment = _summary_only(measured["version_metadata_rerank"])
    combined_increment = _summary_only(measured["dialect_version_metadata_rerank"])
    lexical_increment = _summary_only(measured["lexical_reranker"])

    systems: dict[str, dict[str, Any]] = {
        FROZEN_SYSTEM_ID: {
            "system_id": FROZEN_SYSTEM_ID,
            "method": "frozen_measured_reference",
            "total_latency_type": "frozen_measured_reference",
            **_summary_only(reference_total),
            "total_latency_ms": _summary_only(reference_total),
            "incremental": None,
            "source_artifact": reference.get("source_artifact"),
        }
    }
    for system_id, stage_name, increment in (
        (DIALECT_SYSTEM_ID, "dialect_metadata_rerank", dialect_increment),
        (VERSION_SYSTEM_ID, "version_metadata_rerank", version_increment),
        (COMBINED_SYSTEM_ID, "dialect_version_metadata_rerank", combined_increment),
    ):
        total_estimate = _componentwise_sum(reference_total, increment)
        systems[system_id] = {
            "system_id": system_id,
            "method": "estimate",
            "total_latency_type": "estimate",
            "estimate_formula": f"frozen measured Hybrid total + measured {stage_name} increment",
            "increment_components": [stage_name],
            **total_estimate,
            "incremental": {
                "method": "measured_increment",
                **increment,
            },
            "incremental_online_latency_ms": increment,
            "total_latency_estimate_ms": total_estimate,
            "percentile_estimate_note": (
                "P50/P95 are componentwise sums because frozen per-query samples "
                "are not remeasured or paired with v1 increments"
            ),
        }

    final_increment_estimate = _componentwise_sum(combined_increment, lexical_increment)
    final_total_estimate = _componentwise_sum(
        reference_total, combined_increment, lexical_increment
    )
    systems[FINAL_SYSTEM_ID] = {
        "system_id": FINAL_SYSTEM_ID,
        "method": "estimate",
        "total_latency_type": "estimate",
        "estimate_formula": (
            "frozen measured Hybrid total + measured dialect_version_metadata_rerank "
            "increment + measured lexical_reranker increment"
        ),
        "increment_components": [
            "dialect_version_metadata_rerank",
            "lexical_reranker",
        ],
        **final_total_estimate,
        "incremental": {
            "method": "componentwise_estimate_from_measured_increments",
            **final_increment_estimate,
        },
        "reranker_incremental": {
            "method": "measured_increment",
            **lexical_increment,
        },
        "incremental_online_latency_type": "componentwise_estimate_from_measured_increments",
        "incremental_online_latency_ms": final_increment_estimate,
        "total_latency_estimate_ms": final_total_estimate,
        "percentile_estimate_note": (
            "P50/P95 are componentwise sums because component samples are not "
            "paired end-to-end with the frozen reference"
        ),
    }

    setup: dict[str, Any] = {
        "queries_preloaded": True,
        "candidates_preloaded": True,
        "configs_preloaded": True,
        "lexical_index_preloaded": True,
        "setup_excluded_from_incremental_online_latency": True,
    }
    if lexical_index_build_ms is not None:
        setup["lexical_index_build_ms"] = _finite_nonnegative(
            lexical_index_build_ms, "lexical_index_build_ms"
        )

    return {
        "schema_version": LATENCY_SCHEMA_VERSION,
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "benchmark_scope": (
            "incremental online ranking over preloaded inputs; frozen retrieval is "
            "not rerun"
        ),
        "clock": clock_label,
        "query_count": query_count,
        "repetitions": repeat_count,
        "warmup_queries_per_stage": warmup_count,
        "each_query_measured_at_least_once": True,
        "setup": setup,
        "frozen_hybrid_reference": reference,
        "measured_incremental_online_latency": measured,
        "systems": systems,
    }


def write_latency_report(path: str | Path, result: Mapping[str, Any]) -> Path:
    """Write canonical latency JSON after validating its fixed top-level schema."""

    required = {
        "schema_version",
        "evaluation_label",
        "query_count",
        "frozen_hybrid_reference",
        "measured_incremental_online_latency",
        "systems",
    }
    missing = sorted(required - set(result))
    if missing:
        raise LatencyBenchmarkError(f"latency result is missing fields: {missing!r}")
    if result.get("schema_version") != LATENCY_SCHEMA_VERSION:
        raise LatencyBenchmarkError("latency result schema_version is unexpected")
    output = Path(path)
    write_json(output, dict(result))
    return output


def run_latency_benchmark(
    paths: Any,
    inputs: Any,
    *,
    repetitions: int = DEFAULT_REPETITIONS,
    warmup_queries: int = DEFAULT_WARMUP_QUERIES,
    lexical_index: CorpusLexicalIndex | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Repository-level entry point writing ``reports/latency.json``.

    ``inputs`` must already be the validated, preloaded online input bundle.
    Corpus lexical-index construction happens before query timing and is
    explicitly reported as excluded setup work.
    """

    # Local import avoids coupling the reusable benchmark core to orchestration.
    from .pipeline import load_system_configs

    configs = load_system_configs(paths)
    index_build_ms: float | None = None
    prepared_index = lexical_index
    if prepared_index is None:
        started = time.perf_counter()
        prepared_index = build_corpus_lexical_index(inputs.corpus_by_id)
        index_build_ms = (time.perf_counter() - started) * 1000.0
    elif not isinstance(prepared_index, CorpusLexicalIndex):
        raise TypeError("lexical_index must be a CorpusLexicalIndex")

    baseline_path = paths.baseline / "evaluation" / "latency.json"
    reference = load_frozen_hybrid_reference(baseline_path)
    result = benchmark_incremental_latency(
        inputs.candidates,
        inputs.online_queries,
        configs,
        prepared_index,
        reference,
        repetitions=repetitions,
        warmup_queries=warmup_queries,
        clock=clock,
        lexical_index_build_ms=index_build_ms,
    )
    write_latency_report(paths.reports / "latency.json", result)
    return result


__all__ = [
    "COMBINED_SYSTEM_ID",
    "DEFAULT_QUERY_COUNT",
    "DEFAULT_REPETITIONS",
    "DEFAULT_WARMUP_QUERIES",
    "DIALECT_SYSTEM_ID",
    "EVALUATION_LABEL",
    "FINAL_SYSTEM_ID",
    "FROZEN_SYSTEM_ID",
    "LATENCY_SCHEMA_VERSION",
    "LatencyBenchmarkError",
    "STAGE_SYSTEM_IDS",
    "VERSION_SYSTEM_ID",
    "benchmark_incremental_latency",
    "load_frozen_hybrid_reference",
    "run_latency_benchmark",
    "summarize_latency_samples",
    "write_latency_report",
]
