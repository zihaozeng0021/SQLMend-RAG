"""Chinese Markdown reporting for the Phase 10 paired generation study."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .metrics import NOT_APPLICABLE


REPORT_SCHEMA_VERSION = "sqlmend-generation-report-v1"


METRIC_ROWS = (
    ("Generation Contract Success Rate", "generation_contract_success_rate", "rate"),
    ("Task Success Rate", "task_success_rate", "rate"),
    ("Root Cause Accuracy", "root_cause_accuracy", "rate"),
    ("SQL Repair Correctness", "sql_repair_correctness", "rate"),
    ("Dialect Compatibility", "dialect_compatibility", "rate"),
    ("Version Compatibility", "version_compatibility", "rate"),
    ("Structured Output Validity", "structured_output_validity", "rate"),
    ("Answer Relevance", "answer_relevance", "score"),
    ("Citation Validity", "citation_validity", "rate"),
    ("Citation Coverage", "citation_coverage", "score"),
    ("Faithfulness", "faithfulness", "score"),
    ("Context Precision (qrels rel>=1)", "context_precision", "score"),
    ("Context Query Hit Rate (qrels rel>=1)", "context_query_hit_rate", "rate"),
)


def write_generation_report(
    paths: Any,
    overall: Mapping[str, Any],
    per_query_rows: Sequence[Mapping[str, Any]],
) -> Path:
    """Render and atomically write the formal Markdown report."""

    report_path = Path(getattr(paths, "report", paths))
    markdown = render_generation_report(overall, per_query_rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown)
    temporary.replace(report_path)
    return report_path


def render_generation_report(
    overall: Mapping[str, Any],
    per_query_rows: Sequence[Mapping[str, Any]],
) -> str:
    """Render all required Phase 10 comparisons from published artifacts."""

    _validate_inputs(overall, per_query_rows)
    baseline = overall["systems"]["baseline"]
    generation_v1 = overall["systems"]["generation_v1"]
    paired = overall["paired"]
    judge = overall["judge"]
    artifacts = overall.get("artifacts", {})
    acceptance = overall.get("acceptance", {})
    judge_logical_count = int(judge.get("logical_query_count", overall["query_count"]))
    judge_completed_count = int(judge.get("completed_count", 0))
    judge_failed_count = int(
        judge.get("failed_count", judge_logical_count - judge_completed_count)
    )
    judge_retry_count = int(judge.get("retry_count", 0))
    judge_gate_passed = (
        judge_logical_count == overall["query_count"]
        and judge_completed_count == overall["query_count"]
        and judge_failed_count == 0
    )

    lines = [
        "# Phase 10: Generation Baseline and Generation v1",
        "",
        f"Schema: `{REPORT_SCHEMA_VERSION}`. This report is **{overall['evaluation_label']}**;",
        "The current 250 records and offline reference/qrels are machine-proposed development data,",
        "Not artificial gold, nor the final held-out test result. The failed wrapper always remains in the denominator.",
        "",
        "## Experimentation and Completeness Boundaries",
        "",
        f"- Paired query: {overall['query_count']}; formal result wrapper (with explicit failure record): "
        f"{overall.get('formal_result_wrapper_count', overall['formal_answer_count'])}.",
        f"- Baseline: `{baseline['system_id']}`, does not receive retrieval evidence; Baseline's RAG indicator is `N/A`.",
        f"- Generation v1: `{generation_v1['system_id']}`, only use frozen Retrieval v1 this time Top-5 evidence.",
        f"-offline judge: `{overall['judge']['model_tag']}`, digest "
        f"`{overall['judge']['model_digest']}`, `think=false` (thinking disabled);"
        "Anonymous A/B logic call per query, parity reversed,"
        f"At most {overall['judge']['max_attempts']} attempts.",
        f"- Run seal: Baseline `{overall['generation_seals']['baseline']['sha256']}`;"
        f"Generation v1 `{overall['generation_seals']['generation_v1']['sha256']}`. Both runs are archived before reference/qrels is opened for the first time.",
        "- Online generation output is not written back by reference, annotation evidence or qrels; reference fields only enter this offline evaluation.",
        "- Generation `status=success` only means that the call finally passes the JSON/schema/citation contract, and does not mean that the SQL problem has been corrected; the semantic correctness is only measured by the offline indicators below.",
        "",
        "## Formal generation execution",
        "",
        "| System | Official wrapper | Generation Contract Success | Explicit failure | retries | Recover after retries |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Baseline | {baseline['formal_result_count']} | "
        f"{baseline.get('generation_contract_success_count', baseline['success_count'])} | "
        f"{baseline.get('generation_contract_failure_count', baseline['failure_count'])} | "
        f"{baseline.get('generation_retry_count', 0)} | "
        f"{baseline.get('generation_recovered_after_retry_count', 0)} |",
        f"| Generation v1 | {generation_v1['formal_result_count']} | "
        f"{generation_v1.get('generation_contract_success_count', generation_v1['success_count'])} | "
        f"{generation_v1.get('generation_contract_failure_count', generation_v1['failure_count'])} | "
        f"{generation_v1.get('generation_retry_count', 0)} | "
        f"{generation_v1.get('generation_recovered_after_retry_count', 0)} |",
        "",
        "Attempts/retries are calculated independently from the `attempts` array retained by each official wrapper;"
        "Generation Contract Success does not mean that the SQL semantics are correct.",
        "",
        "## Offline judge execution (project access control)",
        "",
        f"**{'PASS' if judge_gate_passed else 'FAIL'}: judge call success "
        f"{judge_completed_count}/{judge_logical_count}, failure {judge_failed_count},"
        f"retry {judge_retry_count}.**",
        "All 250 records must have a successful judge result; only a judgment record but a judge failure does not meet the project access control.",
        "",
        "## Complete indicator table",
        "",
        "| Indicators | Baseline Closed-Book | Generation v1 Retrieval-v1 RAG | Generation v1 - Baseline |",
        "|---|---:|---:|---:|",
    ]
    for label, key, kind in METRIC_ROWS:
        baseline_value = baseline[key]
        generation_v1_value = generation_v1[key]
        lines.append(
            f"| {label} | {_format_metric(baseline_value, kind)} | "
            f"{_format_metric(generation_v1_value, kind)} | {_format_delta(baseline_value, generation_v1_value, kind)} |"
        )

    lines.extend(
        [
            "",
            "Task Success only counts 1 if root cause, SQL fix, dialect compatibility, and version compatibility are all true at the same time.",
            f"The absolute change of Generation v1 relative to Baseline is **{paired['task_success_percentage_point_delta']:+.2f} percentage points**;"
            f"Main target (at least +10pp): **{'achieved' if paired['success_target']['achieved'] else 'not achieved'}**.",
            "",
            "## Paired per-query comparison",
            "",
            f"See the complete {len(per_query_rows)} row matching results in "
            f"[{_artifact_label(artifacts, 'per_query_comparison')}]({_artifact_link(artifacts, 'per_query_comparison', '../evaluation/per_query_comparison.jsonl')}).",
            "This file retains two system generation status, four task judgments, structured validity, latency, and judge retry query by query,",
            "And citation/context audit for Generation v1; no deletion failure cases.",
            "",
            "| Matching results | Number of queries |",
            "|---|---:|",
            f"| Generation v1 improvement (Baseline Task fail → Generation v1 Task Success) | {paired['generation_v1_improved_count']} |",
            f"| Generation v1 variation (Baseline Task Success → Generation v1 Task fail) | {paired['generation_v1_regressed_count']} |",
            f"| Both Task Success | "
            f"{paired.get('both_task_success_count', paired['both_succeeded_count'])} |",
            f"| Both Task fail | "
            f"{paired.get('neither_task_success_count', paired['neither_succeeded_count'])} |",
            "",
            "## The most obvious case of improvement in Generation v1",
            "",
        ]
    )
    improvements = _rank_improvements(per_query_rows)[:3]
    lines.append(
        f"There are {len(_rank_improvements(per_query_rows))} cases that actually match Baseline Task fail → Generation v1 Task Success;"
        "When there are less than 3 lines, the report structure is retained with clear placeholder lines, and tie or regression will not be passed off as improvement."
    )
    lines.append("")
    lines.extend(
        _case_table(
            improvements,
            minimum_rows=3,
            placeholder="No more real improvement cases",
        )
    )

    lines.extend(
        [
            "",
            "## Cases where Generation v1 did not improve or performed worse",
            "",
        ]
    )
    non_improvements = _rank_non_improvements(per_query_rows)[:3]
    lines.extend(
        _case_table(
            non_improvements,
            minimum_rows=3,
            placeholder="No more unimproved or worsened cases",
        )
    )

    lines.extend(_rag_analysis(per_query_rows))

    baseline_latency = baseline["latency_ms"]
    generation_v1_latency = generation_v1["latency_ms"]
    lines.extend(
        [
            "",
            "## Generation latency",
            "",
            "latency is the end-to-end generation wall time recorded by the official wrapper; judge latency is not mixed into this table.",
            "",
            "| System | Mean (ms) | P50 (ms) | P95 (ms) |",
            "|---|---:|---:|---:|",
            f"| Baseline | {baseline_latency['mean']:.3f} | {baseline_latency['p50']:.3f} | {baseline_latency['p95']:.3f} |",
            f"| Generation v1 | {generation_v1_latency['mean']:.3f} | {generation_v1_latency['p50']:.3f} | {generation_v1_latency['p95']:.3f} |",
            f"| Generation v1 - Baseline | {generation_v1_latency['mean'] - baseline_latency['mean']:+.3f} | "
            f"{generation_v1_latency['p50'] - baseline_latency['p50']:+.3f} | "
            f"{generation_v1_latency['p95'] - baseline_latency['p95']:+.3f} |",
            "",
            "## 500 official results wrapper and provenance",
            "",
            f"- 250 wrappers for Baseline: `{artifacts.get('baseline_answers', 'generation/baseline/runs/baseline_closed_book_dev250.jsonl')}`.",
            f"- 250 wrappers for Generation v1: `{artifacts.get('generation_v1_answers', 'generation/generation-v1/runs/generation_v1_rag_dev250.jsonl')}`.",
            f"- Generation v1 actual Top-5 evidence: `{artifacts.get('generation_v1_evidence', 'generation/generation-v1/prepared_inputs/generation_v1_evidence_top5.jsonl')}`.",
            f"-Anonymous paired judge journal: `{artifacts.get('judgments', 'generation/generation-v1/evaluation/judgments.jsonl')}`.",
            f"- Generation seal: `{overall.get('seal_path', 'generation/generation-v1/evaluation/generation_seal.json')}`.",
            *(
                [f"- Naming migration ledger: `{artifacts['system_naming_migration']}`."]
                if artifacts.get("system_naming_migration")
                else []
            ),
            "",
            "Each answer wrapper comes with input provenance, prompt SHA, exact model provenance, unified retry attempts and wall latency;",
            "So all 500 official result wrappers can be traced back to their own unlabeled inputs and model calls;"
            "which generates `answer=null` for the failed item, but leaves the failure and provenance intact.",
            "",
            "## Acceptance",
            "",
            f"- Engineering: **{_nested_status(acceptance, 'engineering')}**.",
            f"- Evaluation integrity: **{_nested_status(acceptance, 'integrity')}**.",
            f"- Quality target: **{_nested_status(acceptance, 'quality')}**.",
            f"- Phase success: **{'PASS' if acceptance.get('phase_success') else 'FAIL'}**.",
            "",
            "Any failed gate will not trigger reference label modification, failed case deletion, or result overwriting.",
            "",
            "## Rebuild and evaluate from a clean environment",
            "",
            "Execute in the warehouse root directory:",
            "",
            "```powershell",
            "python -m venv .venv-generation-v1",
            ".\\.venv-generation-v1\\Scripts\\python.exe -m pip install --upgrade pip",
            ".\\.venv-generation-v1\\Scripts\\python.exe -m pip install -r generation\\generation-v1\\requirements.txt",
            ".\\.venv-generation-v1\\Scripts\\python.exe -m pip install -e generation\\generation-v1 --no-deps",
            "$env:PYTHONDONTWRITEBYTECODE = '1'",
            "$env:PYTHONPATH = (Resolve-Path 'generation\\generation-v1\\src')",
            ".\\.venv-generation-v1\\Scripts\\python.exe -m sqlmend_generation_v1.cli --root . all --clean",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _rag_analysis(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    judge_failures = sum(row.get("judge_status", "success") != "success" for row in rows)
    baseline_generation_failures = sum(row["baseline"].get("status", "success") != "success" for row in rows)
    generation_v1_generation_failures = sum(row["generation_v1"].get("status", "success") != "success" for row in rows)
    analyzable = [
        row
        for row in rows
        if row.get("judge_status", "success") == "success"
        and row["generation_v1"].get("status", "success") == "success"
    ]
    hit_improved = sum(
        row["generation_v1"]["context_query_hit"] and row["paired"]["task_success_delta"] > 0
        for row in analyzable
    )
    hit_not_improved = sum(
        row["generation_v1"]["context_query_hit"] and row["paired"]["task_success_delta"] <= 0
        for row in analyzable
    )
    miss_count = sum(not row["generation_v1"]["context_query_hit"] for row in analyzable)
    faithful = sum(row["generation_v1"]["faithfulness"] >= 0.8 for row in analyzable)
    low_faithfulness = len(analyzable) - faithful
    valid_but_uncovered = sum(
        row["generation_v1"]["citation_validity"] == 1.0
        and row["generation_v1"]["citation_coverage"] < 0.8
        for row in analyzable
    )
    return [
        "",
        "## Reasons why RAG is valid and invalid",
        "",
        f"- Generation failure separate statistics: Baseline {baseline_generation_failures} items,"
        f"Generation v1 {generation_v1_generation_failures} items; offline judge failure {judge_failures} items."
        f"The following context/evidence-utilization count only analyzes {len(analyzable)} items where both judge and Generation v1 generation are successful,"
        "Do not misattribute call failures to retrieval or evidence utilization.",
        f"- In the query with qrels rel>=1 context hit, {hit_improved} turns into Task Success,"
        f"{hit_not_improved} bars do not form a net improvement. Hit-related passages are a necessary help, but there is no guarantee that the model will take advantage of it.",
        f"Top-5 of {miss_count} queries have no rel>=1 hit; such failures are more likely to come from irrelevant or insufficient retrieval context.",
        f"-{faithful} generation v1 answer's faithfulness ≥ 0.8, {low_faithfulness} less than 0.8;"
        "When relevant context exists but faithfulness is still low, the problem is closer to evidence utilization or model capability.",
        f"- {valid_but_uncovered} answers have no fictitious citation (validity=1) but coverage<0.8;"
        "This shows that citation validity alone cannot prove that key diagnoses and repairs are covered by the evidence.",
        "- Citation Validity assumes vacuous 1.0 for zero references (no fictitious passage ID);"
        "It does not indicate the existence of evidential support and must be interpreted together with citation coverage, generation/judge failure.",
        "-paired case's judge reason and context/citation audit are saved in the per-query artifact,"
        "You can further differentiate between retrieval context, prompts, model SQL capabilities, and evidence utilization.",
        "-Explanation limitation: generation and offline judge use the same Qwen model, and there may be related self-judge bias;"
        "A reference is also a machine-proposed development reference, not artificial gold."
        "These scores are therefore suitable for baseline control and failure analysis and should not be represented as independent human adjudication.",
    ]


def _rank_improvements(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    candidates = [
        row for row in rows if float(row["paired"]["task_success_delta"]) > 0.0
    ]
    return sorted(candidates, key=_improvement_score, reverse=True)


def _rank_non_improvements(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    candidates = [
        row for row in rows if float(row["paired"]["task_success_delta"]) <= 0.0
    ]
    return sorted(candidates, key=_improvement_score)


def _improvement_score(row: Mapping[str, Any]) -> float:
    paired = row["paired"]
    return (
        float(paired["task_success_delta"]) * 100.0
        + float(paired["semantic_component_delta"]) * 10.0
        + float(paired["answer_relevance_delta"])
    )


def _case_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    minimum_rows: int = 0,
    placeholder: str = "No qualifying cases",
) -> list[str]:
    lines = [
        "| Query | Paired outcome | Baseline → Generation v1 task | Context | Judge summary |",
        "|---|---|---:|---:|---|",
    ]
    for row in rows:
        reason = (
            f"Baseline: {row['baseline'].get('judge_reason', '')}; "
            f"Generation v1: {row['generation_v1'].get('judge_reason', '')}"
        )
        lines.append(
            f"| `{_escape_cell(row['query_id'])}` | {row['paired']['outcome']} | "
            f"{int(bool(row['baseline']['task_success']))} → {int(bool(row['generation_v1']['task_success']))} | "
            f"P={row['generation_v1']['context_precision']:.2f}, hit={str(row['generation_v1']['context_query_hit']).lower()} | "
            f"{_escape_cell(_truncate(reason, 220))} |"
        )
    for _ in range(max(0, minimum_rows - len(rows))):
        lines.append(f"| — | {_escape_cell(placeholder)} | — | — | — |")
    return lines


def _validate_inputs(
    overall: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if overall.get("evaluation_label") != "machine-proposed development evaluation":
        raise ValueError("report must retain the machine-proposed development label")
    if not isinstance(overall.get("systems"), Mapping):
        raise ValueError("overall metrics missing systems")
    systems = overall["systems"]
    if not isinstance(systems.get("baseline"), Mapping) or not isinstance(
        systems.get("generation_v1"), Mapping
    ):
        raise ValueError("overall metrics require baseline and generation_v1")
    if systems["baseline"].get("faithfulness") != NOT_APPLICABLE:
        raise ValueError("Baseline faithfulness must be N/A")
    if systems["baseline"].get("context_precision") != NOT_APPLICABLE:
        raise ValueError("Baseline context precision must be N/A")
    if len(rows) != int(overall.get("query_count", -1)):
        raise ValueError("paired rows do not match overall query_count")
    ids = [row.get("query_id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("paired report rows contain duplicate query IDs")


def _format_metric(value: Any, kind: str) -> str:
    if value == NOT_APPLICABLE:
        return NOT_APPLICABLE
    number = float(value)
    if kind == "rate":
        return f"{number * 100.0:.2f}%"
    return f"{number:.4f}"


def _format_delta(baseline: Any, generation_v1: Any, kind: str) -> str:
    if baseline == NOT_APPLICABLE or generation_v1 == NOT_APPLICABLE:
        return NOT_APPLICABLE
    delta = float(generation_v1) - float(baseline)
    if kind == "rate":
        return f"{delta * 100.0:+.2f}pp"
    return f"{delta:+.4f}"


def _artifact_label(artifacts: Mapping[str, Any], key: str) -> str:
    value = artifacts.get(key)
    return Path(value).name if isinstance(value, str) else "per_query_comparison.jsonl"


def _artifact_link(artifacts: Mapping[str, Any], key: str, fallback: str) -> str:
    value = artifacts.get(key)
    if not isinstance(value, str):
        return fallback
    # The report is one directory below the release root, like evaluation/.
    path = Path(value)
    if len(path.parts) >= 2 and path.parts[-2] == "evaluation":
        return f"../evaluation/{path.name}"
    return value.replace("\\", "/")


def _nested_status(value: Mapping[str, Any], key: str) -> str:
    section = value.get(key, {})
    return str(section.get("status", "UNKNOWN")) if isinstance(section, Mapping) else "UNKNOWN"


def _escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _truncate(value: str, length: int) -> str:
    return value if len(value) <= length else value[: length - 1] + "…"


__all__ = [
    "METRIC_ROWS",
    "REPORT_SCHEMA_VERSION",
    "render_generation_report",
    "write_generation_report",
]
