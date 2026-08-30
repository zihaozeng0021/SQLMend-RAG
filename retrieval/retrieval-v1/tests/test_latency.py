from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlmend_retrieval_v1.latency import (
    COMBINED_SYSTEM_ID,
    DIALECT_SYSTEM_ID,
    FINAL_SYSTEM_ID,
    FROZEN_SYSTEM_ID,
    LATENCY_SCHEMA_VERSION,
    VERSION_SYSTEM_ID,
    LatencyBenchmarkError,
    benchmark_incremental_latency,
    load_frozen_hybrid_reference,
    summarize_latency_samples,
    write_latency_report,
)
from sqlmend_retrieval_v1.models import CandidatePassage, OnlineQuery
from sqlmend_retrieval_v1.ranking import CandidateState
from sqlmend_retrieval_v1.reranker import build_corpus_lexical_index


class StepClock:
    def __init__(self, step_seconds: float = 0.001) -> None:
        self.value = 0.0
        self.step_seconds = step_seconds
        self.calls = 0

    def __call__(self) -> float:
        current = self.value
        self.value += self.step_seconds
        self.calls += 1
        return current


def _state(query_number: int, candidate_number: int) -> CandidateState:
    chunk_id = f"q{query_number}-d{candidate_number}"
    return CandidateState(
        passage=CandidatePassage(
            chunk_id=chunk_id,
            dialect="postgresql" if candidate_number == 1 else "mysql",
            version="14",
            version_min="14.0",
            version_max="14.x",
            version_status="range",
            source_type="official_docs",
            title=f"Candidate {candidate_number}",
            section="Diagnostics",
            text=(
                "PostgreSQL relation orders SQLSTATE 42P01 diagnostic"
                if candidate_number == 1
                else "Generic SQL transaction documentation"
            ),
            baseline_rank=candidate_number,
            baseline_score=0.02 - candidate_number * 0.001,
        ),
        bm25_rank=candidate_number,
        dense_rank=None,
    )


def _fixture():
    queries = {
        f"q{number}": OnlineQuery(
            query_id=f"q{number}",
            dialect="postgresql",
            version="14",
            serialized_text=(
                "Dialect: postgresql\n\nVersion: 14\n\n"
                "Question:\nWhy is orders missing?\n\n"
                "Observed error or behavior:\nSQLSTATE: 42P01\n\n"
                "SQL:\nSELECT * FROM orders;"
            ),
            user_problem="Why is orders missing?",
            sql="SELECT * FROM orders;",
            sqlstate="42P01",
        )
        for number in (1, 2)
    }
    candidates = {
        query_id: [_state(number, 1), _state(number, 2)]
        for number, query_id in ((1, "q1"), (2, "q2"))
    }
    corpus = {
        state.passage.chunk_id: state.passage
        for states in candidates.values()
        for state in states
    }
    index = build_corpus_lexical_index(corpus)
    configs = {
        DIALECT_SYSTEM_ID: {
            "run_tag": DIALECT_SYSTEM_ID,
            "output_depth": 2,
            "dialect_bonuses": {
                "compatible": 0.004,
                "related": 0.003,
                "unknown": 0.002,
                "incompatible": 0.0,
            },
        },
        VERSION_SYSTEM_ID: {
            "run_tag": VERSION_SYSTEM_ID,
            "output_depth": 2,
            "version_bonuses": {
                "compatible": 0.002,
                "general": 0.0006,
                "unknown": 0.0004,
                "not_applicable": 0.0004,
                "incompatible": 0.0,
            },
        },
        COMBINED_SYSTEM_ID: {
            "run_tag": COMBINED_SYSTEM_ID,
            "output_depth": 2,
            "dialect_bonuses": {
                "compatible": 0.004,
                "related": 0.003,
                "unknown": 0.002,
                "incompatible": 0.0,
            },
            "version_bonuses": {
                "compatible": 0.005,
                "general": 0.0015,
                "unknown": 0.001,
                "not_applicable": 0.001,
                "incompatible": 0.0,
            },
        },
        FINAL_SYSTEM_ID: {
            "run_tag": FINAL_SYSTEM_ID,
            "output_depth": 2,
            "gamma": 0.001,
        },
    }
    reference = {
        "system_id": FROZEN_SYSTEM_ID,
        "latency_type": "frozen_measured_reference",
        "description": "frozen measured reference",
        "source_artifact": "retrieval/baseline/evaluation/latency.json",
        "query_count": 2,
        "sample_count": 2,
        "total_latency_ms": {
            "mean_ms": 100.0,
            "p50_ms": 90.0,
            "p95_ms": 150.0,
        },
    }
    return candidates, queries, configs, index, reference


def test_latency_summary_uses_linear_percentiles() -> None:
    result = summarize_latency_samples([1.0, 2.0, 3.0, 4.0])
    assert result == {
        "mean_ms": 2.5,
        "p50_ms": 2.5,
        "p95_ms": pytest.approx(3.85),
        "sample_count": 4,
    }
    with pytest.raises(LatencyBenchmarkError, match="cannot be empty"):
        summarize_latency_samples([])
    with pytest.raises(LatencyBenchmarkError, match="finite non-negative"):
        summarize_latency_samples([float("nan")])


def test_loads_frozen_hybrid_as_measured_reference(tmp_path: Path) -> None:
    path = tmp_path / "latency.json"
    path.write_text(
        json.dumps(
            {
                "evaluation_label": "machine-proposed development evaluation",
                "query_count": 2,
                "warm_query_latency": {
                    "hybrid": {
                        "total": {
                            "mean_ms": 100.0,
                            "p50_ms": 90.0,
                            "p95_ms": 150.0,
                            "sample_count": 2,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    reference = load_frozen_hybrid_reference(
        path,
        expected_query_count=2,
        source_artifact="frozen/latency.json",
    )
    assert reference["system_id"] == FROZEN_SYSTEM_ID
    assert reference["latency_type"] == "frozen_measured_reference"
    assert reference["source_artifact"] == "frozen/latency.json"
    assert reference["total_latency_ms"]["p95_ms"] == 150.0


def test_measures_every_query_after_warmup_and_labels_total_estimates() -> None:
    candidates, queries, configs, index, reference = _fixture()
    clock = StepClock()

    result = benchmark_incremental_latency(
        candidates,
        queries,
        configs,
        index,
        reference,
        expected_query_count=2,
        repetitions=1,
        warmup_queries=1,
        clock=clock,
        clock_label="deterministic test clock",
    )

    assert result["schema_version"] == LATENCY_SCHEMA_VERSION
    assert result["each_query_measured_at_least_once"] is True
    assert result["warmup_queries_per_stage"] == 1
    # Four stages x two queries x one repetition x start/end clock calls.
    assert clock.calls == 16
    measured = result["measured_incremental_online_latency"]
    assert set(measured) == {
        "dialect_metadata_rerank",
        "version_metadata_rerank",
        "dialect_version_metadata_rerank",
        "lexical_reranker",
    }
    for stage in measured.values():
        assert stage["latency_type"] == "measured_increment"
        assert stage["sample_count"] == 2
        assert stage["mean_ms"] == pytest.approx(1.0)
        assert stage["p50_ms"] == pytest.approx(1.0)
        assert stage["p95_ms"] == pytest.approx(1.0)

    systems = result["systems"]
    frozen = systems[FROZEN_SYSTEM_ID]
    assert frozen["method"] == "frozen_measured_reference"
    assert frozen["mean_ms"] == 100.0
    assert frozen["incremental"] is None
    for system_id in (DIALECT_SYSTEM_ID, VERSION_SYSTEM_ID, COMBINED_SYSTEM_ID):
        row = systems[system_id]
        assert row["method"] == "estimate"
        assert row["mean_ms"] == pytest.approx(101.0)
        assert row["p50_ms"] == pytest.approx(91.0)
        assert row["p95_ms"] == pytest.approx(151.0)
        assert row["incremental"]["method"] == "measured_increment"
        assert row["incremental"]["mean_ms"] == pytest.approx(1.0)

    final = systems[FINAL_SYSTEM_ID]
    assert final["method"] == "estimate"
    assert final["mean_ms"] == pytest.approx(102.0)
    assert final["p50_ms"] == pytest.approx(92.0)
    assert final["p95_ms"] == pytest.approx(152.0)
    assert final["incremental"]["method"] == (
        "componentwise_estimate_from_measured_increments"
    )
    assert final["incremental"]["mean_ms"] == pytest.approx(2.0)
    assert final["reranker_incremental"]["method"] == "measured_increment"
    assert final["reranker_incremental"]["mean_ms"] == pytest.approx(1.0)


def test_benchmark_requires_each_query_and_a_real_warmup() -> None:
    candidates, queries, configs, index, reference = _fixture()
    with pytest.raises(LatencyBenchmarkError, match="preloaded query count"):
        benchmark_incremental_latency(
            candidates,
            queries,
            configs,
            index,
            reference,
            expected_query_count=3,
            warmup_queries=1,
        )
    with pytest.raises(LatencyBenchmarkError, match="warmup_queries"):
        benchmark_incremental_latency(
            candidates,
            queries,
            configs,
            index,
            reference,
            expected_query_count=2,
            warmup_queries=3,
        )
    with pytest.raises(LatencyBenchmarkError, match="repetitions"):
        benchmark_incremental_latency(
            candidates,
            queries,
            configs,
            index,
            reference,
            expected_query_count=2,
            repetitions=0,
            warmup_queries=1,
        )


def test_writes_fixed_latency_schema(tmp_path: Path) -> None:
    candidates, queries, configs, index, reference = _fixture()
    result = benchmark_incremental_latency(
        candidates,
        queries,
        configs,
        index,
        reference,
        expected_query_count=2,
        warmup_queries=1,
        clock=StepClock(),
    )
    output = write_latency_report(tmp_path / "reports" / "latency.json", result)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded == result
    assert set(loaded["systems"]) == {
        FROZEN_SYSTEM_ID,
        DIALECT_SYSTEM_ID,
        VERSION_SYSTEM_ID,
        COMBINED_SYSTEM_ID,
        FINAL_SYSTEM_ID,
    }
