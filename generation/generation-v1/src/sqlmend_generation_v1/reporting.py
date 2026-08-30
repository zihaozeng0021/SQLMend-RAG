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
        "# Phase 10：Generation Baseline 与 Generation v1",
        "",
        f"Schema: `{REPORT_SCHEMA_VERSION}`。本报告是 **{overall['evaluation_label']}**；",
        "当前 250 条记录及离线 reference/qrels 均为 machine-proposed development data，",
        "不是人工 gold，也不是最终 held-out test 结果。失败 wrapper 始终保留在分母中。",
        "",
        "## 实验与完整性边界",
        "",
        f"- 配对查询：{overall['query_count']}；正式结果 wrapper（含明确失败记录）："
        f"{overall.get('formal_result_wrapper_count', overall['formal_answer_count'])}。",
        f"- Baseline：`{baseline['system_id']}`，不接收 retrieval evidence；Baseline 的 RAG 指标为 `N/A`。",
        f"- Generation v1：`{generation_v1['system_id']}`，只使用冻结 Retrieval v1 本次 Top-5 evidence。",
        f"- 离线 judge：`{overall['judge']['model_tag']}`，digest "
        f"`{overall['judge']['model_digest']}`，`think=false`（thinking disabled）；"
        "每 query 一次匿名 A/B 逻辑调用，奇偶反转，"
        f"最多 {overall['judge']['max_attempts']} 次 attempt。",
        f"- Run seal：Baseline `{overall['generation_seals']['baseline']['sha256']}`；"
        f"Generation v1 `{overall['generation_seals']['generation_v1']['sha256']}`。两个 run 在 reference/qrels 首次打开前封存。",
        "- 在线 generation 输出没有被 reference、annotation evidence 或 qrels 回写；reference 字段只进入此离线评估。",
        "- Generation `status=success` 只表示调用最终通过 JSON/schema/citation 合同，不表示 SQL 问题已修对；语义正确性只由下方离线指标衡量。",
        "",
        "## Formal generation execution",
        "",
        "| 系统 | 正式 wrapper | Generation Contract Success | 明确失败 | retries | 重试后恢复 |",
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
        "Attempts/retries 由每条正式 wrapper 保留的 `attempts` 数组独立复算；"
        "Generation Contract Success 不代表 SQL 语义正确。",
        "",
        "## Offline judge execution（工程门禁）",
        "",
        f"**{'PASS' if judge_gate_passed else 'FAIL'}：judge call success "
        f"{judge_completed_count}/{judge_logical_count}，failure {judge_failed_count}，"
        f"retry {judge_retry_count}。**",
        "全部 250 条都必须得到成功 judge 结果；只有 judgment record 而 judge 失败，不满足工程门禁。",
        "",
        "## 完整指标表",
        "",
        "| 指标 | Baseline Closed-Book | Generation v1 Retrieval-v1 RAG | Generation v1 - Baseline |",
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
            "Task Success 只在根因、SQL 修复、dialect 兼容性和 version 兼容性四项同时为真时计 1。",
            f"Generation v1 相对 Baseline 的绝对变化为 **{paired['task_success_percentage_point_delta']:+.2f} 个百分点**；"
            f"主要目标（至少 +10pp）：**{'达到' if paired['success_target']['achieved'] else '未达到'}**。",
            "",
            "## Paired per-query comparison",
            "",
            f"完整 {len(per_query_rows)} 行配对结果见 "
            f"[{_artifact_label(artifacts, 'per_query_comparison')}]({_artifact_link(artifacts, 'per_query_comparison', '../evaluation/per_query_comparison.jsonl')})。",
            "该文件逐 query 保留两系统 generation status、四项任务判断、structured validity、latency、judge retry，",
            "以及 Generation v1 的 citation/context audit；没有删除失败案例。",
            "",
            "| 配对结果 | 查询数 |",
            "|---|---:|",
            f"| Generation v1 改善（Baseline Task fail → Generation v1 Task Success） | {paired['generation_v1_improved_count']} |",
            f"| Generation v1 变差（Baseline Task Success → Generation v1 Task fail） | {paired['generation_v1_regressed_count']} |",
            f"| 两者均 Task Success | "
            f"{paired.get('both_task_success_count', paired['both_succeeded_count'])} |",
            f"| 两者均 Task fail | "
            f"{paired.get('neither_task_success_count', paired['neither_succeeded_count'])} |",
            "",
            "## Generation v1 改善最明显的案例",
            "",
        ]
    )
    improvements = _rank_improvements(per_query_rows)[:3]
    lines.append(
        f"实际符合 Baseline Task fail → Generation v1 Task Success 的案例共 {len(_rank_improvements(per_query_rows))} 条；"
        "不足 3 条时以明确占位行保留报告结构，不会把 tie 或 regression 冒充 improvement。"
    )
    lines.append("")
    lines.extend(
        _case_table(
            improvements,
            minimum_rows=3,
            placeholder="无更多真实改善案例",
        )
    )

    lines.extend(
        [
            "",
            "## Generation v1 没有改善或表现更差的案例",
            "",
        ]
    )
    non_improvements = _rank_non_improvements(per_query_rows)[:3]
    lines.extend(
        _case_table(
            non_improvements,
            minimum_rows=3,
            placeholder="无更多未改善或变差案例",
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
            "latency 是正式 wrapper 记录的端到端 generation wall time；judge latency 不混入此表。",
            "",
            "| 系统 | Mean (ms) | P50 (ms) | P95 (ms) |",
            "|---|---:|---:|---:|",
            f"| Baseline | {baseline_latency['mean']:.3f} | {baseline_latency['p50']:.3f} | {baseline_latency['p95']:.3f} |",
            f"| Generation v1 | {generation_v1_latency['mean']:.3f} | {generation_v1_latency['p50']:.3f} | {generation_v1_latency['p95']:.3f} |",
            f"| Generation v1 - Baseline | {generation_v1_latency['mean'] - baseline_latency['mean']:+.3f} | "
            f"{generation_v1_latency['p50'] - baseline_latency['p50']:+.3f} | "
            f"{generation_v1_latency['p95'] - baseline_latency['p95']:+.3f} |",
            "",
            "## 500 个正式结果 wrapper 与 provenance",
            "",
            f"- Baseline 的 250 个 wrapper：`{artifacts.get('baseline_answers', 'generation/baseline/runs/baseline_closed_book_dev250.jsonl')}`。",
            f"- Generation v1 的 250 个 wrapper：`{artifacts.get('generation_v1_answers', 'generation/generation-v1/runs/generation_v1_rag_dev250.jsonl')}`。",
            f"- Generation v1 实际 Top-5 evidence：`{artifacts.get('generation_v1_evidence', 'generation/generation-v1/prepared_inputs/generation_v1_evidence_top5.jsonl')}`。",
            f"- 匿名配对 judge journal：`{artifacts.get('judgments', 'generation/generation-v1/evaluation/judgments.jsonl')}`。",
            f"- Generation seal：`{overall.get('seal_path', 'generation/generation-v1/evaluation/generation_seal.json')}`。",
            *(
                [f"- 命名迁移 ledger：`{artifacts['system_naming_migration']}`。"]
                if artifacts.get("system_naming_migration")
                else []
            ),
            "",
            "每个 answer wrapper 自带 input provenance、prompt SHA、exact model provenance、统一 retry attempts 和 wall latency；",
            "因此 500 个正式结果 wrapper 都可以回溯到自己的无标签输入和模型调用；"
            "其中生成失败项的 `answer=null`，但仍保留完整 failure 与 provenance。",
            "",
            "## Acceptance",
            "",
            f"- Engineering：**{_nested_status(acceptance, 'engineering')}**。",
            f"- Evaluation integrity：**{_nested_status(acceptance, 'integrity')}**。",
            f"- Quality target：**{_nested_status(acceptance, 'quality')}**。",
            f"- Phase success：**{'PASS' if acceptance.get('phase_success') else 'FAIL'}**。",
            "",
            "任何失败门禁都不会触发 reference label 修改、失败案例删除或结果覆盖。",
            "",
            "## 从干净环境重新生成与评估",
            "",
            "在仓库根目录执行：",
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
        "## RAG 有效与无效的原因",
        "",
        f"- Generation failure 分开统计：Baseline {baseline_generation_failures} 条，"
        f"Generation v1 {generation_v1_generation_failures} 条；offline judge failure {judge_failures} 条。"
        f"以下 context/evidence-utilization 计数只分析 judge 与 Generation v1 generation 都成功的 {len(analyzable)} 条，"
        "不会把调用失败误归因于 retrieval 或 evidence utilization。",
        f"- 在 qrels rel>=1 context hit 的查询中，{hit_improved} 条转为 Task Success，"
        f"{hit_not_improved} 条没有形成净改善。命中相关 passage 是必要帮助，但不保证模型会利用它。",
        f"- {miss_count} 条查询的 Top-5 没有 rel>=1 hit；这类失败更可能来自 retrieval context 不相关或不足。",
        f"- {faithful} 条 Generation v1 answer 的 faithfulness ≥ 0.8，{low_faithfulness} 条低于 0.8；"
        "相关 context 已存在但 faithfulness 仍低时，问题更接近 evidence utilization 或模型能力。",
        f"- {valid_but_uncovered} 条答案没有虚构 citation（validity=1）但 coverage<0.8；"
        "这说明 citation validity 单独不能证明关键诊断与修复都被证据覆盖。",
        "- Citation Validity 对零引用采用 vacuous 1.0（没有虚构 passage ID）；"
        "它不表示存在证据支持，必须与 Citation Coverage、generation/judge failure 一起解释。",
        "- paired case 的 judge reason 与 context/citation audit 保存在 per-query artifact，"
        "可进一步区分 retrieval context、prompt、模型 SQL 能力和 evidence utilization。",
        "- 解释限制：generation 与 offline judge 使用同一个 Qwen 模型，可能存在相关的 self-judge 偏差；"
        "reference 也是 machine-proposed development reference，而非人工 gold。"
        "因此这些分数适合 baseline 对照与失败分析，不应被表述为独立人工裁决。",
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
    placeholder: str = "无符合条件的案例",
) -> list[str]:
    lines = [
        "| Query | Paired outcome | Baseline → Generation v1 task | Context | Judge 摘要 |",
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
