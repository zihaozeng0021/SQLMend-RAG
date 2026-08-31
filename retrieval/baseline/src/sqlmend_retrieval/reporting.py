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
                f"- Status: `{detail.get('status')}`",
                f"- Available historical configuration: `{json.dumps(detail.get('configuration'), ensure_ascii=False, sort_keys=True)}`",
                f"- Independent recalculation run SHA-256: `{detail.get('reproduced_run_sha256')}`",
                f"-exact top-30 sequence match:`{metrics.get('exact_top30_sequence_match_rate')}`",
                f"-exact top-30 set match:`{metrics.get('exact_top30_set_match_rate')}`",
                f"- mean overlap / Jaccard / RBO: `{metrics.get('mean_top30_set_overlap')}` / `{metrics.get('mean_jaccard_at_30')}` / `{metrics.get('mean_reciprocal_rank_biased_overlap')}`",
                f"- mean Kendall on common docs:`{metrics.get('mean_kendall_correlation_on_common_documents')}`",
                f"- out-of-pool pairs / missing stored docs: `{metrics.get('out_of_pool_query_chunk_pair_count')}` / `{metrics.get('missing_stored_documents')}`",
                f"- score differences: `{metrics.get('score_differences')}` (history saving run without score)",
                f"- Error or restriction: `{detail.get('error') or detail.get('reason')}`",
                "",
            ]
        )
    report = f"""# Marking stage retriever source tracking audit

Data properties: **{DEVELOPMENT_LABEL}**.

Recall semantic statement: Any Recall can only be called a **pooled Recall**; this source audit itself does not publish retrieval quality indicators.

Status: `{result['annotation_reproduction_status']}`

## Identification and Audit Methods

The annotation phase system is identified by the protected `provenance/retrieval_config.json`, `provenance/embedding_model.json`, `provenance/retrieval_runs.jsonl` and `candidate_pools.jsonl`. This audit recalculates the rankings independently from frozen corpus/cases and historical configurations; the saved historical run is only used for comparison after the recalculation is completed, and the candidate pool is only used for out-of-pool audits, neither of which is input to the recalculation of the rankings.

The historical query construct contains annotation-only fields such as `expected_behavior`, which are annotation infrastructure cyclic risks that must be disclosed; these fields are only used for reproduction sources and never enter official baselines. The audit input hash is:

```json
{json.dumps(result.get('inputs', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## Settings and independent reproduction results are available

```json
{json.dumps(result.get('available_configuration', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

{chr(10).join(system_lines)}

## Missing information and restrictions

Source integrity status: `{result.get('provenance_completeness_status')}`. Explicitly documented limitations: `{json.dumps(result.get('provenance_limitations', []), ensure_ascii=False, sort_keys=True)}`. When a system displays `NOT_REPRODUCIBLE`, the error or dependency reasons are listed on a system-by-system basis; success on other systems cannot be inferred to success on that system. The historical save run does not have a score, so it can only verify the ranking, not the historical floating point score.

## Isolation from formal baselines

Formal BM25 uses `rank_bm25`, k1=1.5 and strict user field serializer; formal dense uses fixed revision `intfloat/e5-base-v2`, CPU exact search; formal hybrid only integrates the two sets of official run ranks, with fixed RRF k=60. The official search entry does not read qrels, candidate-pool ranks or annotation evidence, and any historical rankings are not copied into the official run.

## Formal results outside the existing pool

Formal run The only query/chunk pair falling outside the existing judgment pool: `{pool_summary.get('pool_expansion_record_count')}`; top-30 unjudged occurrences: `{pool_summary.get('unjudged_top30_occurrence_count')}`. If the value is `None`, it means that the source audit occurred before the formal pool audit, and the finalization phase will regenerate this report.
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
    return str(value) if value is not None else "Explicit rel=2 not hit in top-30"


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
        "BM25 succeeds but dense fails": [],
        "dense succeeded but BM25 failed": [],
        "hybrid improves ranking": [],
        "hybrid damage ranking": [],
    }
    for query_id in sorted(query_map):
        bm = _first_relevant_rank(grouped.get("bm25", {}).get(query_id, []), qrels)
        de = _first_relevant_rank(grouped.get("dense", {}).get(query_id, []), qrels)
        hy = _first_relevant_rank(grouped.get("hybrid", {}).get(query_id, []), qrels)
        rel2_ranks[query_id] = {"bm25": bm, "dense": de, "hybrid": hy}
        if bm is not None and bm <= 10 and (de is None or de > 10):
            primary_categories["BM25 succeeds but dense fails"].append(query_id)
        if de is not None and de <= 10 and (bm is None or bm > 10):
            primary_categories["dense succeeded but BM25 failed"].append(query_id)
        best_single = min(rank for rank in (bm, de) if rank is not None) if bm is not None or de is not None else None
        if hy is not None and best_single is not None and hy < best_single:
            primary_categories["hybrid improves ranking"].append(query_id)
        # A query with no single-system rel=2 hit has no observed single rank for
        # hybrid to harm.  Keep the condition explicitly anchored to best_single.
        if best_single is not None and (hy is None or hy > best_single):
            primary_categories["hybrid damage ranking"].append(query_id)

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
    semantic_cases = list(primary_categories["dense succeeded but BM25 failed"])

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
            "BM25 succeeds but dense fails",
            "BM25 first explicit rel=2 is in top-10, dense first explicit rel=2 is after top-10 or top-30 misses.",
            primary_categories["BM25 succeeds but dense fails"],
        ),
        (
            "dense succeeded but BM25 failed",
            "dense first explicit rel=2 at top-10, BM25 first explicit rel=2 after top-10 or top-30 miss.",
            primary_categories["dense succeeded but BM25 failed"],
        ),
        (
            "hybrid improves rankings",
            "The hybrid's first explicit rel=2 ranking strictly outperforms the best single-way ranking.",
            primary_categories["hybrid improves ranking"],
        ),
        (
            "hybrid damage ranking",
            "There is a comparable single-way rel=2 ranking, and the hybrid ranks lower or top-30 misses.",
            primary_categories["hybrid damage ranking"],
        ),
        (
            "Failure in dialect-sensitive query",
            "The case flag explicitly requires dialect inference, and hybrid's first explicit rel=2 is not in the top-10.",
            dialect_failures,
        ),
        (
            "Failure in version-sensitive query",
            "The case flag explicitly requires version inference, and the first explicit rel=2 in hybrid is not in the top-10.",
            version_failures,
        ),
        *[
            (
                f"Dialect slice graded nDCG@10 fallback >0.05: {dialect}",
                "The actual slice index is "
                + json.dumps(values, ensure_ascii=False, sort_keys=True)
                + ";Select the query with the relative best single-pass backward shift of hybrid in this dialect as passage/component-rank evidence.",
                [
                    query_id
                    for query_id in primary_categories["hybrid damage ranking"]
                    if query_map[query_id].get("dialect") == dialect
                ],
            )
            for dialect, values in dialect_regressions.items()
        ],
        (
            "Verifiable cases dominated by precise SQL token or error code",
            "The query contains SQLSTATE, error code, error symbol, operator or function name, and at least one identical token appears in the actual top-5 passage.",
            exact_token_cases,
        ),
        (
            "Semantic matching case",
            "Using dense top-10 hits with rel=2 and BM25 misses as operable signals that can be reviewed, we do not claim causation based on this.",
            semantic_cases,
        ),
        (
            "chunk granularity risk case",
            "Explicit rel=2 passage of at least 900 characters, and hybrid not hitting in the top-10; this is a bounds check signal, not a proven cause.",
            chunk_risk_cases,
        ),
        (
            "Undecided cases requiring pool expansion",
            "The query/chunk pair not covered by the current qrels appears at least once in the three official top-30 sets.",
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
            "Due to the incomplete judgment pool, all formal overall, slice, CI, pairwise and single cases"
            "The metric deltas are all **NOT_PUBLISHED (BLOCKED)**; these cases cannot replace the indicators after the supplementary judgment."
        )
        follow_up_statement = (
            "Complete pool expansion first; if the complete evaluation still shows dialect/version or chunk fallback, then "
            "Processed in Stage 7 or chunk-boundary review, no parameters will be adjusted before supplementary judgment."
        )
    else:
        evaluation_statement = (
            "The current three-way official Top-30 judgment pool is complete, and the overall, slice, CI and pairwise indicators have been released;"
            "Single cases are intended only to interpret published results and are not a substitute for independent human held-out evaluation."
        )
        follow_up_statement = (
            "Combined with the published query-by-query, slice and pairing results to review; subsequent dialect/version or chunk innovation must create a new system version,"
            "Keep the fixed baseline and current qrels, and do not change the labels in reverse according to the case."
        )

    lines = [
        "# Retrieval Failure Analysis",
        "",
        f"Data properties: **{DEVELOPMENT_LABEL}**. Undetermined documents are not treated as relevance 0.",
        "",
        "Recall semantic statement: Any Recall can only be called a **pooled Recall**; the Recall value will not be released when the pool is incomplete.",
        "",
        "This report only states the facts that can be supported by actual runs, explicit qrels, and frozen corpus passages; undetermined results are not treated as relevance 0.",
        "",
        f"Pool status: `{pool_summary.get('evaluation_integrity_status', 'UNKNOWN')}`; Unjudged top-30 occurrences: `{pool_summary.get('unjudged_top30_occurrence_count')}`; Unique pool expansion request: `{pool_summary.get('pool_expansion_record_count')}`.",
        "",
        "Success/failure/improvement/damage here is defined only by the ranking of the first explicit relevance-2 observation."
        + evaluation_statement,
        "",
        "## Category coverage",
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
                f"- Determination rule: {definition}",
                f"- Actual recognition: {len(candidates)}; Requested display: {minimum}; Actual display: {len(selected)}; Coverage status: `{coverage}`.",
                f"- Case: {', '.join(selected) if selected else 'Case without evidence'}",
                "",
            ]
        )

    lines.extend(
        [
            "## Case Evidence Directory",
            "",
            "The following cases only display the union of the selected categories above; the entire evidence card will not be repeated when the same query belongs to multiple categories.",
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
                f"- Category: {';'.join(case_tags[query_id])}",
                f"- dialect/version: `{query.get('dialect')}` / `{query.get('version')}`",
                f"- serialized query SHA-256: `{serialized.get('serialized_text_sha256')}`",
                "- follow-up/Follow-up:" + follow_up_statement,
                "- serialized query (taken directly from the frozen audit file):",
                "",
            ]
        )
        for serialized_line in str(serialized.get("serialized_text") or "").split("\n"):
            lines.append(f"    {serialized_line}" if serialized_line else "")
        lines.extend(["", "- relevance-2 evidence passages:"])
        relevant_ids = sorted(
            chunk_id for chunk_id, relevance in qrels.get(query_id, {}).items() if relevance == 2
        )
        if not relevant_ids:
            lines.append(" - No explicit relevance-2 judgment; this case cannot be used to judge the success or failure of ranking.")
        for chunk_id in relevant_ids:
            passage = corpus_map.get(chunk_id)
            if passage is None:
                lines.append(f" - `{chunk_id}`: missing from the corpus, which is an engineering evidence error.")
                continue
            lines.append(
                f" - `{chunk_id}`;doc dialect/version=`{passage.get('dialect')}`/`{passage.get('version')}`;"
                f"title={_excerpt(passage.get('title'), 140)}; section={_excerpt(passage.get('section'), 180)};"
                f"passage={_excerpt(passage.get('text'))}"
            )
        for system in ("bm25", "dense", "hybrid"):
            lines.append(f"- {system} top-5 results: ")
            top = grouped.get(system, {}).get(query_id, [])[:5]
            if not top:
                lines.append(" - Missing formal result.")
            for entry in top:
                passage = corpus_map.get(entry.chunk_id, {})
                judgment = qrels.get(query_id, {}).get(entry.chunk_id)
                judgment_text = "UNJUDGED" if judgment is None else str(judgment)
                bm_rank = component_ranks.get("bm25", {}).get(query_id, {}).get(entry.chunk_id)
                dense_rank = component_ranks.get("dense", {}).get(query_id, {}).get(entry.chunk_id)
                lines.append(
                    f" - rank={entry.rank}; chunk=`{entry.chunk_id}`; score=`{entry.score:.12f}`;"
                    f"judgment=`{judgment_text}`; component ranks BM25=`{bm_rank}` / dense=`{dense_rank}`;"
                    f"doc dialect/version=`{passage.get('dialect')}`/`{passage.get('version')}`;"
                    f"title={_excerpt(passage.get('title'), 140)};passage={_excerpt(passage.get('text'))}"
                )

        metric_caveat = (
            "Official metric impact=NOT_PUBLISHED (BLOCKED); only first rel=2 ranking can be observed."
            if evaluation_blocked
            else "The official metric impact is based on the query and pairing results in the evaluation directory and is not calculated from a single ranking."
        )
        lines.append(
            "-metric impact:"
            f"BM25={_display_rank(ranks['bm25'])}; dense={_display_rank(ranks['dense'])};"
            f"hybrid={_display_rank(ranks['hybrid'])}.{metric_caveat}"
        )

        diagnosis: list[str] = []
        if query_id in primary_categories["BM25 succeeds but dense fails"]:
            diagnosis.append("The actual ranking shows that BM25 top-10 hits but dense does not reach top-10")
        if query_id in primary_categories["dense succeeded but BM25 failed"]:
            diagnosis.append("The actual ranking shows that dense top-10 hits but BM25 does not reach top-10")
        if query_id in primary_categories["hybrid improves ranking"]:
            diagnosis.append("The first rel=2 after RRF is strictly moved forward")
        if query_id in primary_categories["hybrid damage ranking"]:
            diagnosis.append("The first relative best rel=2 after RRF moves backward or disappears in top-30")
        if query_id in dialect_failures:
            target = str(query.get("dialect"))
            counts = {
                system: sum(
                    corpus_map.get(entry.chunk_id, {}).get("dialect") == target
                    for entry in grouped.get(system, {}).get(query_id, [])[:5]
                )
                for system in ("bm25", "dense", "hybrid")
            }
            diagnosis.append(f"Number of top-5 documents in target dialect (BM25/dense/hybrid)={counts['bm25']}/{counts['dense']}/{counts['hybrid']}")
        if query_id in version_failures:
            diagnosis.append("case flag requires version inference, but hybrid does not hit explicit rel=2 in top-10")
        if query_id in token_evidence:
            matches = token_evidence[query_id]
            if any(matches.values()):
                diagnosis.append(
                    "top-5 passage exact match token:"
                    + "; ".join(
                        f"{system}={found or 'None'}" for system, found in matches.items()
                    )
                )
        if query_id in chunk_risk_cases:
            evidence_lengths = [
                len(str(corpus_map.get(chunk_id, {}).get("text") or ""))
                for chunk_id in relevant_ids
            ]
            diagnosis.append(
                f"rel=2 passage maximum number of characters={max(evidence_lengths) if evidence_lengths else 0}, need to check structured chunk boundaries; currently not claimed to cause ranking"
            )
        if query_id in unjudged_counts:
            diagnosis.append(
                f"Three sets of top-30 have {unjudged_counts[query_id]} unjudged occurrences, and the conclusion may change with supplementary judgments"
            )
        lines.append("- diagnosis:" + ";".join(diagnosis) + ".")

        future: list[str] = (
            ["First press pool_expansion_required.jsonl to make an external supplementary judgment for the undetermined result"]
            if evaluation_blocked
            else ["Review using published query-by-query, slice and paired indicators, without modifying the currently frozen qrels"]
        )
        if query_id in dialect_failures or query_id in version_failures:
            future.append(
                "If fallback holds in independent evaluation, verify dialect/version-aware retrieval in Stage 7"
                if not evaluation_blocked
                else "If the fallback is still true after the supplementary judgment, check the dialect/version aware retrieval in Stage 7"
            )
        if query_id in chunk_risk_cases:
            future.append("Manually check the section and chunk boundaries of relevance-2 passage")
        future.append(
            "Innovation experiments create new system versions without overwriting baseline or current qrels"
            if not evaluation_blocked
            else "Do not adjust the model, RRF or qrels accordingly before making up the judgment"
        )
        lines.extend(["- future handling: " + ";".join(future) + ".", ""])

    pool_handoff = (
        "The complete unresolved request is located in `retrieval/baseline/pool_expansion/pool_expansion_required.jsonl`; it holds the actual "
        "passage snapshot, occurrence position of three systems and component ranks. Manual or independent annotations should be written independently"
        "`retrieval/baseline/qrels/pool_expansion_judgments.jsonl`, you must not edit protected qrels or automatically write unjudgments to 0."
        "After re-judgment, check-pool, evaluate, test, protected directory after audit and finalize must be re-run."
        if evaluation_blocked
        else "The current `retrieval/baseline/pool_expansion/pool_expansion_required.jsonl` is empty. Add retriever or modify run "
        "If an undetermined pair is introduced, it must be completed according to the versioning annotation process; the missing qrel must not be automatically written as 0."
    )
    lines.extend(["## Pool expansion handover", "", pool_handoff, ""])
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
        release = "retrieval-baseline"
    elif engineering_passed and statuses.get("evaluation_integrity_status") == "BLOCKED":
        release = "retrieval-baseline-candidate"
    else:
        release = "retrieval-baseline-invalid"
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
        baseline_title = "# SQLMend-RAG official baseline search report"
        completion_title = "# Phase 5–6 Completion Report"
    elif engineering_passed and blocked:
        baseline_title = "# SQLMend-RAG Official Baseline Candidate Status Report - Not Completed"
        completion_title = "# Phase 5–6 Candidate Status Report - Not Completed"
    else:
        baseline_title = "# SQLMend-RAG Invalid Baseline Status Report - Not Completed"
        completion_title = "# Phase 5–6 Invalid Status Report - Not Completed"

    if blocked:
        evaluation_sections = """## Overall metrics

`NOT_PUBLISHED (BLOCKED)`. `evaluation/overall_metrics.json` only saves blocking sentinels and does not contain retrieval quality values.

## Slice metrics

`NOT_PUBLISHED (BLOCKED)`. `evaluation/slice_metrics.csv` must not exist to avoid treating an incomplete pool as a complete evaluation.

## Confidence intervals

`NOT_PUBLISHED (BLOCKED)`. Paired bootstrap was not run.

## Pairwise comparisons

`NOT_PUBLISHED (BLOCKED)`. Unpublished BM25/dense/hybrid pairing differences.

## Complementarity

`NOT_PUBLISHED (BLOCKED)`. Formal complementarity indicators await all top-30 determinations. Ranked observations in failure analysis are not equivalent to this metric.
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

Data properties: **{DEVELOPMENT_LABEL}**. The 250 queries and current qrels are machine-raised development data and are not gold, human annotation, or held-out testing, nor do they replace the 1,000+ human annotations required for the course.

## final state

- release: `{manifest.get('release')}`
- engineering: `{statuses.get('engineering_status')}`
- evaluation integrity: `{statuses.get('evaluation_integrity_status')}`
- retrieval quality: `{statuses.get('retrieval_quality_status')}`
- annotation reproduction: `{statuses.get('annotation_reproduction_status')}`

## Freeze input identity

```json
{_report_json(identity_summary)}
```

## Formal configuration

```json
{_report_json(config_summary)}
```

BM25 and dense share `sqlmend-query-v1` strict whitelist serialization. Dense model exact revision is `{dense.get('model_revision')}`; retrieved as L2-normalized float32 exact inner product on CPU, without using ANN. Hybrid only reads two sets of formal top-30 runs and fuses them according to fixed RRF k={hybrid.get('rrf_k')}.

## Run and index identity

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

Missing `(query_id, chunk_id)` judgment means undecided, which is by no means equivalent to relevance 0. The strict name of all Recall metrics is **pooled Recall**, with the denominator coming from the limited judgment pool, not corpus-exhaustive recall.

{evaluation_sections}
## Quality targets

```json
{_report_json(quality_targets)}
```

## Latency, throughput, build time and index size

```json
{_report_json(benchmark_summary)}
```

## Limitations and follow-up work

- The current development labels are proposed by the Codex machine, which has the risk of circularity and labeling errors, and must be replaced by subsequent manual labeling or independently reviewed.
- The historical pool is constructed from BM25, BGE dense and source-linked evidence, and there is pooling bias; officially, the results outside the pool of E5/BM25 are expected risks and cannot be punished as 0.
- The current pool expansion required=`{pool.get('pool_expansion_required')}`; the overall, slice, CI, pairwise or complementarity indicators will not be released before the supplementary judgment, nor will the parameters be adjusted accordingly.
- annotation reproduction=`{reproduction.get('annotation_reproduction_status')}`; see `reports/provenance_audit.md` for system-by-system evidence and missing items.
- This phase is the retrieval baseline and does not include dialect/version weighting, filtering, reranker, query rewriting, HyDE, SQL repair or generation.
- AI6127 PDF's simple UI, 5 interface demo queries, grounded generator, answer-level RAG metrics, at least 1,000 manually annotated held-out data and annotator consistency of at least 80% are still incomplete.

It is recommended to complete the external re-judgment first and freeze the effective evaluation; only consider Stage 7 dialect-aware retrieval after both engineering and evaluation integrity are PASS. The UI, generation and manual testing requirements of PDF are still independent work in the future.
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

Data properties: **{DEVELOPMENT_LABEL}**.

This report only covers the formal search baseline and does not represent the completion of the overall AI6127 coursework. The current release is `{manifest.get('release')}`; as long as engineering fails or evaluation integrity is not PASS, the title and status must be clearly written as "not yet completed".

## Exact file created

The following list recursively enumerates the project's own code, configuration, hidden placeholder files (including `.gitignore`/`.gitkeep`) and contract products. Download model cache, Python bytecode/`__pycache__`, and pytest cache are explicitly excluded; legacy annotation reproduction naming residues are not included in the official list. The formal dense model snapshot is bound by the manifest's directory tree hash as a whole.

{inventory_lines}

## Exact command to execute

Run from the root directory of the warehouse and the environment where the `retrieval` editable package has been installed:

{chr(10).join(f'{index}. `python -m sqlmend_retrieval.cli {command}`' for index, command in enumerate(commands, start=1))}

The `test` subcommand internally executes and records `python -m pytest retrieval/baseline/tests -q -p no:cacheprovider`, and compares the source tree before and after the test; running pytest alone is only suitable for development diagnosis, but cannot replace `reports/test_results.json`. When the pool is not filled, `evaluate` writes to the BLOCKED sentinel and returns 0; `finalize`, `validate` (and `all` that fails due to `finalize` blocking) return non-zero, which is an expected blocking signal, not a successful release.

## Corpus, query and qrel validation

```json
{_report_json(identity_summary)}
```

Number of queries: `{observed.get('query_count')}`; Number of protected qrels: `{observed.get('qrel_count')}`; Number of effective qrels: `{manifest.get('effective_qrel_count')}`. Supplemental judgments only allow access to standalone files and do not modify protected input.

## Protected directory before and after verification

```json
{_report_json(protected_summary)}
```

## Annotation-reproduction status

Status: `{status_object['annotation_reproduction_status']}`; empirical ranking status: `{reproduction.get('empirical_ranking_reproduction_status')}`; provenance completeness: `{reproduction.get('provenance_completeness_status')}`. See `reports/provenance_audit.md` for detailed system-level comparisons, configurations and missing items.

## Official BM25, dense and hybrid configurations

```json
{_report_json(config_summary)}
```

## Run and index hashes

```json
{_report_json(run_and_index_hashes)}
```

## Metric summary, slice summary, CI, pairwise and complementarity

All Recall names are **pooled Recall**.

{evaluation_sections}
## Quality-target summary

```json
{_report_json(quality_targets)}
```

## Performance summary

```json
{_report_json(benchmark_summary)}
```

## Pool-expansion status

```json
{_report_json(pool)}
```

The only request is written in `pool_expansion/pool_expansion_required.jsonl`. After the external supplementary judgment is written into `qrels/pool_expansion_judgments.jsonl`, the pipeline will only merge the query/chunk pairs that are within the current official top-30 union and do not conflict with the frozen base qrels; the pipeline will not create or overwrite the manual file.

## Test evidence

```json
{_report_json(test_summary)}
```

## All failed checks

```json
{_report_json(_validation_issue_summary(validation))}
```

`BLOCKED` indicates a lack of judgment to publish the indicator; it should not be mistakenly written as relevance 0, nor is it equivalent to engineering implementation FAIL. Engineering FAIL must be fixed first; quality FAIL can only be reported truthfully and cannot be hidden by changing qrels, queries, models, or RRFs on the same development set.

## All restrictions and next recommendation phase

- The 250 queries and 13,449 underlying qrels are machine-proposed development data, not final human held-out test.
- Incomplete judgment pool and historical pooling bias prevent reliable quality comparison; complete independent manual supplementary judgment first, and then rerun the entire evaluation and verification chain.
- If the annotation retriever recurrence is PARTIAL/NOT_REPRODUCIBLE, it cannot be inferred that the non-recurring system is consistent with the historical ranking.
- There is no dialect/version awareness, reranker, query rewriting, HyDE, SQL fixes, grounded generator or answer level evaluation at this stage.
- PDF still requires a simple UI, 5 interface demo queries, a grounded generator, answer-level RAG metrics, at least 1,000 human annotations, and annotator agreement of at least 80%.

The next step is not to enter Stage 7 directly: first complete the current official top-30 judgments, make evaluation integrity PASS and freeze the effective baseline; then recommend Stage 7 dialect-aware retrieval. UI, generation and final manual test sets for coursework must still be completed.

## final status object

```json
{_report_json(status_object)}
```
"""
    (paths.reports / "completion_report.md").write_text(completion, encoding="utf-8", newline="\n")
