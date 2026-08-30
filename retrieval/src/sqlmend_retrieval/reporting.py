"""Deterministic provenance, baseline, failure, manifest, and completion reports."""

from __future__ import annotations

import csv
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml

from .hashing import (
    canonical_json_sha256,
    sha256_file,
    sha256_tree,
    snapshot_release_source,
)
from .paths import ProjectPaths

DEVELOPMENT_LABEL = "machine-proposed development evaluation"

COMPLETE_EVALUATION_HASH_FIELDS: tuple[tuple[str, str], ...] = (
    ("per_query_metrics_sha256", "per_query_metrics.csv"),
    ("slice_metrics_sha256", "slice_metrics.csv"),
    ("confidence_intervals_sha256", "confidence_intervals.json"),
    ("pairwise_differences_sha256", "pairwise_differences.json"),
    ("complementarity_report_sha256", "complementarity_report.json"),
)
HUMAN_REPORT_HASH_FIELDS: tuple[tuple[str, str], ...] = (
    ("baseline_report_sha256", "baseline_report.md"),
    ("failure_analysis_sha256", "failure_analysis.md"),
    ("provenance_audit_sha256", "provenance_audit.md"),
    ("completion_report_sha256", "completion_report.md"),
)

# These were temporary names from an earlier local reproduction attempt.  A
# recursive inventory must not promote them into the formal artifact contract.
LEGACY_REPRODUCTION_ARTIFACTS = frozenset(
    {
        "reproduction/annotation_bm25_reproduced.trec",
        "reproduction/annotation_dense_reproduced.trec",
        "reproduction/annotation_hybrid_reproduced.trec",
        "reproduction/annotation_hybrid_rrf_reproduced.trec",
    }
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def audit_annotation_retrievers(paths: ProjectPaths) -> dict[str, Any]:
    """Independently reproduce saved annotation rankings and compare them."""
    from .reproduction import reproduce_annotation_retrievers

    config_path = paths.annotation / "provenance" / "retrieval_config.json"
    model_path = paths.annotation / "provenance" / "embedding_model.json"
    config = read_json(config_path, {})
    model = read_json(model_path, {})
    pool_summary = read_json(paths.pool_expansion / "pool_expansion_summary.json", {})
    cached = read_json(paths.reproduction / "reproduction_report.json", {})
    expected_input_hashes = {
        "implementation_sha256": sha256_file(
            Path(__file__).with_name("reproduction.py")
        ),
        "corpus_sha256": sha256_file(paths.corpus),
        "queries_sha256": sha256_file(paths.queries),
        "stored_runs_sha256": sha256_file(
            paths.annotation / "provenance" / "retrieval_runs.jsonl"
        ),
        "candidate_pools_sha256": sha256_file(paths.candidate_pools),
        "retrieval_config_sha256": sha256_file(config_path),
        "embedding_model_sha256": sha256_file(model_path),
        "snapshot_manifest_sha256": model.get("snapshot_manifest_sha256"),
    }
    cached_inputs = cached.get("inputs") if isinstance(cached, dict) else None
    cached_systems = cached.get("systems", {}) if isinstance(cached, dict) else {}
    expected_run_paths = {
        "bm25": paths.reproduction / "bm25_annotation_reproduced.trec",
        "dense": paths.reproduction / "dense_annotation_reproduced.trec",
        "hybrid_rrf": paths.reproduction / "hybrid_annotation_reproduced.trec",
    }
    cache_is_complete_attempt = (
        isinstance(cached_systems, dict)
        and set(cached_systems) == set(expected_run_paths)
        and cached.get("attempt_completed") is True
        and isinstance(cached.get("preflight_validation"), dict)
        and cached["preflight_validation"].get("status") in {"PASS", "FAIL"}
        and all(
            isinstance(cached_systems[system], dict)
            and cached_systems[system].get("status")
            in {"PASS", "PARTIAL", "NOT_REPRODUCIBLE"}
            for system in expected_run_paths
        )
    )
    reproduced_runs_valid = cache_is_complete_attempt and all(
        (
            cached_systems[system].get("status") == "NOT_REPRODUCIBLE"
            and not cached_systems[system].get("reproduced_run_sha256")
            and not expected_run_paths[system].exists()
        )
        or (
            cached_systems[system].get("status") in {"PASS", "PARTIAL"}
            and expected_run_paths[system].is_file()
            and cached_systems[system].get("reproduced_run_sha256")
            == sha256_file(expected_run_paths[system])
        )
        for system in expected_run_paths
    )
    if cached_inputs == expected_input_hashes and reproduced_runs_valid:
        core = {
            key: cached[key]
            for key in (
                "schema_version",
                "attempt_completed",
                "annotation_reproduction_status",
                "empirical_ranking_reproduction_status",
                "provenance_completeness_status",
                "provenance_limitations",
                "historical_query_contains_annotation_only_fields",
                "historical_query_is_never_used_by_formal_baselines",
                "preflight_validation",
                "reproduction_runtime",
                "inputs",
                "systems",
            )
        }
    else:
        try:
            core = reproduce_annotation_retrievers(paths)
        except Exception as exc:
            # A failed preflight/reproduction attempt must not inherit TREC
            # evidence from a prior successful run under the same filenames.
            for stale_run in expected_run_paths.values():
                stale_run.unlink(missing_ok=True)
            # Provenance uncertainty must be reported precisely, but §5.1
            # explicitly says it must not block the independent formal
            # baselines.  Engineering validation still checks the audit report
            # exists and retains this failure evidence.
            core = {
                "schema_version": "sqlmend-annotation-reproduction-v1",
                "attempt_completed": True,
                "annotation_reproduction_status": "NOT_REPRODUCIBLE",
                "empirical_ranking_reproduction_status": "NOT_REPRODUCIBLE",
                "provenance_completeness_status": "NOT_REPRODUCIBLE",
                "provenance_limitations": [
                    f"independent reproduction failed: {type(exc).__name__}: {exc}"
                ],
                "historical_query_contains_annotation_only_fields": True,
                "historical_query_is_never_used_by_formal_baselines": True,
                "preflight_validation": {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                "reproduction_runtime": {
                    "python_version": platform.python_version(),
                    "operating_system": platform.platform(),
                },
                "inputs": expected_input_hashes,
                "systems": {
                    system: {
                        "status": "NOT_REPRODUCIBLE",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    for system in ("bm25", "dense", "hybrid_rrf")
                },
            }
    result = {
        **core,
        "evaluation_label": DEVELOPMENT_LABEL,
        "machine_proposed_development_only": True,
        "method": "independent recomputation from frozen corpus/cases and recorded historical configuration; saved rankings used only after retrieval for comparison",
        "available_configuration": {
            "bm25": config.get("bm25"),
            "dense": config.get("dense"),
            "pooling": config.get("pooling"),
            "model_snapshot": {
                "resolved_repository": model.get("resolved_repository"),
                "resolved_revision": model.get("resolved_revision"),
                "snapshot_manifest_sha256": model.get("snapshot_manifest_sha256"),
            },
        },
        "formal_baseline_independence": {
            "bm25": "rank_bm25 BM25Okapi k1=1.5 with strict user-field serializer",
            "dense": "pinned intfloat/e5-base-v2 zero-shot exact CPU search",
            "hybrid": "two-channel fixed RRF k=60",
            "uses_candidate_pool_ranks": False,
            "uses_qrels_during_search": False,
        },
        "formal_results_outside_current_judgment_pool": {
            "pool_audit_available": bool(pool_summary),
            "unique_query_chunk_pairs": pool_summary.get("pool_expansion_record_count"),
            "top30_occurrences": pool_summary.get("unjudged_top30_occurrence_count"),
            "pool_expansion_required": pool_summary.get("pool_expansion_required"),
        },
    }
    paths.reproduction.mkdir(parents=True, exist_ok=True)
    write_json(paths.reproduction / "reproduction_report.json", result)
    system_lines: list[str] = []
    for system, detail in result["systems"].items():
        metrics = detail.get("comparison_metrics", {})
        system_lines.extend(
            [
                f"## {system}",
                "",
                f"- 状态：`{detail.get('status')}`",
                f"- 可获得的历史配置：`{json.dumps(detail.get('configuration'), ensure_ascii=False, sort_keys=True)}`",
                f"- 独立重算 run SHA-256：`{detail.get('reproduced_run_sha256')}`",
                f"- exact top-30 sequence match：`{metrics.get('exact_top30_sequence_match_rate')}`",
                f"- exact top-30 set match：`{metrics.get('exact_top30_set_match_rate')}`",
                f"- mean overlap / Jaccard / RBO：`{metrics.get('mean_top30_set_overlap')}` / `{metrics.get('mean_jaccard_at_30')}` / `{metrics.get('mean_reciprocal_rank_biased_overlap')}`",
                f"- mean Kendall on common docs：`{metrics.get('mean_kendall_correlation_on_common_documents')}`",
                f"- out-of-pool pairs / missing stored docs：`{metrics.get('out_of_pool_query_chunk_pair_count')}` / `{metrics.get('missing_stored_documents')}`",
                f"- score differences：`{metrics.get('score_differences')}`（历史保存 run 无 score）",
                f"- 错误或限制：`{detail.get('error') or detail.get('reason')}`",
                "",
            ]
        )
    report = f"""# 标注阶段检索器来源追踪审计

数据性质：**{DEVELOPMENT_LABEL}**。

Recall 语义声明：任何 Recall 都只能称为 **pooled Recall**；本来源审计本身不发布检索质量指标。

状态：`{result['annotation_reproduction_status']}`

## 识别与审计方法

标注阶段系统由受保护的 `provenance/retrieval_config.json`、`provenance/embedding_model.json`、`provenance/retrieval_runs.jsonl` 与 `candidate_pools.jsonl` 共同识别。本审计从冻结 corpus/cases 和历史配置独立重算排名；保存的历史 run 只在重算完成后用于比较，candidate pool 只用于 out-of-pool 审计，二者都不是重算排名的输入。

历史 query 构造包含 `expected_behavior` 等 annotation-only 字段，这是必须披露的标注基础设施循环性风险；这些字段仅用于复现来源，绝不进入正式 baselines。审计输入哈希为：

```json
{json.dumps(result.get('inputs', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## 可获得设置与独立复现结果

```json
{json.dumps(result.get('available_configuration', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

{chr(10).join(system_lines)}

## 缺失信息与限制

来源完整性状态：`{result.get('provenance_completeness_status')}`。明确记录的限制：`{json.dumps(result.get('provenance_limitations', []), ensure_ascii=False, sort_keys=True)}`。某个系统显示 `NOT_REPRODUCIBLE` 时，其错误或依赖原因已逐系统列出；不能把其余系统的成功推断成该系统也成功。历史保存 run 没有 score，因此只能核验排名，不能核验历史浮点 score。

## 与正式 baselines 的隔离

正式 BM25 使用 `rank_bm25`、k1=1.5 与严格用户字段 serializer；正式 dense 使用固定 revision 的 `intfloat/e5-base-v2`、CPU exact search；正式 hybrid 只融合这两套正式 run 的 rank，固定 RRF k=60。正式检索入口不读取 qrels、candidate-pool ranks 或 annotation evidence，任何历史 ranking 都未被复制进正式 run。

## 现有 pool 之外的正式结果

正式 run 落在现有 judgment pool 之外的唯一 query/chunk 对数：`{pool_summary.get('pool_expansion_record_count')}`；top-30 未判定出现次数：`{pool_summary.get('unjudged_top30_occurrence_count')}`。若值为 `None`，说明来源审计发生在正式 pool audit 之前，最终化阶段会重新生成本报告。
"""
    (paths.reports / "provenance_audit.md").parent.mkdir(parents=True, exist_ok=True)
    (paths.reports / "provenance_audit.md").write_text(report, encoding="utf-8", newline="\n")
    return result


def _first_relevant_rank(entries: list[Any], qrels: dict[str, dict[str, int]], threshold: int = 2) -> int | None:
    if not entries:
        return None
    query_id = entries[0].query_id
    judgments = qrels.get(query_id, {})
    ranks = [entry.rank for entry in entries if judgments.get(entry.chunk_id) == threshold]
    return min(ranks) if ranks else None


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    """Read a strict JSONL artifact used as report evidence."""

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"Blank JSONL line {line_number}: {path}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} is not an object: {path}")
            records.append(value)
    return records


def _excerpt(value: Any, limit: int = 280) -> str:
    """Return a deterministic, single-line passage excerpt for Markdown."""

    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return json.dumps(text, ensure_ascii=False)


def _display_rank(value: int | None) -> str:
    return str(value) if value is not None else "未在 top-30 命中显式 rel=2"


def _query_diagnostic_tokens(query: dict[str, Any]) -> list[str]:
    """Extract exact, inspectable diagnostic tokens without inferring causality."""

    tokens: list[str] = []
    for field in ("sqlstate", "error_code", "error_symbol"):
        value = query.get(field)
        if isinstance(value, str) and value.strip():
            tokens.append(value.strip())
    sql = str(query.get("sql") or "")
    for operator in ("->>", "->", "::", "<=", ">=", "<>", "!="):
        if operator in sql:
            tokens.append(operator)
    tokens.extend(re.findall(r"\b[A-Za-z_][A-Za-z0-9_$]*\s*(?=\()", sql))
    tokens.extend(
        token
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_$]*\b", sql)
        if "_" in token
    )
    return list(dict.fromkeys(token for token in tokens if token))[:12]


def generate_failure_analysis(
    paths: ProjectPaths,
    runs: dict[str, list[Any]],
    qrels: dict[str, dict[str, int]],
    queries: list[dict[str, Any]],
    corpus_records: list[dict[str, Any]],
) -> None:
    """Write the section-24 evidence catalog from actual runs and passages."""

    query_map = {record["query_id"]: record for record in queries}
    corpus_map = {record["chunk_id"]: record for record in corpus_records}
    if not paths.serialized_queries.is_file():
        raise FileNotFoundError(
            "Failure analysis requires the frozen serialized-query audit artifact: "
            f"{paths.serialized_queries}"
        )
    serialized_records = _read_jsonl_objects(paths.serialized_queries)
    serialized_map = {record.get("query_id"): record for record in serialized_records}
    missing_serialized = sorted(set(query_map).difference(serialized_map))
    if missing_serialized:
        raise ValueError(
            "Failure analysis cannot substitute raw or annotation-only text for serialized "
            f"queries; missing query IDs: {missing_serialized[:10]!r}"
        )

    grouped: dict[str, dict[str, list[Any]]] = {}
    for system, entries in runs.items():
        by_query: dict[str, list[Any]] = defaultdict(list)
        for entry in entries:
            by_query[entry.query_id].append(entry)
        grouped[system] = {qid: sorted(value, key=lambda item: item.rank) for qid, value in by_query.items()}

    component_ranks: dict[str, dict[str, dict[str, int]]] = {
        system: {
            query_id: {entry.chunk_id: entry.rank for entry in entries}
            for query_id, entries in by_query.items()
        }
        for system, by_query in grouped.items()
    }
    rel2_ranks: dict[str, dict[str, int | None]] = {}
    primary_categories: dict[str, list[str]] = {
        "BM25 成功而 dense 失败": [],
        "dense 成功而 BM25 失败": [],
        "hybrid 改善排名": [],
        "hybrid 损害排名": [],
    }
    for query_id in sorted(query_map):
        bm = _first_relevant_rank(grouped.get("bm25", {}).get(query_id, []), qrels)
        de = _first_relevant_rank(grouped.get("dense", {}).get(query_id, []), qrels)
        hy = _first_relevant_rank(grouped.get("hybrid", {}).get(query_id, []), qrels)
        rel2_ranks[query_id] = {"bm25": bm, "dense": de, "hybrid": hy}
        if bm is not None and bm <= 10 and (de is None or de > 10):
            primary_categories["BM25 成功而 dense 失败"].append(query_id)
        if de is not None and de <= 10 and (bm is None or bm > 10):
            primary_categories["dense 成功而 BM25 失败"].append(query_id)
        best_single = min(rank for rank in (bm, de) if rank is not None) if bm is not None or de is not None else None
        if hy is not None and best_single is not None and hy < best_single:
            primary_categories["hybrid 改善排名"].append(query_id)
        # A query with no single-system rel=2 hit has no observed single rank for
        # hybrid to harm.  Keep the condition explicitly anchored to best_single.
        if best_single is not None and (hy is None or hy > best_single):
            primary_categories["hybrid 损害排名"].append(query_id)

    def hybrid_failed(query_id: str) -> bool:
        rank = rel2_ranks[query_id]["hybrid"]
        return rank is None or rank > 10

    dialect_failures = [
        query_id
        for query_id, query in sorted(query_map.items())
        if query.get("case_flags", {}).get("requires_dialect_reasoning")
        and hybrid_failed(query_id)
    ]
    version_failures = [
        query_id
        for query_id, query in sorted(query_map.items())
        if query.get("case_flags", {}).get("requires_version_reasoning")
        and hybrid_failed(query_id)
    ]

    dialect_regressions: dict[str, dict[str, float]] = {}
    slice_path = paths.evaluation / "slice_metrics.csv"
    if slice_path.is_file():
        by_dialect: dict[str, dict[str, float]] = defaultdict(dict)
        with slice_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("slice_name") != "dialect":
                    continue
                system = str(row.get("retriever") or "")
                dialect = str(row.get("slice_value") or "")
                try:
                    by_dialect[dialect][system] = float(row["graded_nDCG@10"])
                except (KeyError, TypeError, ValueError):
                    continue
        for dialect, values in sorted(by_dialect.items()):
            if set(values) != {"bm25", "dense", "hybrid"}:
                continue
            regression = max(values["bm25"], values["dense"]) - values["hybrid"]
            if regression > 0.05:
                dialect_regressions[dialect] = {**values, "regression": regression}

    token_evidence: dict[str, dict[str, list[str]]] = {}
    exact_token_candidates: list[tuple[int, str]] = []
    for query_id, query in sorted(query_map.items()):
        tokens = _query_diagnostic_tokens(query)
        if not tokens:
            continue
        matches: dict[str, list[str]] = {}
        for system in ("bm25", "dense", "hybrid"):
            passage_text = "\n".join(
                str(corpus_map.get(entry.chunk_id, {}).get("text") or "")
                for entry in grouped.get(system, {}).get(query_id, [])[:5]
            ).casefold()
            matches[system] = [token for token in tokens if token.casefold() in passage_text]
        token_evidence[query_id] = matches
        match_count = sum(len(found) for found in matches.values())
        if match_count:
            exact_token_candidates.append((-match_count, query_id))
    exact_token_cases = [query_id for _, query_id in sorted(exact_token_candidates)]

    # Dense success with lexical failure is the report's operational, rank-based
    # signal for cases that warrant semantic matching inspection.
    semantic_cases = list(primary_categories["dense 成功而 BM25 失败"])

    chunk_risk_candidates: list[tuple[int, int, str]] = []
    for query_id in sorted(query_map):
        relevant = [
            corpus_map[chunk_id]
            for chunk_id, relevance in qrels.get(query_id, {}).items()
            if relevance == 2 and chunk_id in corpus_map
        ]
        if not relevant:
            continue
        maximum_length = max(len(str(record.get("text") or "")) for record in relevant)
        hybrid_rank = rel2_ranks[query_id]["hybrid"]
        if maximum_length >= 900 and (hybrid_rank is None or hybrid_rank > 10):
            chunk_risk_candidates.append((-maximum_length, hybrid_rank or 10_000, query_id))
    chunk_risk_cases = [query_id for _, _, query_id in sorted(chunk_risk_candidates)]

    unjudged_counts: dict[str, int] = {}
    for query_id in sorted(query_map):
        occurrences = sum(
            1
            for system in ("bm25", "dense", "hybrid")
            for entry in grouped.get(system, {}).get(query_id, [])
            if entry.chunk_id not in qrels.get(query_id, {})
        )
        if occurrences:
            unjudged_counts[query_id] = occurrences
    pool_cases = sorted(unjudged_counts, key=lambda query_id: (-unjudged_counts[query_id], query_id))

    category_definitions: list[tuple[str, str, list[str]]] = [
        (
            "BM25 成功而 dense 失败",
            "BM25 首个显式 rel=2 位于 top-10，dense 首个显式 rel=2 在 top-10 之后或 top-30 未命中。",
            primary_categories["BM25 成功而 dense 失败"],
        ),
        (
            "dense 成功而 BM25 失败",
            "dense 首个显式 rel=2 位于 top-10，BM25 首个显式 rel=2 在 top-10 之后或 top-30 未命中。",
            primary_categories["dense 成功而 BM25 失败"],
        ),
        (
            "hybrid 改善排名",
            "hybrid 的首个显式 rel=2 排名严格优于最佳单路排名。",
            primary_categories["hybrid 改善排名"],
        ),
        (
            "hybrid 损害排名",
            "存在可比较的单路 rel=2 排名，且 hybrid 排名更低或 top-30 未命中。",
            primary_categories["hybrid 损害排名"],
        ),
        (
            "dialect-sensitive 查询中的失败",
            "case flag 明确要求方言推理，且 hybrid 首个显式 rel=2 不在 top-10。",
            dialect_failures,
        ),
        (
            "version-sensitive 查询中的失败",
            "case flag 明确要求版本推理，且 hybrid 首个显式 rel=2 不在 top-10。",
            version_failures,
        ),
        *[
            (
                f"方言切片 graded nDCG@10 回退 >0.05：{dialect}",
                "实际 slice 指标为 "
                + json.dumps(values, ensure_ascii=False, sort_keys=True)
                + "；选择该方言中 hybrid 相对最佳单路后移的查询作为 passage/component-rank 证据。",
                [
                    query_id
                    for query_id in primary_categories["hybrid 损害排名"]
                    if query_map[query_id].get("dialect") == dialect
                ],
            )
            for dialect, values in dialect_regressions.items()
        ],
        (
            "精确 SQL token 或 error code 主导的可核查案例",
            "查询含 SQLSTATE、错误码、错误符号、运算符或函数名，且实际 top-5 passage 出现至少一个完全相同 token。",
            exact_token_cases,
        ),
        (
            "需要语义匹配的案例",
            "以 dense top-10 命中 rel=2 而 BM25 未命中作为可复核的操作性信号，不据此宣称因果。",
            semantic_cases,
        ),
        (
            "chunk 粒度风险案例",
            "显式 rel=2 passage 至少 900 字符，且 hybrid 未在 top-10 命中；这是边界检查信号，不是已证明的原因。",
            chunk_risk_cases,
        ),
        (
            "需要 pool expansion 的未判定案例",
            "三套正式 top-30 中至少一次出现当前 qrels 未覆盖的 query/chunk 对。",
            pool_cases,
        ),
    ]

    selected_by_category = {
        title: candidates[:5] for title, _, candidates in category_definitions
    }
    case_tags: dict[str, list[str]] = defaultdict(list)
    catalog_order: list[str] = []
    for title, _, _ in category_definitions:
        for query_id in selected_by_category[title]:
            case_tags[query_id].append(title)
            if query_id not in catalog_order:
                catalog_order.append(query_id)

    pool_summary = read_json(paths.pool_expansion / "pool_expansion_summary.json", {})
    evaluation_blocked = pool_summary.get("evaluation_integrity_status") != "PASS"
    if evaluation_blocked:
        evaluation_statement = (
            "由于 judgment pool 不完整，所有正式 overall、slice、CI、pairwise 与单案例 "
            "metric delta 都是 **NOT_PUBLISHED (BLOCKED)**；这些案例不能替代补判后的指标。"
        )
        follow_up_statement = (
            "先完成 pool expansion；若完整评估仍显示方言/版本或 chunk 回退，再在 "
            "Stage 7 或 chunk-boundary review 中处理，补判前不调参。"
        )
    else:
        evaluation_statement = (
            "当前三路正式 Top-30 judgment pool 完整，overall、slice、CI 与 pairwise 指标已发布；"
            "单案例只用于解释已发布结果，不能替代独立人工 held-out 评估。"
        )
        follow_up_statement = (
            "结合已发布逐查询、切片与配对结果复核；后续方言/版本或 chunk 创新必须建立新系统版本，"
            "保留固定 v1 和当前 qrels，不按案例反向改标签。"
        )

    lines = [
        "# 检索失败分析",
        "",
        f"数据性质：**{DEVELOPMENT_LABEL}**。未判定文档没有被当作 relevance 0。",
        "",
        "Recall 语义声明：任何 Recall 都只能称为 **pooled Recall**；pool 不完整时不发布 Recall 数值。",
        "",
        "本报告只陈述实际 run、显式 qrel 与冻结语料 passage 可支持的事实；未判定结果不按 relevance 0 处理。",
        "",
        f"Pool 状态：`{pool_summary.get('evaluation_integrity_status', 'UNKNOWN')}`；未判定 top-30 出现次数：`{pool_summary.get('unjudged_top30_occurrence_count')}`；唯一扩池请求：`{pool_summary.get('pool_expansion_record_count')}`。",
        "",
        "这里的“成功/失败/改善/损害”只按首个显式 relevance-2 的观察排名定义。"
        + evaluation_statement,
        "",
        "## 类别覆盖",
        "",
    ]
    for title, definition, candidates in category_definitions:
        selected = selected_by_category[title]
        minimum = 5
        coverage = "PASS" if len(selected) >= minimum else "INSUFFICIENT_CASES"
        lines.extend(
            [
                f"### {title}",
                "",
                f"- 判定规则：{definition}",
                f"- 实际可识别：{len(candidates)}；要求展示：{minimum}；实际展示：{len(selected)}；覆盖状态：`{coverage}`。",
                f"- 案例：{', '.join(selected) if selected else '无可证据支持的案例'}",
                "",
            ]
        )

    lines.extend(
        [
            "## 案例证据目录",
            "",
            "下列案例只展示上述各类别选中的并集；同一查询属于多个类别时不重复整张证据卡。",
            "",
        ]
    )
    for query_id in catalog_order:
        query = query_map[query_id]
        serialized = serialized_map[query_id]
        ranks = rel2_ranks[query_id]
        lines.extend(
            [
                f"### {query_id}",
                "",
                f"- 类别：{'；'.join(case_tags[query_id])}",
                f"- dialect/version：`{query.get('dialect')}` / `{query.get('version')}`",
                f"- serialized query SHA-256：`{serialized.get('serialized_text_sha256')}`",
                "- follow-up/后续：" + follow_up_statement,
                "- serialized query（直接取自冻结 audit 文件）：",
                "",
            ]
        )
        for serialized_line in str(serialized.get("serialized_text") or "").split("\n"):
            lines.append(f"    {serialized_line}" if serialized_line else "")
        lines.extend(["", "- relevance-2 evidence passages："])
        relevant_ids = sorted(
            chunk_id for chunk_id, relevance in qrels.get(query_id, {}).items() if relevance == 2
        )
        if not relevant_ids:
            lines.append("  - 无显式 relevance-2 judgment；本案例不能作排名成败判断。")
        for chunk_id in relevant_ids:
            passage = corpus_map.get(chunk_id)
            if passage is None:
                lines.append(f"  - `{chunk_id}`：语料中缺失，属于工程证据错误。")
                continue
            lines.append(
                f"  - `{chunk_id}`；doc dialect/version=`{passage.get('dialect')}`/`{passage.get('version')}`；"
                f"title={_excerpt(passage.get('title'), 140)}；section={_excerpt(passage.get('section'), 180)}；"
                f"passage={_excerpt(passage.get('text'))}"
            )
        for system in ("bm25", "dense", "hybrid"):
            lines.append(f"- {system} top-5 results：")
            top = grouped.get(system, {}).get(query_id, [])[:5]
            if not top:
                lines.append("  - 缺失正式结果。")
            for entry in top:
                passage = corpus_map.get(entry.chunk_id, {})
                judgment = qrels.get(query_id, {}).get(entry.chunk_id)
                judgment_text = "UNJUDGED" if judgment is None else str(judgment)
                bm_rank = component_ranks.get("bm25", {}).get(query_id, {}).get(entry.chunk_id)
                dense_rank = component_ranks.get("dense", {}).get(query_id, {}).get(entry.chunk_id)
                lines.append(
                    f"  - rank={entry.rank}；chunk=`{entry.chunk_id}`；score=`{entry.score:.12f}`；"
                    f"judgment=`{judgment_text}`；component ranks BM25=`{bm_rank}` / dense=`{dense_rank}`；"
                    f"doc dialect/version=`{passage.get('dialect')}`/`{passage.get('version')}`；"
                    f"title={_excerpt(passage.get('title'), 140)}；passage={_excerpt(passage.get('text'))}"
                )

        metric_caveat = (
            "正式 metric impact=NOT_PUBLISHED (BLOCKED)；只可观察首个 rel=2 排名。"
            if evaluation_blocked
            else "正式 metric impact 以 evaluation 目录中的逐查询与配对结果为准，不从单个排名臆算。"
        )
        lines.append(
            "- metric impact："
            f"BM25={_display_rank(ranks['bm25'])}；dense={_display_rank(ranks['dense'])}；"
            f"hybrid={_display_rank(ranks['hybrid'])}。{metric_caveat}"
        )

        diagnosis: list[str] = []
        if query_id in primary_categories["BM25 成功而 dense 失败"]:
            diagnosis.append("实际排名显示 BM25 top-10 命中而 dense 未达 top-10")
        if query_id in primary_categories["dense 成功而 BM25 失败"]:
            diagnosis.append("实际排名显示 dense top-10 命中而 BM25 未达 top-10")
        if query_id in primary_categories["hybrid 改善排名"]:
            diagnosis.append("RRF 后首个 rel=2 严格前移")
        if query_id in primary_categories["hybrid 损害排名"]:
            diagnosis.append("RRF 后首个 rel=2 相对最佳单路后移或消失于 top-30")
        if query_id in dialect_failures:
            target = str(query.get("dialect"))
            counts = {
                system: sum(
                    corpus_map.get(entry.chunk_id, {}).get("dialect") == target
                    for entry in grouped.get(system, {}).get(query_id, [])[:5]
                )
                for system in ("bm25", "dense", "hybrid")
            }
            diagnosis.append(f"目标方言 top-5 文档数（BM25/dense/hybrid）={counts['bm25']}/{counts['dense']}/{counts['hybrid']}")
        if query_id in version_failures:
            diagnosis.append("case flag 要求版本推理，但 hybrid 未在 top-10 命中显式 rel=2")
        if query_id in token_evidence:
            matches = token_evidence[query_id]
            if any(matches.values()):
                diagnosis.append(
                    "top-5 passage 完全匹配 token："
                    + "; ".join(
                        f"{system}={found or '无'}" for system, found in matches.items()
                    )
                )
        if query_id in chunk_risk_cases:
            evidence_lengths = [
                len(str(corpus_map.get(chunk_id, {}).get("text") or ""))
                for chunk_id in relevant_ids
            ]
            diagnosis.append(
                f"rel=2 passage 最大字符数={max(evidence_lengths) if evidence_lengths else 0}，需检查结构化 chunk 边界；当前不声称它造成排名"
            )
        if query_id in unjudged_counts:
            diagnosis.append(
                f"三套 top-30 有 {unjudged_counts[query_id]} 次未判定出现，结论可能随补判变化"
            )
        lines.append("- diagnosis：" + "；".join(diagnosis) + "。")

        future: list[str] = (
            ["先按 pool_expansion_required.jsonl 对未判定结果作外部补判"]
            if evaluation_blocked
            else ["使用已发布逐查询、切片与配对指标复核，不修改当前冻结 qrels"]
        )
        if query_id in dialect_failures or query_id in version_failures:
            future.append(
                "若回退在独立评估中仍成立，在 Stage 7 检验方言/版本感知检索"
                if not evaluation_blocked
                else "若补判后回退仍成立，在 Stage 7 检验方言/版本感知检索"
            )
        if query_id in chunk_risk_cases:
            future.append("人工检查 relevance-2 passage 的 section 与 chunk 边界")
        future.append(
            "创新实验建立新系统版本，不覆盖 v1 或当前 qrels"
            if not evaluation_blocked
            else "补判前不据此调模型、RRF 或 qrels"
        )
        lines.extend(["- future handling：" + "；".join(future) + "。", ""])

    pool_handoff = (
        "完整未判定请求位于 `retrieval/pool_expansion/pool_expansion_required.jsonl`；它保存实际 "
        "passage 快照、三个系统的出现位置与 component ranks。人工或独立标注应写入独立的 "
        "`retrieval/qrels/pool_expansion_judgments.jsonl`，不得编辑受保护 qrels 或把未判定项自动写成 0。"
        "补判后必须重跑 check-pool、evaluate、test、受保护目录 after audit 与 finalize。"
        if evaluation_blocked
        else "当前 `retrieval/pool_expansion/pool_expansion_required.jsonl` 为空。新增 retriever 或修改 run "
        "若引入未判断 pair，必须先按版本化标注流程补齐；不得把 missing qrel 自动写成 0。"
    )
    lines.extend(["## Pool expansion 交接", "", pool_handoff, ""])
    output = paths.reports / "failure_analysis.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def compute_complementarity(
    bm25_entries: Iterable[Any],
    dense_entries: Iterable[Any],
    qrels: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Compute BM25/dense complementarity after the top-20 pool is complete."""
    by_bm25: dict[str, list[Any]] = defaultdict(list)
    by_dense: dict[str, list[Any]] = defaultdict(list)
    for entry in bm25_entries:
        by_bm25[entry.query_id].append(entry)
    for entry in dense_entries:
        by_dense[entry.query_id].append(entry)
    query_ids = sorted(set(by_bm25) | set(by_dense))
    jaccard10: list[float] = []
    jaccard20: list[float] = []
    hit_counts = {"bm25_only": 0, "dense_only": 0, "both": 0, "neither": 0}
    oracle = {5: 0, 10: 0, 20: 0}
    single_hits = {"bm25": 0, "dense": 0}
    rel2_bm25: set[str] = set()
    rel2_dense: set[str] = set()

    def ranked_ids(grouped: dict[str, list[Any]], query_id: str, depth: int) -> list[str]:
        return [
            entry.chunk_id
            for entry in sorted(grouped.get(query_id, []), key=lambda item: item.rank)
            if entry.rank <= depth
        ]

    for query_id in query_ids:
        judgments = qrels.get(query_id, {})
        for depth, target in ((10, jaccard10), (20, jaccard20)):
            left = set(ranked_ids(by_bm25, query_id, depth))
            right = set(ranked_ids(by_dense, query_id, depth))
            union = left | right
            target.append(len(left & right) / len(union) if union else 0.0)
        bm20 = ranked_ids(by_bm25, query_id, 20)
        de20 = ranked_ids(by_dense, query_id, 20)
        bm_hit = any(judgments.get(chunk_id) == 2 for chunk_id in bm20)
        de_hit = any(judgments.get(chunk_id) == 2 for chunk_id in de20)
        single_hits["bm25"] += int(bm_hit)
        single_hits["dense"] += int(de_hit)
        hit_counts[
            "both" if bm_hit and de_hit else "bm25_only" if bm_hit else "dense_only" if de_hit else "neither"
        ] += 1
        rel2_bm25.update(chunk_id for chunk_id in bm20 if judgments.get(chunk_id) == 2)
        rel2_dense.update(chunk_id for chunk_id in de20 if judgments.get(chunk_id) == 2)
        for depth in oracle:
            union = set(ranked_ids(by_bm25, query_id, depth)) | set(ranked_ids(by_dense, query_id, depth))
            oracle[depth] += int(any(judgments.get(chunk_id) == 2 for chunk_id in union))

    import statistics

    count = len(query_ids)
    bm25_hit_rate20 = single_hits["bm25"] / count if count else 0.0
    dense_hit_rate20 = single_hits["dense"] / count if count else 0.0
    oracle_hit_rate20 = oracle[20] / count if count else 0.0
    oracle_delta = oracle_hit_rate20 - max(bm25_hit_rate20, dense_hit_rate20)
    diagnostic_targets = {
        "BM25_only_relevance_2_query_hits_at_20": {
            "observed": hit_counts["bm25_only"],
            "required_minimum": 5,
            "passed": hit_counts["bm25_only"] >= 5,
        },
        "Dense_only_relevance_2_query_hits_at_20": {
            "observed": hit_counts["dense_only"],
            "required_minimum": 5,
            "passed": hit_counts["dense_only"] >= 5,
        },
        "oracle_union_HitRate@20_delta_over_best_single": {
            "observed": oracle_delta,
            "required_minimum": 0.02,
            "passed": oracle_delta + 1e-12 >= 0.02,
        },
    }
    targets_passed = all(item["passed"] for item in diagnostic_targets.values())
    return {
        "evaluation_label": DEVELOPMENT_LABEL,
        "relevance_definition": "explicit relevance = 2",
        "query_count": count,
        "BM25_only_relevance_2_query_hits_at_20": hit_counts["bm25_only"],
        "Dense_only_relevance_2_query_hits_at_20": hit_counts["dense_only"],
        "queries_hit_by_both_at_20": hit_counts["both"],
        "queries_missed_by_both_at_20": hit_counts["neither"],
        "mean_Jaccard@10": sum(jaccard10) / count if count else 0.0,
        "mean_Jaccard@20": sum(jaccard20) / count if count else 0.0,
        "median_Jaccard@10": statistics.median(jaccard10) if jaccard10 else 0.0,
        "median_Jaccard@20": statistics.median(jaccard20) if jaccard20 else 0.0,
        "oracle_union_HitRate@5": oracle[5] / count if count else 0.0,
        "oracle_union_HitRate@10": oracle[10] / count if count else 0.0,
        "oracle_union_HitRate@20": oracle_hit_rate20,
        "BM25_HitRate@20_rel2": bm25_hit_rate20,
        "Dense_HitRate@20_rel2": dense_hit_rate20,
        "oracle_union_HitRate@20_delta_over_best_single": oracle_delta,
        "unique_relevance_2_chunks_only_BM25": len(rel2_bm25 - rel2_dense),
        "unique_relevance_2_chunks_only_dense": len(rel2_dense - rel2_bm25),
        "unique_relevance_2_chunks_found_by_both": len(rel2_bm25 & rel2_dense),
        "diagnostic_target_status": "PASS" if targets_passed else "FAIL",
        "diagnostic_targets": diagnostic_targets,
        "diagnostic_investigation": {
            "dense_model_suitability": "The zero-shot dense model was frozen before evaluation; a diagnostic miss is reported rather than tuned away.",
            "query_document_prefix": "The fixed E5 query/document prefixes are asserted by automated tests.",
            "normalization": "Document and query embeddings are L2-normalized and tested against cosine equivalence.",
            "query_truncation": "The fixed maximum input length is recorded; inspect long serialized queries if complementarity targets fail.",
            "representation_independence": "BM25 tokens and dense embeddings use separate implementations; measured Jaccard values quantify overlap.",
        },
    }


def _git_commit(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_worktree_state(root: Path) -> dict[str, Any]:
    try:
        output = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal", "--", "retrieval"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        return {
            "retrieval_worktree_dirty_or_untracked": bool(output),
            "retrieval_status_porcelain": output,
        }
    except (OSError, subprocess.CalledProcessError):
        return {
            "retrieval_worktree_dirty_or_untracked": None,
            "retrieval_status_porcelain": [],
        }


def generate_manifest(paths: ProjectPaths, statuses: dict[str, Any]) -> dict[str, Any]:
    package_versions: dict[str, str | None] = {}
    for name in (
        "numpy",
        "rank-bm25",
        "sentence-transformers",
        "transformers",
        "torch",
        "PyYAML",
        "huggingface-hub",
        "fastembed",
        "onnxruntime",
        "protobuf",
    ):
        try:
            package_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            package_versions[name] = None
    dense_config = load_yaml(paths.config / "dense_baseline.yaml")
    evaluation_config = load_yaml(paths.config / "evaluation.yaml")
    bm25_metadata = read_json(paths.bm25_index / "metadata.json", {})
    dense_metadata = read_json(paths.dense_index / "metadata.json", {})
    dense_content_identity = {
        "embeddings_sha256": dense_metadata.get("embeddings_sha256"),
        "chunk_ids_sha256": dense_metadata.get("chunk_ids_sha256"),
        "configuration": dense_metadata.get("configuration"),
    }
    artifact = lambda path: sha256_file(path) if path.exists() else None
    protected = read_json(paths.protected_report, {})
    input_validation = read_json(paths.reports / "input_validation.json", {})
    observed_inputs = input_validation.get("observed", {}) if isinstance(input_validation, dict) else {}
    corpus_validation = input_validation.get("corpus", {}) if isinstance(input_validation, dict) else {}
    effective_qrels = read_json(paths.reports / "effective_qrels.json", {})
    source_snapshot = snapshot_release_source(paths)
    engineering_passed = statuses.get("engineering_status") == "PASS"
    if engineering_passed and statuses.get("evaluation_integrity_status") == "PASS":
        release = "retrieval-baseline-v1"
    elif engineering_passed and statuses.get("evaluation_integrity_status") == "BLOCKED":
        release = "retrieval-baseline-v1-candidate"
    else:
        release = "retrieval-baseline-v1-invalid"
    manifest = {
        "schema_version": "sqlmend-retrieval-manifest-v1",
        "module": "sqlmend-retrieval-baseline",
        "release": release,
        "machine_proposed_development_only": True,
        "corpus_path": paths.corpus.relative_to(paths.root).as_posix(),
        "corpus_sha256": artifact(paths.corpus),
        "corpus_record_count": corpus_validation.get("record_count"),
        "corpus_word_count": observed_inputs.get("total_word_count"),
        "corpus_approximate_unique_word_count": observed_inputs.get("approximate_unique_word_count"),
        "query_path": paths.queries.relative_to(paths.root).as_posix(),
        "query_sha256": artifact(paths.queries),
        "qrels_source_path": paths.qrels_source.relative_to(paths.root).as_posix(),
        "qrels_source_sha256": artifact(paths.qrels_source),
        "base_trec_qrels_sha256": artifact(paths.qrels),
        "supplemental_qrels_path": (
            paths.supplemental_qrels.relative_to(paths.root).as_posix()
            if paths.supplemental_qrels.is_file()
            else None
        ),
        "supplemental_qrels_sha256": artifact(paths.supplemental_qrels),
        "supplemental_qrel_count": effective_qrels.get("supplemental_qrel_count"),
        "effective_qrels_path": paths.effective_qrels.relative_to(paths.root).as_posix(),
        "effective_qrels_sha256": artifact(paths.effective_qrels),
        "effective_qrels_metadata_sha256": artifact(
            paths.reports / "effective_qrels.json"
        ),
        "effective_qrel_count": effective_qrels.get("effective_qrel_count"),
        "qrels_merge_policy": "frozen base plus conflict-free supplemental judgments limited to current formal top-30 union",
        "qrels_merge_policy_version": "sqlmend-effective-qrels-v1",
        "candidate_pool_sha256": artifact(paths.candidate_pools),
        "query_serializer_version": "sqlmend-query-v1",
        "query_serializer_config_sha256": artifact(paths.config / "query_serializer.yaml"),
        "serialized_queries_sha256": artifact(paths.serialized_queries),
        "bm25_config_sha256": artifact(paths.config / "bm25_baseline.yaml"),
        "dense_config_sha256": artifact(paths.config / "dense_baseline.yaml"),
        "hybrid_config_sha256": artifact(paths.config / "hybrid_rrf_baseline.yaml"),
        "evaluation_config_sha256": artifact(paths.config / "evaluation.yaml"),
        "dense_model_id": dense_config["model_id"],
        "dense_model_revision": dense_config["model_revision"],
        "random_seed": evaluation_config.get("random_seed"),
        "python_version": platform.python_version(),
        "package_versions": package_versions,
        "git_commit": _git_commit(paths.root),
        **_git_worktree_state(paths.root),
        "retrieval_source_tree_sha256": source_snapshot["tree_sha256"],
        "retrieval_source_file_count": source_snapshot["file_count"],
        "bm25_index_sha256": bm25_metadata.get("payload_sha256"),
        "dense_index_sha256": (
            canonical_json_sha256(dense_content_identity)
            if dense_metadata.get("embeddings_sha256") and dense_metadata.get("chunk_ids_sha256")
            else None
        ),
        "dense_model_snapshot_sha256": (
            sha256_tree(paths.dense_index / "model_cache")
            if (paths.dense_index / "model_cache").is_dir()
            else None
        ),
        "bm25_run_sha256": artifact(paths.bm25_run),
        "dense_run_sha256": artifact(paths.dense_run),
        "hybrid_run_sha256": artifact(paths.hybrid_run),
        "hybrid_provenance_sha256": artifact(paths.hybrid_provenance),
        "protected_paths_unchanged": protected.get("protected_paths_unchanged"),
        "protected_paths_report_sha256": artifact(paths.protected_report),
        "repeated_run_hashes": read_json(paths.evaluation / "run_determinism.json", {}),
        "run_determinism_sha256": artifact(paths.evaluation / "run_determinism.json"),
        "test_results_sha256": artifact(paths.reports / "test_results.json"),
        "input_validation_sha256": artifact(paths.reports / "input_validation.json"),
        "annotation_reproduction_sha256": artifact(
            paths.reproduction / "reproduction_report.json"
        ),
        "latency_sha256": artifact(paths.evaluation / "latency.json"),
        "judged_coverage_sha256": artifact(
            paths.evaluation / "judged_coverage.json"
        ),
        "overall_metrics_sha256": artifact(
            paths.evaluation / "overall_metrics.json"
        ),
        **{
            field: artifact(paths.evaluation / filename)
            for field, filename in COMPLETE_EVALUATION_HASH_FIELDS
        },
        **{
            field: artifact(paths.reports / filename)
            for field, filename in HUMAN_REPORT_HASH_FIELDS
        },
        "pool_expansion_summary_sha256": artifact(paths.pool_expansion / "pool_expansion_summary.json"),
        "pool_expansion_requests_sha256": artifact(paths.pool_expansion / "pool_expansion_required.jsonl"),
        **statuses,
    }
    write_json(paths.retrieval / "manifest.json", manifest)
    return manifest


def _report_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _slice_report_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "MISSING"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        "status": "PUBLISHED",
        "row_count": len(rows),
        "slice_names": sorted({row.get("slice_name") for row in rows}),
        "dialect_rows": [row for row in rows if row.get("slice_name") == "dialect"],
        "sha256": sha256_file(path),
    }


def _benchmark_report_summary(latency: dict[str, Any]) -> dict[str, Any]:
    warm = latency.get("warm_query_latency", {}) if isinstance(latency, dict) else {}
    dense = warm.get("dense", {}) if isinstance(warm, dict) else {}
    hybrid = warm.get("hybrid", {}) if isinstance(warm, dict) else {}

    def selected(summary: Any) -> dict[str, Any]:
        if not isinstance(summary, dict):
            return {}
        return {
            key: summary.get(key)
            for key in ("sample_count", "mean_ms", "median_ms", "p95_ms", "maximum_ms", "queries_per_second")
        }

    return {
        "query_count": latency.get("query_count") if isinstance(latency, dict) else None,
        "warmup_queries": latency.get("warmup_queries") if isinstance(latency, dict) else None,
        "repetitions": latency.get("repetitions") if isinstance(latency, dict) else None,
        "cold_start_seconds": latency.get("cold_start") if isinstance(latency, dict) else None,
        "warm_latency": {
            "bm25": selected(warm.get("bm25") if isinstance(warm, dict) else None),
            "dense_query_encoding": selected(dense.get("query_encoding")),
            "dense_exact_vector_search": selected(dense.get("vector_search")),
            "dense_total": selected(dense.get("total")),
            "hybrid_rrf_fusion": selected(hybrid.get("rrf_fusion")),
            "hybrid_total": selected(hybrid.get("total")),
        },
        "build_time_and_index_size": latency.get("build_performance") if isinstance(latency, dict) else None,
        "environment": latency.get("environment") if isinstance(latency, dict) else None,
    }


def _project_owned_inventory_files(paths: ProjectPaths) -> list[Path]:
    """Recursively enumerate owned files while pruning caches and legacy aliases."""

    excluded_directory_names = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
    excluded_directory_prefixes = {
        ("indices", "dense", "model_cache"),
        ("reproduction", "model_cache"),
    }

    def excluded_directory(parts: tuple[str, ...]) -> bool:
        return (
            parts[-1] in excluded_directory_names
            or any(parts[: len(prefix)] == prefix for prefix in excluded_directory_prefixes)
        )

    discovered: list[Path] = []
    if not paths.retrieval.is_dir():
        return discovered
    for current_root, directory_names, file_names in os.walk(paths.retrieval):
        current = Path(current_root)
        current_parts = current.relative_to(paths.retrieval).parts
        directory_names[:] = sorted(
            directory_name
            for directory_name in directory_names
            if not excluded_directory((*current_parts, directory_name))
        )
        for file_name in sorted(file_names):
            path = current / file_name
            relative = path.relative_to(paths.retrieval).as_posix()
            if relative in LEGACY_REPRODUCTION_ARTIFACTS:
                continue
            if path.suffix.casefold() in {".pyc", ".pyo"}:
                continue
            discovered.append(path)
    return discovered


def _contract_inventory(paths: ProjectPaths, blocked: bool) -> list[tuple[str, str]]:
    """List recursive project-owned artifacts and explicit cache exclusions."""

    dynamic = _project_owned_inventory_files(paths)
    fixed = [
        paths.retrieval / "README.md",
        paths.retrieval / "pyproject.toml",
        paths.retrieval / "requirements.txt",
        paths.serialized_queries,
        paths.bm25_index / "index.pkl",
        paths.bm25_index / "metadata.json",
        paths.dense_index / "embeddings.npy",
        paths.dense_index / "chunk_ids.json",
        paths.dense_index / "metadata.json",
        paths.bm25_run,
        paths.dense_run,
        paths.hybrid_run,
        paths.hybrid_provenance,
        paths.qrels,
        paths.supplemental_qrels,
        paths.effective_qrels,
        paths.pool_expansion / "pool_expansion_required.jsonl",
        paths.pool_expansion / "pool_expansion_summary.json",
        paths.evaluation / "overall_metrics.json",
        paths.evaluation / "per_query_metrics.csv",
        paths.evaluation / "slice_metrics.csv",
        paths.evaluation / "confidence_intervals.json",
        paths.evaluation / "pairwise_differences.json",
        paths.evaluation / "complementarity_report.json",
        paths.evaluation / "judged_coverage.json",
        paths.evaluation / "latency.json",
        paths.evaluation / "run_determinism.json",
        paths.reproduction / "bm25_annotation_reproduced.trec",
        paths.reproduction / "dense_annotation_reproduced.trec",
        paths.reproduction / "hybrid_annotation_reproduced.trec",
        paths.reproduction / "reproduction_report.json",
        paths.reports / "input_validation.json",
        paths.reports / "effective_qrels.json",
        paths.reports / "test_results.json",
        paths.reports / "protected_paths_report.json",
        paths.reports / "baseline_report.md",
        paths.reports / "failure_analysis.md",
        paths.reports / "provenance_audit.md",
        paths.reports / "validation_report.json",
        paths.reports / "completion_report.md",
        paths.retrieval / "manifest.json",
    ]
    blocked_publications = {
        "evaluation/per_query_metrics.csv",
        "evaluation/slice_metrics.csv",
        "evaluation/confidence_intervals.json",
        "evaluation/pairwise_differences.json",
        "evaluation/complementarity_report.json",
    }
    generated_outputs = {
        "reports/baseline_report.md",
        "reports/completion_report.md",
        "manifest.json",
    }
    inventory: list[tuple[str, str]] = []
    candidates: dict[str, Path] = {}
    for path in [*dynamic, *fixed]:
        relative = path.relative_to(paths.retrieval).as_posix()
        candidates.setdefault(relative, path)
    # Sort the union of present and contractual-placeholder paths so report
    # bytes do not change merely because finalize has just created a report.
    for relative, path in sorted(candidates.items()):
        if relative in generated_outputs:
            state = "CREATED_BY_FINALIZE"
        elif path == paths.supplemental_qrels and not path.exists():
            state = "OPTIONAL_EXTERNAL_INPUT_NOT_PRESENT"
        elif blocked and relative in blocked_publications:
            state = "NOT_PUBLISHED (BLOCKED)" if not path.exists() else "STALE_FILE_PRESENT"
        else:
            state = "CREATED" if path.exists() else "MISSING"
        inventory.append((relative, state))
    inventory.extend(
        [
            (
                "indices/dense/model_cache/",
                "EXCLUDED_DOWNLOADED_MODEL_CACHE; aggregate identity is "
                "dense_model_snapshot_sha256 in manifest.json",
            ),
            (
                "reproduction/model_cache/",
                "EXCLUDED_DOWNLOADED_MODEL_CACHE; identity is recorded by the "
                "annotation reproduction provenance",
            ),
            ("**/__pycache__/ and *.py[co]", "EXCLUDED_BYTECODE_CACHE"),
            ("**/.pytest_cache/", "EXCLUDED_TEST_CACHE"),
        ]
    )
    return inventory


def _validation_issue_summary(validation: Any) -> list[dict[str, Any]]:
    if not isinstance(validation, dict):
        return [{"status": "NOT_AVAILABLE", "reason": "final validation has not run"}]
    checks = validation.get("checks")
    if not isinstance(checks, list):
        return [{"status": "NOT_AVAILABLE", "reason": "validation checks are absent"}]
    issues = [
        {
            "check_id": check.get("check_id"),
            "status": check.get("status"),
            "explanation": check.get("explanation"),
            "recommended_remediation": check.get("recommended_remediation"),
        }
        for check in checks
        if isinstance(check, dict) and check.get("status") not in {"PASS", "SKIP"}
    ]
    return issues or [{"status": "NONE", "reason": "all recorded checks passed"}]


def generate_reports(paths: ProjectPaths, statuses: dict[str, Any], manifest: dict[str, Any]) -> None:
    pool = read_json(paths.pool_expansion / "pool_expansion_summary.json", {})
    latency = read_json(paths.evaluation / "latency.json", {})
    overall = read_json(paths.evaluation / "overall_metrics.json", {})
    judged = read_json(paths.evaluation / "judged_coverage.json", {})
    validation = read_json(paths.reports / "validation_report.json", None)
    tests = read_json(paths.reports / "test_results.json", None)
    input_validation = read_json(paths.reports / "input_validation.json", {})
    observed = input_validation.get("observed", {}) if isinstance(input_validation, dict) else {}
    protected = read_json(paths.protected_report, {})
    reproduction = read_json(paths.reproduction / "reproduction_report.json", {})
    bm25 = load_yaml(paths.config / "bm25_baseline.yaml")
    dense = load_yaml(paths.config / "dense_baseline.yaml")
    hybrid = load_yaml(paths.config / "hybrid_rrf_baseline.yaml")
    evaluation_config = load_yaml(paths.config / "evaluation.yaml")
    # Metric suppression is governed by direct pool incompleteness.  The
    # aggregate integrity status may be FAIL when a malformed artifact coexists
    # with the same unjudged results, but metrics must remain unpublished.
    blocked = pool.get("pool_expansion_required") is True
    engineering_passed = statuses.get("engineering_status") == "PASS"
    if engineering_passed and not blocked and statuses.get("evaluation_integrity_status") == "PASS":
        baseline_title = "# SQLMend-RAG 正式基线检索报告"
        completion_title = "# 阶段 5–6 完成报告"
    elif engineering_passed and blocked:
        baseline_title = "# SQLMend-RAG 正式基线候选状态报告——尚未完成"
        completion_title = "# 阶段 5–6 候选状态报告——尚未完成"
    else:
        baseline_title = "# SQLMend-RAG 无效基线状态报告——尚未完成"
        completion_title = "# 阶段 5–6 无效状态报告——尚未完成"

    if blocked:
        evaluation_sections = """## Overall metrics

`NOT_PUBLISHED (BLOCKED)`。`evaluation/overall_metrics.json` 只保存阻塞哨兵，不包含检索质量数值。

## Slice metrics

`NOT_PUBLISHED (BLOCKED)`。`evaluation/slice_metrics.csv` 必须不存在，避免把不完整 pool 当成完整评估。

## Confidence intervals

`NOT_PUBLISHED (BLOCKED)`。未运行 paired bootstrap。

## Pairwise comparisons

`NOT_PUBLISHED (BLOCKED)`。未发布 BM25/dense/hybrid 配对差异。

## Complementarity

`NOT_PUBLISHED (BLOCKED)`。正式互补性指标等待 top-30 全部判定。失败分析中的排名观察不等同于此指标。
"""
    else:
        evaluation_sections = f"""## Overall metrics

```json
{_report_json(overall)}
```

## Slice metrics

```json
{_report_json(_slice_report_summary(paths.evaluation / 'slice_metrics.csv'))}
```

## Confidence intervals

```json
{_report_json(read_json(paths.evaluation / 'confidence_intervals.json', {}))}
```

## Pairwise comparisons

```json
{_report_json(read_json(paths.evaluation / 'pairwise_differences.json', {}))}
```

## Complementarity

```json
{_report_json(read_json(paths.evaluation / 'complementarity_report.json', {}))}
```
"""

    quality_targets = {
        "status": statuses.get("retrieval_quality_status"),
        "hybrid_graded_nDCG@10_minimum": "best(BM25,dense)+0.01",
        "hybrid_pooled_Recall@10_rel2_minimum": "best(BM25,dense)-0.01",
        "hybrid_HitRate@5_rel2_minimum": "best(BM25,dense)-0.01",
        "maximum_unexplained_dialect_graded_nDCG@10_regression": 0.05,
        "interpretation": (
            "NOT_EVALUATED because the judgment pool is incomplete"
            if blocked
            else "see validation_report.json quality.hybrid_targets"
        ),
    }
    benchmark_summary = _benchmark_report_summary(latency)
    before = protected.get("before", {}) if isinstance(protected, dict) else {}
    after = protected.get("after", {}) if isinstance(protected, dict) else {}
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    protected_summary = {
        "before_file_count": before.get("file_count"),
        "before_tree_sha256": before.get("tree_sha256"),
        "after_file_count": after.get("file_count"),
        "after_tree_sha256": after.get("tree_sha256"),
        "protected_paths_unchanged": protected.get("protected_paths_unchanged")
        if isinstance(protected, dict)
        else None,
    }
    config_summary = {
        "bm25": bm25,
        "dense": dense,
        "hybrid": hybrid,
        "evaluation": evaluation_config,
    }
    identity_summary = {
        "corpus": {
            "path": manifest.get("corpus_path"),
            "sha256": manifest.get("corpus_sha256"),
            "chunks": manifest.get("corpus_record_count"),
            "words": manifest.get("corpus_word_count"),
            "approximate_unique_word_types": manifest.get("corpus_approximate_unique_word_count"),
            "ordering": "ascending chunk_id",
        },
        "queries": {
            "path": manifest.get("query_path"),
            "sha256": manifest.get("query_sha256"),
            "count": observed.get("query_count"),
            "serialized_sha256": manifest.get("serialized_queries_sha256"),
        },
        "qrels": {
            "protected_source_sha256": manifest.get("qrels_source_sha256"),
            "protected_source_count": observed.get("qrel_count"),
            "label_counts": observed.get("qrel_label_counts"),
            "effective_sha256": manifest.get("effective_qrels_sha256"),
            "effective_count": manifest.get("effective_qrel_count"),
            "supplemental_count": manifest.get("supplemental_qrel_count"),
        },
    }
    run_and_index_hashes = {
        "bm25_run_sha256": manifest.get("bm25_run_sha256"),
        "dense_run_sha256": manifest.get("dense_run_sha256"),
        "hybrid_run_sha256": manifest.get("hybrid_run_sha256"),
        "bm25_index_sha256": manifest.get("bm25_index_sha256"),
        "dense_index_sha256": manifest.get("dense_index_sha256"),
        "dense_model_snapshot_sha256": manifest.get("dense_model_snapshot_sha256"),
        "repeated_run_hashes": manifest.get("repeated_run_hashes"),
    }

    baseline = f"""{baseline_title}

数据性质：**{DEVELOPMENT_LABEL}**。250 条查询和当前 qrels 是机器提出的开发数据，不是 gold、人工标注或 held-out test，也不替代课程要求的 1,000+ 条人工标注。

## 最终状态

- release：`{manifest.get('release')}`
- engineering：`{statuses.get('engineering_status')}`
- evaluation integrity：`{statuses.get('evaluation_integrity_status')}`
- retrieval quality：`{statuses.get('retrieval_quality_status')}`
- annotation reproduction：`{statuses.get('annotation_reproduction_status')}`

## 冻结输入身份

```json
{_report_json(identity_summary)}
```

## 正式配置

```json
{_report_json(config_summary)}
```

BM25 与 dense 共用 `sqlmend-query-v1` 严格白名单序列化。Dense 模型精确 revision 是 `{dense.get('model_revision')}`；检索为 CPU 上的 L2-normalized float32 exact inner product，不使用 ANN。Hybrid 只读取两套正式 top-30 run，并按固定 RRF k={hybrid.get('rrf_k')} 融合。

## Run 与 index 身份

```json
{_report_json(run_and_index_hashes)}
```

## Pool completeness

```json
{_report_json(pool)}
```

```json
{_report_json(judged)}
```

缺失 `(query_id, chunk_id)` judgment 表示未判定，绝不等同于 relevance 0。所有 Recall 指标的严格名称是 **pooled Recall**，分母来自有限 judgment pool，不是 corpus-exhaustive recall。

{evaluation_sections}
## Quality targets

```json
{_report_json(quality_targets)}
```

## Latency、throughput、build time 与 index size

```json
{_report_json(benchmark_summary)}
```

## 限制与后续工作

- 当前开发标签由 Codex 机器提出，存在循环性与标注误差风险，必须由后续人工标注替换或独立复核。
- 历史 pool 由 BM25、BGE dense 与 source-linked evidence 构造，存在 pooling bias；正式 E5/BM25 的 pool 外结果是预期风险，不可按 0 惩罚。
- 当前 pool expansion required=`{pool.get('pool_expansion_required')}`；补判前不发布 overall、slice、CI、pairwise 或 complementarity 指标，也不据此调参。
- annotation reproduction=`{reproduction.get('annotation_reproduction_status')}`；逐系统证据和缺失项见 `reports/provenance_audit.md`。
- 本阶段是检索基线，不含方言/版本加权、过滤、reranker、query rewriting、HyDE、SQL 修复或生成。
- AI6127 PDF 的简单 UI、5 条界面演示查询、grounded generator、答案级 RAG 指标、至少 1,000 条人工标注 held-out 数据及标注者一致性至少 80% 仍未完成。

推荐先完成外部补判并冻结有效评估；只有 engineering 与 evaluation integrity 都 PASS 后，才考虑 Stage 7 dialect-aware retrieval。PDF 的 UI、生成与人工测试要求仍是后续独立工作。
"""
    paths.reports.mkdir(parents=True, exist_ok=True)
    (paths.reports / "baseline_report.md").write_text(baseline, encoding="utf-8", newline="\n")

    commands = [
        "audit-protected-paths --phase before",
        "verify-inputs",
        "serialize-queries",
        "audit-annotation-retrievers",
        "build-bm25",
        "build-dense",
        "run-bm25",
        "run-dense",
        "run-hybrid",
        "check-pool",
        "evaluate",
        "benchmark",
        "test",
        "audit-protected-paths --phase after",
        "finalize",
        "validate",
    ]
    status_object = {
        "release": manifest["release"],
        **statuses,
        "annotation_reproduction_status": statuses.get(
            "annotation_reproduction_status", "NOT_REPRODUCIBLE"
        ),
        "pool_expansion_required": bool(pool.get("pool_expansion_required")),
        "machine_proposed_development_only": True,
        "ready_for_stage_7_dialect_aware_retrieval": engineering_passed
        and statuses.get("evaluation_integrity_status") == "PASS",
    }
    inventory = _contract_inventory(paths, blocked)
    inventory_lines = "\n".join(f"- `{relative}` — `{state}`" for relative, state in inventory)
    test_summary = (
        {
            "status": tests.get("status"),
            "returncode": tests.get("returncode"),
            "source_tree_sha256": tests.get("source_tree_sha256"),
            "source_tree_sha256_after": tests.get("source_tree_sha256_after"),
            "source_stable_during_tests": tests.get("source_stable_during_tests"),
            "command": tests.get("command"),
        }
        if isinstance(tests, dict)
        else {"status": "NOT_AVAILABLE"}
    )
    completion = f"""{completion_title}

数据性质：**{DEVELOPMENT_LABEL}**。

本报告只覆盖正式检索基线，不代表 AI6127 整体课程作业已经完成。当前 release 是 `{manifest.get('release')}`；只要 engineering 失败或 evaluation integrity 未 PASS，标题与状态都必须明确写作“尚未完成”。

## 创建的精确文件

以下清单递归枚举项目自有代码、配置、隐藏占位文件（包括 `.gitignore`/`.gitkeep`）与契约产物。下载模型缓存、Python bytecode/`__pycache__`、pytest cache 明确排除；旧版 annotation reproduction 命名残留不进入正式清单。正式 dense 模型快照由 manifest 的目录 tree hash 整体绑定。

{inventory_lines}

## 执行的精确命令

从仓库根目录、已安装 `retrieval` editable package 的环境依次运行：

{chr(10).join(f'{index}. `python -m sqlmend_retrieval.cli {command}`' for index, command in enumerate(commands, start=1))}

`test` 子命令内部执行并记录 `python -m pytest retrieval/tests -q -p no:cacheprovider`，且比较测试前后 source tree；单独运行 pytest 只适合开发诊断，不能替代 `reports/test_results.json`。在 pool 未补齐时，`evaluate` 写入 BLOCKED sentinel 并返回 0；`finalize`、`validate`（以及因 `finalize` 阻塞而失败的 `all`）返回非零，这是预期阻塞信号，不是发布成功。

## Corpus、query 与 qrel 验证

```json
{_report_json(identity_summary)}
```

查询数：`{observed.get('query_count')}`；受保护 qrel 数：`{observed.get('qrel_count')}`；effective qrel 数：`{manifest.get('effective_qrel_count')}`。Supplemental judgments 只允许进入独立文件，不修改受保护输入。

## 受保护目录前后验证

```json
{_report_json(protected_summary)}
```

## Annotation-reproduction 状态

状态：`{status_object['annotation_reproduction_status']}`；empirical ranking 状态：`{reproduction.get('empirical_ranking_reproduction_status')}`；provenance completeness：`{reproduction.get('provenance_completeness_status')}`。详细系统级比较、配置与缺失项见 `reports/provenance_audit.md`。

## 正式 BM25、dense 与 hybrid 配置

```json
{_report_json(config_summary)}
```

## Run 与 index hashes

```json
{_report_json(run_and_index_hashes)}
```

## Metric summary、slice summary、CI、pairwise 与 complementarity

所有 Recall 名称均为 **pooled Recall**。

{evaluation_sections}
## Quality-target summary

```json
{_report_json(quality_targets)}
```

## Performance summary

```json
{_report_json(benchmark_summary)}
```

## Pool-expansion 状态

```json
{_report_json(pool)}
```

唯一请求写在 `pool_expansion/pool_expansion_required.jsonl`。外部补判写入 `qrels/pool_expansion_judgments.jsonl` 后，流水线只合并当前正式 top-30 union 内、且不与冻结 base qrels 冲突的 query/chunk 对；流水线不会创建或覆盖该人工文件。

## Test evidence

```json
{_report_json(test_summary)}
```

## 所有未通过检查

```json
{_report_json(_validation_issue_summary(validation))}
```

`BLOCKED` 表示缺少判断而不能发布指标；它不应被误写为 relevance 0，也不等同于工程实现 FAIL。工程 FAIL 必须先修复；质量 FAIL 只能如实报告，不能在同一开发集上改 qrels、queries、模型或 RRF 来隐藏。

## 所有限制与下一推荐阶段

- 250 条查询与 13,449 条基础 qrels 是 machine-proposed development data，不是最终人工 held-out test。
- 不完整 judgment pool 和历史 pooling bias 阻止可靠的质量比较；先完成独立人工补判，再重跑整个评估与验证链。
- annotation retriever 复现若为 PARTIAL/NOT_REPRODUCIBLE，不能推断未复现系统与历史排名一致。
- 本阶段没有方言/版本感知、reranker、query rewriting、HyDE、SQL 修复、grounded generator 或答案级评估。
- PDF 仍要求简单 UI、5 条界面演示查询、grounded generator、答案级 RAG 指标、至少 1,000 条人工标注，以及标注者一致性至少 80%。

下一步不是直接进入 Stage 7：先补齐当前正式 top-30 judgments，使 evaluation integrity PASS 并冻结有效 baseline；随后才建议 Stage 7 dialect-aware retrieval。课程作业的 UI、生成与最终人工测试集仍须继续完成。

## 最终 status object

```json
{_report_json(status_object)}
```
"""
    (paths.reports / "completion_report.md").write_text(completion, encoding="utf-8", newline="\n")
