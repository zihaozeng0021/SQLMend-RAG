from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from sqlmend_retrieval_v1.reporting import (
    QUALITY_METRICS,
    ReportBlockedError,
    ReportSources,
    SYSTEM_ORDER,
    generate_retrieval_v1_report,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _metric_row(value: float) -> dict[str, float]:
    return {
        "graded_nDCG@10": value,
        "MRR@10_rel2": value,
        "pooled_Recall@10_rel2": value,
        "HitRate@5_rel2": value,
        "Wrong-Dialect@5": 0.1,
        "Wrong-Version@5": 0.02,
        "Unknown-Version@5": 0.03,
        "Judged@30": 1.0,
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path) -> ReportSources:
    query_ids = [f"q{index}" for index in range(1, 7)]
    chunk_ids = [f"d{index:02d}" for index in range(1, 31)]

    serialized_records: list[dict[str, object]] = []
    dialects = ("postgresql", "mysql", "sqlite", "mariadb", "duckdb", "postgresql")
    for query_id, dialect in zip(query_ids, dialects, strict=True):
        serialized = (
            f"Dialect: {dialect}\n\nVersion: 16.2\n\nQuestion:\n"
            f"Safe problem for {query_id} | with markdown\n\nSQL:\nSELECT 1;"
        )
        serialized_records.append(
            {
                "query_id": query_id,
                "source_fields_used": ["dialect", "version", "user_problem", "sql"],
                "serialized_text": serialized,
                "serialized_text_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "serializer_version": "sqlmend-query-v1",
            }
        )
    serialized_queries = tmp_path / "safe_queries.jsonl"
    _write_jsonl(serialized_queries, serialized_records)

    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(
        corpus,
        [
            {
                "chunk_id": chunk_id,
                "title": f"Document {chunk_id}",
                "dialect": "postgresql" if index % 2 else "mysql",
                "version": "16.2",
                "version_status": "exact",
                "text": f"Allowed candidate passage {chunk_id}",
                # The display projection must never copy unrelated corpus fields.
                "annotation_evidence": "CORPUS_SECRET_MUST_NOT_APPEAR",
            }
            for index, chunk_id in enumerate(chunk_ids, start=1)
        ],
    )

    qrels = tmp_path / "qrels.txt"
    qrel_lines = []
    for query_id in query_ids:
        for chunk_id in chunk_ids:
            relevance = 2 if chunk_id == "d01" else 0
            qrel_lines.append(f"{query_id} 0 {chunk_id} {relevance}\n")
    qrels.write_text("".join(qrel_lines), encoding="utf-8")

    run_paths: dict[str, Path] = {}
    for system_id in reversed(SYSTEM_ORDER):
        path = tmp_path / f"{system_id}.trec"
        lines: list[str] = []
        for query_id in query_ids:
            order = list(chunk_ids)
            if system_id == SYSTEM_ORDER[-1]:
                order.reverse()
            for rank, chunk_id in enumerate(order, start=1):
                lines.append(
                    f"{query_id} Q0 {chunk_id} {rank} {1.0 / (60 + rank):.12f} {system_id}\n"
                )
        path.write_text("".join(lines), encoding="utf-8")
        run_paths[system_id] = path

    overall_metrics = tmp_path / "overall.json"
    _write_json(
        overall_metrics,
        {
            "schema_version": "test-overall-v1",
            "evaluation_label": "machine-proposed development evaluation",
            "systems": {
                system_id: _metric_row(0.30 + index * 0.01)
                for index, system_id in enumerate(SYSTEM_ORDER)
            },
        },
    )

    slice_rows: list[dict[str, object]] = []
    for system_index, system_id in enumerate(SYSTEM_ORDER):
        for slice_name, slice_value, query_count in (
            ("case_flag", "dialect-sensitive", 4),
            ("case_flag", "version-sensitive", 3),
            *(("dialect", dialect, 1) for dialect in ("postgresql", "mysql", "sqlite", "mariadb", "duckdb")),
        ):
            slice_rows.append(
                {
                    "system_id": system_id,
                    "slice_name": slice_name,
                    "slice_value": slice_value,
                    "source_field": "offline-test-slice",
                    "query_count": query_count,
                    **_metric_row(0.20 + system_index * 0.01),
                }
            )
    slice_metrics = tmp_path / "slices.csv"
    _write_csv(
        slice_metrics,
        ["system_id", "slice_name", "slice_value", "source_field", "query_count", *QUALITY_METRICS],
        slice_rows,
    )

    baseline = {"q1": 0.0, "q2": 0.1, "q3": 0.2, "q4": 1.0, "q5": 0.9, "q6": 0.8}
    final = {"q1": 1.0, "q2": 0.8, "q3": 0.6, "q4": 0.0, "q5": 0.1, "q6": 0.2}
    per_query_rows: list[dict[str, object]] = []
    for system_id in SYSTEM_ORDER:
        for query_id in query_ids:
            value = (
                baseline[query_id]
                if system_id == SYSTEM_ORDER[0]
                else final[query_id]
                if system_id == SYSTEM_ORDER[-1]
                else 0.4
            )
            per_query_rows.append(
                {
                    "system_id": system_id,
                    "query_id": query_id,
                    "graded_nDCG@10": value,
                }
            )
    per_query_metrics = tmp_path / "per_query.csv"
    _write_csv(
        per_query_metrics,
        ["system_id", "query_id", "graded_nDCG@10"],
        per_query_rows,
    )

    latency = tmp_path / "latency.json"
    systems_latency: dict[str, object] = {}
    for index, system_id in enumerate(SYSTEM_ORDER):
        systems_latency[system_id] = {
            "method": "warm CPU total",
            "mean_ms": 100.0 + index,
            "p50_ms": 90.0 + index,
            "p95_ms": 140.0 + index,
            "incremental": {
                "mean_ms": float(index),
                "p50_ms": float(index) / 2.0,
                "p95_ms": float(index) * 1.5,
            },
        }
    _write_json(latency, {"schema_version": "test-latency-v1", "systems": systems_latency})

    acceptance = tmp_path / "acceptance.json"
    _write_json(
        acceptance,
        {
            "retrieval_quality_status": "PASS",
            "status": {"phase7": "PASS", "phase8": "PASS", "phase9": "PASS", "final": "PASS"},
            "phase7": {
                "dialect gate": {
                    "description": "Wrong-dialect reduction",
                    "observed": 0.4,
                    "required_minimum": 0.3,
                    "passed": True,
                }
            },
            "phase8": {
                "version gate": {
                    "description": "Wrong-version reduction",
                    "observed": 0.5,
                    "required_minimum": 0.3,
                    "passed": True,
                }
            },
            "phase9": {
                "ndcg_delta": 0.01,
                "reranker gate": {"passed": True},
            },
            "final": {
                "quality gate": {
                    "description": "Final nDCG gain",
                    "observed": 0.02,
                    "required_minimum": 0.02,
                    "passed": True,
                }
            },
        },
    )

    evaluation_status = tmp_path / "evaluation_status.json"
    _write_json(
        evaluation_status,
        {
            "evaluation_integrity_status": "PASS",
            "retrieval_quality_status": "PASS",
            "machine_proposed_development_only": True,
            "Judged@30": 1.0,
        },
    )
    return ReportSources(
        overall_metrics=overall_metrics,
        slice_metrics=slice_metrics,
        per_query_metrics=per_query_metrics,
        runs=run_paths,
        serialized_queries=serialized_queries,
        corpus=corpus,
        qrels=qrels,
        latency=latency,
        acceptance=acceptance,
        evaluation_status=evaluation_status,
    )


def test_report_contains_required_comparisons_cases_latency_and_scope(tmp_path: Path) -> None:
    sources = _fixture(tmp_path)
    output = tmp_path / "nested" / "report.md"

    report = generate_retrieval_v1_report(sources, output_path=output)

    assert output.read_text(encoding="utf-8") == report
    assert "machine-proposed development evaluation" in report
    assert "not human gold" in report
    assert "not a final held-out test result" in report
    assert "Overall five-system comparison" in report
    assert "Dialect-sensitive and version-sensitive slices" in report
    assert "Per-dialect slices" in report
    assert all(system_id in report for system_id in SYSTEM_ORDER)
    assert report.count("### Success case ") == 3
    assert report.count("### Failure case ") == 3
    assert "Safe problem for q1 \\| with markdown" in report
    assert "Offline relevance grade" in report
    assert "Reranking overhead versus dialect+version-aware retrieval" in report
    assert "mean +1.000 ms, P50 +1.000 ms, P95 +1.000 ms" in report
    assert "phase7=PASS, phase8=PASS, phase9=PASS, final=PASS" in report
    assert "CORPUS_SECRET_MUST_NOT_APPEAR" not in report


def test_report_is_deterministic(tmp_path: Path) -> None:
    sources = _fixture(tmp_path)

    first = generate_retrieval_v1_report(sources)
    second = generate_retrieval_v1_report(sources)

    assert first == second


def test_unjudged_top30_blocks_report_before_output(tmp_path: Path) -> None:
    sources = _fixture(tmp_path)
    qrel_lines = sources.qrels.read_text(encoding="utf-8").splitlines(keepends=True)
    sources.qrels.write_text(
        "".join(line for line in qrel_lines if not line.startswith("q1 0 d30 ")),
        encoding="utf-8",
    )
    output = tmp_path / "must_not_exist.md"

    with pytest.raises(ReportBlockedError, match="unjudged"):
        generate_retrieval_v1_report(sources, output_path=output)

    assert not output.exists()


def test_serialized_query_contract_rejects_extra_fields(tmp_path: Path) -> None:
    sources = _fixture(tmp_path)
    records = [json.loads(line) for line in sources.serialized_queries.read_text(encoding="utf-8").splitlines()]
    records[0]["reference_fix"] = "QUERY_SECRET_MUST_NOT_APPEAR"
    _write_jsonl(sources.serialized_queries, records)
    output = tmp_path / "must_not_exist.md"

    with pytest.raises(ValueError, match="outside the safe artifact contract"):
        generate_retrieval_v1_report(sources, output_path=output)

    assert not output.exists()
