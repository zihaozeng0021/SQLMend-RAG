"""Offline pool audit, five-system evaluation, and acceptance gates."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evaluation import METRIC_NAMES, evaluate_system
from .io import load_jsonl, load_yaml, read_qrels, read_trec_run, write_json, write_jsonl
from .models import RunEntry
from .paths import ProjectPaths
from .pipeline import (
    RUN_FILES,
    SYSTEM_CONFIG_FILES,
    OnlineInputs,
    corpus_passage_index,
    load_system_configs,
)
from .pool import FORMAL_SYSTEM_IDS, audit_pool, pool_expansion_artifact_data


SYSTEM_ORDER = tuple(FORMAL_SYSTEM_IDS)
PUBLISHABLE_ARTIFACTS = (
    "overall_metrics.json",
    "per_query_metrics.csv",
    "slice_metrics.csv",
    "comparison_results.json",
    "acceptance.json",
)


def load_formal_runs(paths: ProjectPaths) -> dict[str, list[RunEntry]]:
    runs: dict[str, list[RunEntry]] = {
        "hybrid_rrf_frozen_control_v1": read_trec_run(paths.baseline_run)
    }
    for system_id, filename in RUN_FILES.items():
        path = paths.runs / filename
        if not path.is_file():
            raise FileNotFoundError(f"Formal run is missing: {path}")
        runs[system_id] = read_trec_run(path)
    if tuple(runs) != SYSTEM_ORDER:
        raise ValueError(f"Formal system order differs: {tuple(runs)}")
    return runs


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    temporary.replace(path)


def _slice(result: Mapping[str, Any], name: str, value: str) -> Mapping[str, Any]:
    rows = [
        row
        for row in result["slices"]
        if row["slice_name"] == name and row["slice_value"] == value
    ]
    if len(rows) != 1:
        raise ValueError(f"Expected one {name}/{value} slice, found {len(rows)}")
    return rows[0]


def _relative_reduction(baseline: float, current: float) -> float | None:
    if baseline == 0.0:
        return None
    return (baseline - current) / baseline


def _gate(value: float | None, minimum: float, description: str) -> dict[str, Any]:
    passed = value is not None and value + 1e-15 >= minimum
    return {
        "description": description,
        "observed": value,
        "required_minimum": minimum,
        "passed": passed,
    }


def evaluate_acceptance(
    results: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = results["hybrid_rrf_frozen_control_v1"]
    dialect = results["hybrid_rrf_dialect_aware_v1"]
    version = results["hybrid_rrf_version_aware_v1"]
    combined = results["hybrid_rrf_dialect_version_aware_v1"]
    final = results["hybrid_rrf_dialect_version_lexical_rerank_v1"]
    baseline_ds = _slice(baseline, "case_flag", "dialect-sensitive")
    dialect_ds = _slice(dialect, "case_flag", "dialect-sensitive")
    final_ds = _slice(final, "case_flag", "dialect-sensitive")
    baseline_vs = _slice(baseline, "case_flag", "version-sensitive")
    version_vs = _slice(version, "case_flag", "version-sensitive")
    final_vs = _slice(final, "case_flag", "version-sensitive")

    phase7_cfg = config["phase7"]
    dialect_regressions = []
    for dialect_name in ("postgresql", "mysql", "sqlite", "mariadb", "duckdb"):
        before = float(_slice(baseline, "dialect", dialect_name)["graded_nDCG@10"])
        after = float(_slice(dialect, "dialect", dialect_name)["graded_nDCG@10"])
        delta = after - before
        dialect_regressions.append(
            {
                "dialect": dialect_name,
                "baseline": before,
                "phase7": after,
                "delta": delta,
                "passed": delta + 1e-15 >= -float(phase7_cfg["maximum_unexplained_dialect_slice_ndcg_regression"]),
            }
        )
    phase7_gates = {
        "wrong_dialect_reduction": _gate(
            _relative_reduction(
                float(baseline_ds["Wrong-Dialect@5"]),
                float(dialect_ds["Wrong-Dialect@5"]),
            ),
            float(phase7_cfg["wrong_dialect_relative_reduction_minimum"]),
            "Dialect-sensitive Wrong-Dialect@5 relative reduction versus frozen Hybrid",
        ),
        "dialect_sensitive_ndcg_delta": _gate(
            float(dialect_ds["graded_nDCG@10"]) - float(baseline_ds["graded_nDCG@10"]),
            float(phase7_cfg["dialect_sensitive_ndcg_delta_minimum"]),
            "Dialect-sensitive graded nDCG@10 delta versus frozen Hybrid",
        ),
        "overall_ndcg_delta": _gate(
            float(dialect["overall"]["graded_nDCG@10"]) - float(baseline["overall"]["graded_nDCG@10"]),
            float(phase7_cfg["overall_ndcg_delta_minimum"]),
            "Overall graded nDCG@10 delta versus frozen Hybrid",
        ),
        "dialect_slice_regressions": {
            "description": "No dialect slice nDCG@10 regression exceeds the configured limit",
            "rows": dialect_regressions,
            "passed": all(row["passed"] for row in dialect_regressions),
        },
    }

    phase8_cfg = config["phase8"]
    phase8_gates = {
        "wrong_version_reduction": _gate(
            _relative_reduction(
                float(baseline_vs["Wrong-Version@5"]),
                float(version_vs["Wrong-Version@5"]),
            ),
            float(phase8_cfg["wrong_version_relative_reduction_minimum"]),
            "Version-sensitive Wrong-Version@5 relative reduction for version-only ablation",
        ),
        "version_sensitive_ndcg_delta": _gate(
            float(version_vs["graded_nDCG@10"]) - float(baseline_vs["graded_nDCG@10"]),
            float(phase8_cfg["version_sensitive_ndcg_delta_minimum"]),
            "Version-sensitive graded nDCG@10 delta for version-only ablation",
        ),
        "combined_overall_ndcg_vs_phase7": _gate(
            float(combined["overall"]["graded_nDCG@10"]) - float(dialect["overall"]["graded_nDCG@10"]),
            float(phase8_cfg["overall_ndcg_vs_phase7_delta_minimum"]),
            "Combined overall graded nDCG@10 delta versus Phase 7",
        ),
        "combined_recall_vs_baseline": _gate(
            float(combined["overall"]["pooled_Recall@10_rel2"])
            - float(baseline["overall"]["pooled_Recall@10_rel2"]),
            float(phase8_cfg["overall_recall_vs_baseline_delta_minimum"]),
            "Combined overall pooled Recall@10_rel2 delta versus frozen Hybrid",
        ),
    }

    phase9_cfg = config["phase9"]
    ndcg_gain = float(final["overall"]["graded_nDCG@10"]) - float(combined["overall"]["graded_nDCG@10"])
    mrr_gain = float(final["overall"]["MRR@10_rel2"]) - float(combined["overall"]["MRR@10_rel2"])
    gain_minimum = float(phase9_cfg["primary_metric_absolute_gain_minimum"])
    other_floor = -float(phase9_cfg["other_metric_maximum_regression"])
    primary_pass = (ndcg_gain >= gain_minimum and mrr_gain >= other_floor) or (
        mrr_gain >= gain_minimum and ndcg_gain >= other_floor
    )
    slice_regressions = []
    for value in ("dialect-sensitive", "version-sensitive"):
        before = _slice(combined, "case_flag", value)
        after = _slice(final, "case_flag", value)
        delta = float(after["graded_nDCG@10"]) - float(before["graded_nDCG@10"])
        slice_regressions.append({"slice": value, "ndcg_delta": delta, "passed": delta >= -0.05})
    phase9_gates = {
        "ndcg_delta": ndcg_gain,
        "mrr_delta": mrr_gain,
        "one_primary_gain_and_other_preserved": {
            "required_gain": gain_minimum,
            "maximum_other_regression": -other_floor,
            "passed": primary_pass,
        },
        "recall_delta": _gate(
            float(final["overall"]["pooled_Recall@10_rel2"])
            - float(combined["overall"]["pooled_Recall@10_rel2"]),
            float(phase9_cfg["overall_recall_delta_minimum"]),
            "Reranked pooled Recall@10_rel2 delta versus combined unreranked",
        ),
        "sensitive_slice_regressions": {
            "rows": slice_regressions,
            "passed": all(row["passed"] for row in slice_regressions),
        },
    }

    final_cfg = config["final"]
    final_gates = {
        "overall_ndcg_delta": _gate(
            float(final["overall"]["graded_nDCG@10"]) - float(baseline["overall"]["graded_nDCG@10"]),
            float(final_cfg["overall_ndcg_delta_minimum"]),
            "Final overall graded nDCG@10 delta versus frozen Hybrid",
        ),
        "overall_mrr_delta": _gate(
            float(final["overall"]["MRR@10_rel2"]) - float(baseline["overall"]["MRR@10_rel2"]),
            float(final_cfg["overall_mrr_delta_minimum"]),
            "Final overall MRR@10_rel2 delta versus frozen Hybrid",
        ),
        "overall_recall_delta": _gate(
            float(final["overall"]["pooled_Recall@10_rel2"])
            - float(baseline["overall"]["pooled_Recall@10_rel2"]),
            float(final_cfg["overall_recall_delta_minimum"]),
            "Final overall pooled Recall@10_rel2 delta versus frozen Hybrid",
        ),
        "wrong_dialect_reduction": _gate(
            _relative_reduction(
                float(baseline_ds["Wrong-Dialect@5"]),
                float(final_ds["Wrong-Dialect@5"]),
            ),
            float(final_cfg["wrong_dialect_relative_reduction_minimum"]),
            "Final dialect-sensitive Wrong-Dialect@5 relative reduction",
        ),
        "wrong_version_reduction": _gate(
            _relative_reduction(
                float(baseline_vs["Wrong-Version@5"]),
                float(final_vs["Wrong-Version@5"]),
            ),
            float(final_cfg["wrong_version_relative_reduction_minimum"]),
            "Final version-sensitive Wrong-Version@5 relative reduction",
        ),
    }

    def passed(group: Mapping[str, Any]) -> bool:
        return all(value.get("passed", True) for value in group.values() if isinstance(value, Mapping))

    status = {
        "phase7": "PASS" if passed(phase7_gates) else "FAIL",
        "phase8": "PASS" if passed(phase8_gates) else "FAIL",
        "phase9": "PASS" if passed(phase9_gates) else "FAIL",
        "final": "PASS" if passed(final_gates) else "FAIL",
    }
    return {
        "schema_version": "sqlmend-retrieval-v1-acceptance-v1",
        "phase7": phase7_gates,
        "phase8": phase8_gates,
        "phase9": phase9_gates,
        "final": final_gates,
        "status": status,
        "retrieval_quality_status": "PASS" if all(value == "PASS" for value in status.values()) else "FAIL",
    }


def run_pool_audit(paths: ProjectPaths, inputs: OnlineInputs) -> dict[str, Any]:
    """Audit formal Top-30 coverage without publishing ranking metrics."""

    runs = load_formal_runs(paths)
    qrels = read_qrels(paths.qrels)
    corpus = corpus_passage_index(inputs)
    pool_result = audit_pool(runs, qrels, corpus, system_ids=SYSTEM_ORDER)
    expansion_records, pool_summary = pool_expansion_artifact_data(pool_result)
    write_jsonl(paths.pool_expansion / "pool_expansion_required.jsonl", expansion_records)
    write_json(paths.pool_expansion / "pool_expansion_summary.json", pool_summary)
    write_json(paths.evaluation / "judged_coverage.json", pool_summary)
    return {"runs": runs, "qrels": qrels, "corpus": corpus, "summary": pool_summary}


def run_evaluation(paths: ProjectPaths, inputs: OnlineInputs) -> dict[str, Any]:
    # Case flags are an offline-only slice join.  They are loaded here rather
    # than retained in OnlineInputs, so no annotation-only field can reach a
    # ranking function.
    raw_queries = load_jsonl(paths.queries)
    pool = run_pool_audit(paths, inputs)
    runs = pool["runs"]
    qrels = pool["qrels"]
    corpus = pool["corpus"]
    pool_summary = pool["summary"]
    if pool_summary["evaluation_integrity_status"] != "PASS":
        for filename in PUBLISHABLE_ARTIFACTS:
            path = paths.evaluation / filename
            if path.exists():
                path.unlink()
        blocked = {
            "schema_version": "sqlmend-retrieval-v1-evaluation-status-v1",
            "evaluation_integrity_status": "BLOCKED",
            "retrieval_quality_status": "NOT_EVALUATED",
            "reason": "At least one formal top-30 query-document pair is unjudged",
            "pool_expansion_record_count": pool_summary["pool_expansion_record_count"],
        }
        write_json(paths.evaluation / "evaluation_status.json", blocked)
        return blocked

    results = {
        system_id: evaluate_system(
            runs[system_id],
            qrels,
            inputs.online_queries,
            corpus,
            raw_queries,
            system_id=system_id,
        )
        for system_id in SYSTEM_ORDER
    }
    overall = {
        "schema_version": "sqlmend-retrieval-v1-overall-metrics-v1",
        "evaluation_label": "machine-proposed development evaluation",
        "recall_semantics": "pooled Recall",
        "systems": {system_id: results[system_id]["overall"] for system_id in SYSTEM_ORDER},
    }
    comparison = {
        "schema_version": "sqlmend-retrieval-v1-comparison-v1",
        "evaluation_label": "machine-proposed development evaluation",
        "system_order": list(SYSTEM_ORDER),
        "systems": {
            system_id: {
                "overall": results[system_id]["overall"],
                "slices": results[system_id]["slices"],
            }
            for system_id in SYSTEM_ORDER
        },
    }
    evaluation_config = load_yaml(paths.config / "evaluation.yaml")
    acceptance = evaluate_acceptance(results, evaluation_config)
    write_json(paths.evaluation / "overall_metrics.json", overall)
    write_json(paths.evaluation / "comparison_results.json", comparison)
    write_json(paths.evaluation / "acceptance.json", acceptance)
    write_json(
        paths.evaluation / "evaluation_status.json",
        {
            "schema_version": "sqlmend-retrieval-v1-evaluation-status-v1",
            "evaluation_integrity_status": "PASS",
            "retrieval_quality_status": acceptance["retrieval_quality_status"],
            "machine_proposed_development_only": True,
            "Judged@30": pool_summary["overall"]["Judged@30"],
        },
    )

    per_query_rows: list[dict[str, Any]] = []
    slice_rows: list[dict[str, Any]] = []
    for system_id in SYSTEM_ORDER:
        for query_id, metrics in sorted(results[system_id]["per_query"].items()):
            per_query_rows.append({"system_id": system_id, "query_id": query_id, **metrics})
        slice_rows.extend(results[system_id]["slices"])
    _write_csv(
        paths.evaluation / "per_query_metrics.csv",
        per_query_rows,
        ["system_id", "query_id", *METRIC_NAMES],
    )
    _write_csv(
        paths.evaluation / "slice_metrics.csv",
        slice_rows,
        ["system_id", "slice_name", "slice_value", "source_field", "query_count", *METRIC_NAMES, "estimate_warning"],
    )
    return {
        "evaluation_integrity_status": "PASS",
        "retrieval_quality_status": acceptance["retrieval_quality_status"],
        "results": results,
        "acceptance": acceptance,
        "pool": pool_summary,
    }
