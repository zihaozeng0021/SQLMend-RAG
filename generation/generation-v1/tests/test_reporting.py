from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sqlmend_generation_v1.reporting import (
    render_generation_report,
    write_generation_report,
)


def _system(system_id: str, *, rag: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "system_id": system_id,
        "formal_result_count": 6,
        "success_count": 6,
        "failure_count": 0,
        "generation_contract_success_rate": 1.0,
        "generation_contract_success_count": 6,
        "generation_contract_failure_count": 0,
        "generation_attempt_count": 8,
        "generation_retry_count": 2,
        "generation_recovered_after_retry_count": 2,
        "task_success_rate": 0.5 if not rag else 2 / 3,
        "root_cause_accuracy": 2 / 3 if not rag else 5 / 6,
        "sql_repair_correctness": 0.5 if not rag else 2 / 3,
        "dialect_compatibility": 5 / 6,
        "version_compatibility": 5 / 6,
        "structured_output_validity": 1.0,
        "answer_relevance": 0.7 if not rag else 0.82,
        "latency_ms": {
            "count": 6,
            "mean": 1000.0 if not rag else 1300.0,
            "p50": 900.0 if not rag else 1200.0,
            "p95": 1500.0 if not rag else 1900.0,
        },
    }
    if rag:
        result.update(
            {
                "citation_validity": 1.0,
                "citation_coverage": 0.8,
                "faithfulness": 0.85,
                "context_precision": 0.6,
                "context_query_hit_rate": 5 / 6,
                "context_fully_judged_rate": 1.0,
            }
        )
    else:
        result.update(
            {
                "citation_validity": "N/A",
                "citation_coverage": "N/A",
                "faithfulness": "N/A",
                "context_precision": "N/A",
                "context_query_hit_rate": "N/A",
                "context_fully_judged_rate": "N/A",
            }
        )
    return result


def _row(number: int, delta: int) -> dict[str, Any]:
    g0_success = delta <= 0 and number % 2 == 0
    g1_success = g0_success or delta > 0
    if delta < 0:
        g0_success, g1_success = True, False
    return {
        "query_id": f"DEV{number:04d}",
        "g0": {
            "task_success": g0_success,
            "judge_reason": f"closed-book reason {number}",
        },
        "g1": {
            "task_success": g1_success,
            "judge_reason": f"RAG reason {number}",
            "context_precision": 0.8 if number != 6 else 0.0,
            "context_query_hit": number != 6,
            "faithfulness": 0.9 if number < 5 else 0.5,
            "citation_validity": 1.0,
            "citation_coverage": 0.9 if number < 4 else 0.6,
        },
        "paired": {
            "task_success_delta": delta,
            "semantic_component_delta": delta * 2,
            "answer_relevance_delta": 0.2 if delta > 0 else -0.1,
            "outcome": (
                "g1_improved" if delta > 0 else "g1_regressed" if delta < 0 else "tied"
            ),
        },
    }


def _overall() -> dict[str, Any]:
    return {
        "schema_version": "sqlmend-generation-evaluation-v1",
        "evaluation_label": "machine-proposed development evaluation",
        "query_count": 6,
        "formal_answer_count": 12,
        "generation_seals": {
            "g0": {"sha256": "0" * 64},
            "g1": {"sha256": "1" * 64},
        },
        "seal_path": "generation/generation-v1/evaluation/generation_seal.json",
        "judge": {
            "model": "qwen3.5:4b",
            "model_tag": "qwen3.5:4b",
            "model_digest": "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd",
            "think": False,
            "thinking_disabled": True,
            "max_attempts": 3,
            "logical_query_count": 6,
            "completed_count": 6,
            "failed_count": 0,
            "retry_count": 2,
        },
        "systems": {
            "g0": _system("g0_closed_book", rag=False),
            "g1": _system("g1_retrieval_v1_rag", rag=True),
        },
        "paired": {
            "g1_improved_count": 2,
            "g1_regressed_count": 1,
            "both_succeeded_count": 1,
            "neither_succeeded_count": 2,
            "task_success_percentage_point_delta": 16.6667,
            "success_target": {"achieved": True},
        },
        "artifacts": {
            "g0_answers": "generation/generation-v1/runs/g0_closed_book_dev250.jsonl",
            "g1_answers": "generation/generation-v1/runs/g1_retrieval_v1_rag_dev250.jsonl",
            "g1_evidence": "generation/generation-v1/prepared_inputs/g1_evidence_top5.jsonl",
            "judgments": "generation/generation-v1/evaluation/judgments.jsonl",
            "per_query_comparison": "generation/generation-v1/evaluation/per_query_comparison.jsonl",
        },
        "acceptance": {
            "engineering": {"status": "PASS"},
            "integrity": {"status": "PASS"},
            "quality": {"status": "PASS"},
            "phase_success": True,
        },
    }


def test_report_contains_all_required_comparisons_and_commands() -> None:
    rows = [_row(1, 1), _row(2, 1), _row(3, 0), _row(4, 0), _row(5, -1), _row(6, 0)]
    report = render_generation_report(_overall(), rows)
    assert "Generation Contract Success Rate" in report
    assert "Formal generation execution" in report
    assert "| retries |" in report
    assert "Task Success Rate" in report
    assert "Root Cause Accuracy" in report
    assert "SQL Repair Correctness" in report
    assert "Dialect Compatibility" in report
    assert "Version Compatibility" in report
    assert "Structured Output Validity" in report
    assert "Citation Validity" in report
    assert "Faithfulness" in report
    assert "Context Precision" in report
    assert "+16.67 个百分点" in report
    assert "完整 6 行配对结果" in report
    assert "G1 改善最明显的案例" in report
    assert "G1 没有改善或表现更差的案例" in report
    assert "RAG 有效与无效的原因" in report
    assert "Generation latency" in report
    assert "500 个正式结果 wrapper 与 provenance" in report
    assert "qwen3.5:4b" in report
    assert "think=false" in report
    assert "judge call success 6/6，failure 0，retry 2" in report
    assert "vacuous 1.0" in report
    assert "self-judge" in report
    assert "machine-proposed development reference" in report
    assert "Generation failure 分开统计" in report
    assert report.count("-m sqlmend_generation_v1.cli --root . all --clean") == 1
    assert "python -m sqlmend_generation_v1.cli --root . evaluate" not in report
    # Top and non-improvement sections each retain three case rows.
    assert "DEV0001" in report and "DEV0002" in report and "DEV0005" in report
    assert report.count("| `DEV") == 5
    assert "无更多真实改善案例" in report


def test_write_report_uses_requested_artifact_path(tmp_path: Path) -> None:
    rows = [_row(1, 1), _row(2, 0), _row(3, -1), _row(4, 0), _row(5, 0), _row(6, 0)]
    path = tmp_path / "reports" / "generation_v1_report.md"
    written = write_generation_report(SimpleNamespace(report=path), _overall(), rows)
    assert written == path
    assert path.read_text(encoding="utf-8").startswith("# Phase 10")


def test_report_rejects_turning_closed_book_rag_metric_into_zero() -> None:
    overall = _overall()
    overall["systems"]["g0"]["faithfulness"] = 0.0
    with pytest.raises(ValueError, match="faithfulness must be N/A"):
        render_generation_report(overall, [_row(number, 0) for number in range(1, 7)])


def test_improvement_section_never_promotes_ties_or_regressions() -> None:
    rows = [
        _row(1, -1),
        _row(2, 0),
        _row(3, 0),
        _row(4, -1),
        _row(5, 0),
        _row(6, 0),
    ]
    report = render_generation_report(_overall(), rows)
    improvement_section = report.split("## G1 没有改善或表现更差的案例", 1)[0].split(
        "## G1 改善最明显的案例", 1
    )[1]
    assert (
        "实际符合 G0 Task fail → G1 Task Success 的案例共 0 条"
        in improvement_section
    )
    assert improvement_section.count("无更多真实改善案例") == 3
    assert "g1_regressed" not in improvement_section
