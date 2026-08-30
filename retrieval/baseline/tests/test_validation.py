from __future__ import annotations

import csv
from collections import Counter
import json
from pathlib import Path

import numpy as np
import pytest

from sqlmend_retrieval import validation
from sqlmend_retrieval.bootstrap import (
    bootstrap_metric_confidence_intervals,
    required_pairwise_comparisons,
)
from sqlmend_retrieval.cli import _finalize_release
from sqlmend_retrieval.hashing import (
    canonical_json_sha256,
    sha256_file,
    sha256_text,
    snapshot_protected_paths,
    snapshot_release_source,
)
from sqlmend_retrieval.bm25 import build_bm25_index
from sqlmend_retrieval.corpus import passages, validate_corpus
from sqlmend_retrieval.metrics import EVALUATION_LABEL, REQUIRED_METRIC_NAMES, evaluate_run
from sqlmend_retrieval.paths import ProjectPaths
from sqlmend_retrieval.pool_audit import write_pool_audit
from sqlmend_retrieval.qrels import QrelEntry, write_trec_qrels
from sqlmend_retrieval.queries import query_statistics, serialize_queries
from sqlmend_retrieval.reporting import compute_complementarity
from sqlmend_retrieval.slices import build_query_slices, evaluate_slices
from sqlmend_retrieval.trec import TrecRunEntry, read_trec_run, write_trec_run


DIALECTS = ("postgresql", "mysql", "sqlite", "mariadb", "duckdb")
QUERY_IDS = tuple(f"Q{number}" for number in range(1, 6))
CHUNK_IDS = tuple(f"c{number:02d}" for number in range(1, 32))


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _metrics(*, hybrid: bool = False) -> dict[str, float]:
    result = {name: 0.5 for name in REQUIRED_METRIC_NAMES}
    for name in ("Judged@5", "Judged@10", "Judged@20", "Judged@30"):
        result[name] = 1.0
    if hybrid:
        result["graded_nDCG@10"] = 0.52
    return result


def _write_pool_state(paths: ProjectPaths, *, blocked: bool) -> None:
    corpus = [json.loads(line) for line in paths.corpus.read_text(encoding="utf-8").splitlines()]
    qrels = validation.read_trec_qrels(
        paths.effective_qrels, known_chunk_ids=CHUNK_IDS, require_all_labels=True
    )
    runs = {
        "bm25_formal": read_trec_run(paths.bm25_run, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30),
        "dense_formal": read_trec_run(paths.dense_run, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30),
        "hybrid_rrf_formal": read_trec_run(paths.hybrid_run, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30),
    }
    pool = write_pool_audit(paths.pool_expansion, runs, qrels, corpus)
    coverage = pool["per_system"]
    paths.evaluation.mkdir(parents=True, exist_ok=True)
    (paths.evaluation / "judged_coverage.json").write_text(
        json.dumps(
            {
                "evaluation_label": EVALUATION_LABEL,
                "unjudged_documents_are_not_relevance_zero": True,
                "per_system": coverage,
                "evaluation_integrity_status": pool["evaluation_integrity_status"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _build_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, blocked: bool = False):
    root = tmp_path / "repo"
    (root / "construction" / "data" / "processed").mkdir(parents=True)
    (root / "annotation" / "codex").mkdir(parents=True)
    paths = ProjectPaths(root)
    bm25_config = {
        "retriever_id": "bm25_formal_v1",
        "k1": 1.5,
        "b": 0.75,
        "lowercase": True,
        "document_template": "sqlmend-passage-v1",
    }
    dense_config = {
        "retriever_id": "dense_formal_v1",
        "model_id": "fixture/model",
        "model_revision": "fixture-revision",
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
    }
    paths.config.mkdir(parents=True)
    config_values = {
        "bm25_baseline.yaml": bm25_config,
        "dense_baseline.yaml": dense_config,
        "hybrid_rrf_baseline.yaml": {
            "retriever_id": "hybrid_rrf_formal_v1",
            "rrf_k": 60,
            "fusion_depth": 30,
            "output_depth": 30,
        },
        "evaluation.yaml": {"evaluation_label": EVALUATION_LABEL},
        "query_serializer.yaml": {"serializer_version": "sqlmend-query-v1"},
    }
    for name, value in config_values.items():
        (paths.config / name).write_text(json.dumps(value) + "\n", encoding="utf-8")

    corpus = [
        {
            "chunk_id": chunk_id,
            "dialect": DIALECTS[index % len(DIALECTS)],
            "title": f"Title {chunk_id}",
            "section": "SQL",
            "text": f"Documentation for {chunk_id}",
        }
        for index, chunk_id in enumerate(CHUNK_IDS)
    ]
    _write_jsonl(paths.corpus, corpus)
    build_bm25_index(corpus, paths.bm25_index, bm25_config)
    ordered_corpus = sorted(corpus, key=lambda record: record["chunk_id"])
    dense_chunk_ids = [record["chunk_id"] for record in ordered_corpus]
    dense_embeddings = np.eye(len(dense_chunk_ids), dtype=np.float32)
    paths.dense_index.mkdir(parents=True)
    dense_embeddings_path = paths.dense_index / "embeddings.npy"
    dense_chunk_ids_path = paths.dense_index / "chunk_ids.json"
    np.save(dense_embeddings_path, dense_embeddings, allow_pickle=False)
    dense_chunk_ids_path.write_text(
        json.dumps(dense_chunk_ids) + "\n", encoding="utf-8"
    )
    dense_metadata = {
        "retriever_id": dense_config["retriever_id"],
        "model_id": dense_config["model_id"],
        "model_revision": dense_config["model_revision"],
        "configuration": dense_config,
        "chunk_order_sha256": sha256_text("\n".join(dense_chunk_ids) + "\n"),
        "corpus_records_sha256": canonical_json_sha256(ordered_corpus),
        "rendered_passages_sha256": canonical_json_sha256(passages(ordered_corpus)),
        "configuration_sha256": canonical_json_sha256(dense_config),
        "embeddings_sha256": sha256_file(dense_embeddings_path),
        "chunk_ids_sha256": sha256_file(dense_chunk_ids_path),
    }
    (paths.dense_index / "metadata.json").write_text(
        json.dumps(dense_metadata) + "\n", encoding="utf-8"
    )
    corpus_dialects: dict[str, int] = {}
    for record in corpus:
        corpus_dialects[record["dialect"]] = corpus_dialects.get(record["dialect"], 0) + 1

    queries = [
        {
            "query_id": query_id,
            "dialect": dialect,
            "version": "1.0",
            "user_problem": f"Why does query {query_id} fail?",
            "sql": f"SELECT '{query_id}';",
            "error_category": "syntax_error",
            "case_flags": {
                "requires_dialect_reasoning": False,
                "requires_version_reasoning": False,
                "has_documented_error": True,
                "plausible_but_wrong": False,
            },
        }
        for query_id, dialect in zip(QUERY_IDS, DIALECTS, strict=True)
    ]
    _write_jsonl(paths.queries, queries)
    _write_jsonl(
        paths.candidate_pools,
        [{"query_id": query_id, "candidates": []} for query_id in QUERY_IDS],
    )

    qrels: list[QrelEntry] = []
    for query_id in QUERY_IDS:
        for index, chunk_id in enumerate(CHUNK_IDS[:30], start=1):
            relevance = 2 if index == 1 else 1 if index == 2 else 0
            qrels.append(QrelEntry(query_id, chunk_id, relevance))
    if blocked:
        qrels.remove(QrelEntry("Q1", "c30", 0))
        qrels.append(QrelEntry("Q1", "c31", 0))
    _write_jsonl(
        paths.qrels_source,
        [
            {
                "query_id": item.query_id,
                "chunk_id": item.chunk_id,
                "relevance": item.relevance,
            }
            for item in qrels
        ],
    )
    write_trec_qrels(paths.qrels, qrels, known_chunk_ids=CHUNK_IDS, require_all_labels=True)
    write_trec_qrels(
        paths.effective_qrels,
        qrels,
        known_chunk_ids=CHUNK_IDS,
        require_all_labels=True,
    )

    serialized = [item.to_dict() for item in serialize_queries(queries)]
    _write_jsonl(paths.serialized_queries, serialized)

    for system, run_path in (
        ("bm25", paths.bm25_run),
        ("dense", paths.dense_run),
        ("hybrid", paths.hybrid_run),
    ):
        entries = [
            TrecRunEntry(
                query_id,
                chunk_id,
                rank,
                2.0 / (60 + rank) if system == "hybrid" else float(31 - rank),
                {
                    "bm25": "bm25_formal_v1",
                    "dense": "dense_formal_v1",
                    "hybrid": "hybrid_rrf_formal_v1",
                }[system],
            )
            for query_id in QUERY_IDS
            for rank, chunk_id in enumerate(CHUNK_IDS[:30], start=1)
        ]
        write_trec_run(run_path, entries, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30)

    with paths.hybrid_provenance.open("w", encoding="utf-8", newline="\n") as handle:
        for query_id in QUERY_IDS:
            for rank, chunk_id in enumerate(CHUNK_IDS[:30], start=1):
                handle.write(
                    json.dumps(
                        {
                            "query_id": query_id,
                            "chunk_id": chunk_id,
                            "rank": rank,
                            "rrf_score": 2.0 / (60 + rank),
                            "bm25_rank": rank,
                            "dense_rank": rank,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )

    _write_pool_state(paths, blocked=blocked)
    nested_qrels: dict[str, dict[str, int]] = {}
    for item in qrels:
        nested_qrels.setdefault(item.query_id, {})[item.chunk_id] = item.relevance
    run_entries = {
        "bm25": read_trec_run(paths.bm25_run, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30),
        "dense": read_trec_run(paths.dense_run, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30),
        "hybrid": read_trec_run(paths.hybrid_run, known_chunk_ids=CHUNK_IDS, exact_results_per_query=30),
    }
    evaluations = {
        system: evaluate_run(entries, nested_qrels)
        for system, entries in run_entries.items()
    }
    metrics = {
        "evaluation_label": EVALUATION_LABEL,
        "recall_semantics": "pooled Recall",
        "systems": {
            system: evaluation["overall"]
            for system, evaluation in evaluations.items()
        },
    }
    (paths.evaluation / "overall_metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    per_query_rows = [
        {"query_id": query_id, "retriever": system, **query_metrics}
        for system, evaluation in evaluations.items()
        for query_id, query_metrics in evaluation["per_query"].items()
    ]
    with (paths.evaluation / "per_query_metrics.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fields = sorted({key for row in per_query_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(per_query_rows, key=lambda row: (row["query_id"], row["retriever"])))

    slice_rows = []
    for system, entries in run_entries.items():
        slice_rows.extend(
            evaluate_slices(
                entries,
                nested_qrels,
                queries,
                retriever=system,
                confidence_interval_metrics=(
                    "graded_nDCG@10",
                    "MRR@10_rel2",
                    "pooled_Recall@10_rel2",
                    "HitRate@5_rel2",
                ),
                bootstrap_samples=10_000,
                random_seed=42,
                confidence_level=0.95,
            )
        )
    with (paths.evaluation / "slice_metrics.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fields = sorted({key for row in slice_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(slice_rows)

    primary_metrics = (
        "graded_nDCG@10",
        "MRR@10_rel2",
        "pooled_Recall@10_rel2",
        "HitRate@5_rel2",
    )
    (paths.evaluation / "confidence_intervals.json").write_text(
        json.dumps(
            {
                system: bootstrap_metric_confidence_intervals(
                    evaluation["per_query"],
                    n_samples=10_000,
                    seed=42,
                    confidence_level=0.95,
                )
                for system, evaluation in evaluations.items()
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    pairwise = required_pairwise_comparisons(
        {system: evaluation["per_query"] for system, evaluation in evaluations.items()},
        n_samples=10_000,
        seed=42,
        confidence_level=0.95,
    )
    (paths.evaluation / "pairwise_differences.json").write_text(
        json.dumps(pairwise, indent=2) + "\n", encoding="utf-8"
    )
    (paths.evaluation / "complementarity_report.json").write_text(
        json.dumps(
            compute_complementarity(
                run_entries["bm25"], run_entries["dense"], nested_qrels
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if blocked:
        for name in (
            "per_query_metrics.csv",
            "slice_metrics.csv",
            "confidence_intervals.json",
            "pairwise_differences.json",
            "complementarity_report.json",
        ):
            (paths.evaluation / name).unlink()
        (paths.evaluation / "overall_metrics.json").write_text(
            json.dumps(
                {
                    "evaluation_label": EVALUATION_LABEL,
                    "status": "BLOCKED",
                    "reason": "At least one formal top-30 document is unjudged.",
                    "metrics_published": False,
                    "unjudged_documents_are_not_relevance_zero": True,
                    "required_action": "Obtain external judgments for pool_expansion_required.jsonl, merge them into a separately versioned evaluation qrels file without editing protected inputs, then rerun the pool audit and evaluation.",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    provenance_dir = paths.annotation / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    stored_runs_path = provenance_dir / "retrieval_runs.jsonl"
    retrieval_config_path = provenance_dir / "retrieval_config.json"
    embedding_model_path = provenance_dir / "embedding_model.json"
    stored_runs_path.write_text("fixture historical runs\n", encoding="utf-8")
    retrieval_config_path.write_text("{}\n", encoding="utf-8")
    embedding_model_path.write_text(
        json.dumps({"snapshot_manifest_sha256": "fixture-snapshot"}) + "\n",
        encoding="utf-8",
    )

    reports = {}
    for relative, markers in validation.REQUIRED_REPORT_MARKERS.items():
        name = Path(relative).name
        reports[name] = (
            f"# Fixture report\n{EVALUATION_LABEL}\n"
            + "pooled Recall\n"
            + "\n".join(markers)
            + "\n"
        )
    paths.reports.mkdir(parents=True, exist_ok=True)
    for name, text in reports.items():
        (paths.reports / name).write_text(text, encoding="utf-8")
    protected_snapshot = snapshot_protected_paths(paths)
    protected = {
        "before": protected_snapshot,
        "after": protected_snapshot,
        "differences": {"added": [], "removed": [], "changed": []},
        "protected_paths_unchanged": True,
    }
    paths.protected_report.write_text(
        json.dumps(protected, indent=2) + "\n", encoding="utf-8"
    )
    reproduction_inputs = {
        "implementation_sha256": sha256_file(
            Path(validation.__file__).with_name("reproduction.py")
        ),
        "corpus_sha256": sha256_file(paths.corpus),
        "queries_sha256": sha256_file(paths.queries),
        "stored_runs_sha256": sha256_file(stored_runs_path),
        "candidate_pools_sha256": sha256_file(paths.candidate_pools),
        "retrieval_config_sha256": sha256_file(retrieval_config_path),
        "embedding_model_sha256": sha256_file(embedding_model_path),
        "snapshot_manifest_sha256": "fixture-snapshot",
    }
    paths.reproduction.mkdir(parents=True, exist_ok=True)
    (paths.reproduction / "reproduction_report.json").write_text(
        json.dumps(
            {
                "schema_version": "sqlmend-annotation-reproduction-v1",
                "attempt_completed": True,
                "annotation_reproduction_status": "NOT_REPRODUCIBLE",
                "empirical_ranking_reproduction_status": "NOT_REPRODUCIBLE",
                "provenance_completeness_status": "NOT_REPRODUCIBLE",
                "provenance_limitations": ["fixture has no historical model"],
                "historical_query_contains_annotation_only_fields": True,
                "historical_query_is_never_used_by_formal_baselines": True,
                "preflight_validation": {"status": "FAIL", "error": "fixture"},
                "reproduction_runtime": {"python_version": "fixture"},
                "inputs": reproduction_inputs,
                "systems": {
                    system: {"status": "NOT_REPRODUCIBLE", "error": "fixture"}
                    for system in ("bm25", "dense", "hybrid_rrf")
                },
                "evaluation_label": EVALUATION_LABEL,
                "machine_proposed_development_only": True,
                "formal_baseline_independence": {
                    "uses_candidate_pool_ranks": False,
                    "uses_qrels_during_search": False,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (paths.reports / "effective_qrels.json").write_text(
        json.dumps(
            {
                "supplemental_qrels_present": False,
                "supplemental_qrel_count": 0,
                "effective_qrel_count": len(qrels),
                "base_qrels_sha256": sha256_file(paths.qrels),
                "effective_qrels_sha256": sha256_file(paths.effective_qrels),
                "protected_source_unchanged": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    corpus_validation = validate_corpus(
        paths.corpus, expected_sha256="", expected_records=0
    )
    corpus_report = {
        key: value for key, value in corpus_validation.items() if key != "records"
    }
    fixture_query_stats = query_statistics(queries)
    fixture_label_counts = dict(sorted(Counter(item.relevance for item in qrels).items()))
    fixture_required = {
        "query_count": len(queries),
        "dialect_counts": {dialect: 1 for dialect in sorted(DIALECTS)},
        "dialect_sensitive_count": 0,
        "version_sensitive_count": 0,
        "qrel_count": len(qrels),
        "qrel_label_counts": fixture_label_counts,
        "total_word_count": corpus_report["total_word_count"],
        "approximate_unique_word_count": corpus_report[
            "approximate_unique_word_count"
        ],
    }
    fixture_observed = {
        **fixture_query_stats,
        "qrel_count": len(qrels),
        "qrel_label_counts": fixture_label_counts,
        "queries_with_relevance_2": len(queries),
        "candidate_pool_sha256": sha256_file(paths.candidate_pools),
        "candidate_pool_record_count": len(queries),
        "total_word_count": corpus_report["total_word_count"],
        "approximate_unique_word_count": corpus_report[
            "approximate_unique_word_count"
        ],
    }
    (paths.reports / "input_validation.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "corpus": corpus_report,
                "observed": fixture_observed,
                "required": fixture_required,
                "failures": [],
                "machine_proposed_development_only": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    latency_summary = {
        "maximum_ms": 1.0,
        "mean_ms": 1.0,
        "median_ms": 1.0,
        "p50_ms": 1.0,
        "p95_ms": 1.0,
        "queries_per_second": 1000.0,
        "sample_count": len(queries),
    }
    latency = {
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "query_count": len(queries),
        "repetitions": 1,
        "warmup_queries": 3,
        "cold_start_scope": "fixture index/model load and binding validation",
        "warm_query_latency": {
            "bm25": dict(latency_summary),
            "dense": {
                "query_encoding": dict(latency_summary),
                "vector_search": dict(latency_summary),
                "total": dict(latency_summary),
            },
            "hybrid": {
                "bm25_component": dict(latency_summary),
                "dense_component": dict(latency_summary),
                "rrf_fusion": dict(latency_summary),
                "total": dict(latency_summary),
            },
        },
        "cold_start": {"bm25_seconds": 0.1, "dense_seconds": 0.1},
        "build_performance": {
            "bm25_index_build_seconds": 0.1,
            "bm25_index_size_bytes": 1,
            "dense_corpus_encoding_seconds": 0.1,
            "dense_embedding_index_size_bytes": 1,
            "dense_index_build_seconds": 0.1,
            "dense_model_cache_size_bytes": 1,
            "dense_model_load_or_download_seconds": 0.1,
        },
        "environment": {
            "clock": "test clock",
            "corpus_chunks": len(corpus),
            "cpu": "test cpu",
            "device_used_for_official_run": "cpu",
            "embedding_dimension": 1,
            "logical_cpu_count": 1,
            "operating_system": "test os",
            "package_versions": {"fixture": "1"},
            "physical_cpu_count": 1,
            "python_version": "3.12",
            "ram_bytes": 1,
        },
    }
    (paths.evaluation / "latency.json").write_text(
        json.dumps(latency, indent=2) + "\n", encoding="utf-8"
    )

    # Create non-semantic required fixture artifacts without invoking a model.
    json_artifacts = {
        "evaluation/latency.json",
        "evaluation/confidence_intervals.json",
        "evaluation/pairwise_differences.json",
        "evaluation/complementarity_report.json",
        "reproduction/reproduction_report.json",
    }
    for relative in (
        *validation.REQUIRED_ENGINEERING_FILES,
        *(validation.REQUIRED_COMPLETE_EVALUATION_FILES if not blocked else ()),
    ):
        path = paths.retrieval / relative
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if relative in json_artifacts else "fixture\n", encoding="utf-8")

    run_hashes = {
        "bm25": sha256_file(paths.bm25_run),
        "dense": sha256_file(paths.dense_run),
        "hybrid": sha256_file(paths.hybrid_run),
    }
    determinism = {
        system: {
            "first_sha256": digest,
            "second_sha256": digest,
            "byte_identical": True,
        }
        for system, digest in run_hashes.items()
    }
    (paths.evaluation / "run_determinism.json").write_text(
        json.dumps(determinism, indent=2) + "\n", encoding="utf-8"
    )
    source_snapshot = snapshot_release_source(paths)
    (paths.reports / "test_results.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "returncode": 0,
                "source_tree_sha256": source_snapshot["tree_sha256"],
                "source_file_count": source_snapshot["file_count"],
                "source_tree_sha256_after": source_snapshot["tree_sha256"],
                "source_stable_during_tests": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "sqlmend-retrieval-manifest-v1",
        "module": "sqlmend-retrieval-baseline",
        "release": (
            "retrieval-baseline-v1-candidate" if blocked else "retrieval-baseline-v1"
        ),
        "machine_proposed_development_only": True,
        "engineering_status": "PASS",
        "evaluation_integrity_status": "BLOCKED" if blocked else "PASS",
        "retrieval_quality_status": "NOT_EVALUATED" if blocked else "FAIL",
        "annotation_reproduction_status": "NOT_REPRODUCIBLE",
        "retrieval_source_tree_sha256": source_snapshot["tree_sha256"],
        "retrieval_source_file_count": source_snapshot["file_count"],
        "corpus_sha256": sha256_file(paths.corpus),
        "query_sha256": sha256_file(paths.queries),
        "qrels_source_sha256": sha256_file(paths.qrels_source),
        "base_trec_qrels_sha256": sha256_file(paths.qrels),
        "effective_qrels_sha256": sha256_file(paths.effective_qrels),
        "effective_qrels_metadata_sha256": sha256_file(paths.reports / "effective_qrels.json"),
        "supplemental_qrels_sha256": None,
        "candidate_pool_sha256": sha256_file(paths.candidate_pools),
        "query_serializer_config_sha256": sha256_file(paths.config / "query_serializer.yaml"),
        "serialized_queries_sha256": sha256_file(paths.serialized_queries),
        "bm25_config_sha256": sha256_file(paths.config / "bm25_baseline.yaml"),
        "dense_config_sha256": sha256_file(paths.config / "dense_baseline.yaml"),
        "hybrid_config_sha256": sha256_file(paths.config / "hybrid_rrf_baseline.yaml"),
        "evaluation_config_sha256": sha256_file(paths.config / "evaluation.yaml"),
        "bm25_index_sha256": json.loads(
            (paths.bm25_index / "metadata.json").read_text(encoding="utf-8")
        )["payload_sha256"],
        "dense_index_sha256": canonical_json_sha256(
            {
                "embeddings_sha256": dense_metadata["embeddings_sha256"],
                "chunk_ids_sha256": dense_metadata["chunk_ids_sha256"],
                "configuration": dense_metadata["configuration"],
            }
        ),
        "dense_model_snapshot_sha256": None,
        "bm25_run_sha256": run_hashes["bm25"],
        "dense_run_sha256": run_hashes["dense"],
        "hybrid_run_sha256": run_hashes["hybrid"],
        "hybrid_provenance_sha256": sha256_file(paths.hybrid_provenance),
        "protected_paths_report_sha256": sha256_file(paths.protected_report),
        "repeated_run_hashes": determinism,
        "run_determinism_sha256": sha256_file(paths.evaluation / "run_determinism.json"),
        "test_results_sha256": sha256_file(paths.reports / "test_results.json"),
        "input_validation_sha256": sha256_file(paths.reports / "input_validation.json"),
        "annotation_reproduction_sha256": sha256_file(
            paths.reproduction / "reproduction_report.json"
        ),
        "latency_sha256": sha256_file(paths.evaluation / "latency.json"),
        "judged_coverage_sha256": sha256_file(paths.evaluation / "judged_coverage.json"),
        "overall_metrics_sha256": sha256_file(paths.evaluation / "overall_metrics.json"),
        "per_query_metrics_sha256": (
            None
            if blocked
            else sha256_file(paths.evaluation / "per_query_metrics.csv")
        ),
        "slice_metrics_sha256": (
            None if blocked else sha256_file(paths.evaluation / "slice_metrics.csv")
        ),
        "confidence_intervals_sha256": (
            None
            if blocked
            else sha256_file(paths.evaluation / "confidence_intervals.json")
        ),
        "pairwise_differences_sha256": (
            None
            if blocked
            else sha256_file(paths.evaluation / "pairwise_differences.json")
        ),
        "complementarity_report_sha256": (
            None
            if blocked
            else sha256_file(paths.evaluation / "complementarity_report.json")
        ),
        "baseline_report_sha256": sha256_file(paths.reports / "baseline_report.md"),
        "failure_analysis_sha256": sha256_file(paths.reports / "failure_analysis.md"),
        "provenance_audit_sha256": sha256_file(paths.reports / "provenance_audit.md"),
        "completion_report_sha256": sha256_file(paths.reports / "completion_report.md"),
        "pool_expansion_summary_sha256": sha256_file(
            paths.pool_expansion / "pool_expansion_summary.json"
        ),
        "pool_expansion_requests_sha256": sha256_file(
            paths.pool_expansion / "pool_expansion_required.jsonl"
        ),
    }
    (paths.retrieval / "manifest.json").write_text(
        json.dumps(manifest, indent=2)
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(validation, "EXPECTED_CORPUS_SHA256", sha256_file(paths.corpus))
    monkeypatch.setattr(validation, "EXPECTED_CORPUS_RECORDS", len(corpus))
    monkeypatch.setattr(validation, "EXPECTED_CORPUS_DIALECT_COUNTS", dict(sorted(corpus_dialects.items())))
    monkeypatch.setattr(validation, "EXPECTED_QUERY_COUNT", len(queries))
    monkeypatch.setattr(
        validation,
        "EXPECTED_QUERY_DIALECT_COUNTS",
        {dialect: 1 for dialect in sorted(DIALECTS)},
    )
    monkeypatch.setattr(validation, "EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT", 0)
    monkeypatch.setattr(validation, "EXPECTED_VERSION_SENSITIVE_QUERY_COUNT", 0)
    monkeypatch.setattr(validation, "EXPECTED_QREL_COUNT", len(qrels))
    monkeypatch.setattr(
        validation,
        "EXPECTED_QREL_LABEL_COUNTS",
        dict(sorted({0: 140, 1: 5, 2: 5}.items())),
    )
    monkeypatch.setattr(
        validation,
        "EXPECTED_CONFIG_HASHES",
        {
            name: sha256_file(paths.config / name)
            for name in validation.EXPECTED_CONFIG_HASHES
        },
    )
    return paths, qrels


def _check(report, check_id):
    return next(record for record in report["checks"] if record["check_id"] == check_id)


def test_validate_release_passes_complete_fixture_and_writes_exact_contract(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)

    report = validation.validate_release(paths)

    assert report["engineering_status"] == "PASS"
    assert report["evaluation_integrity_status"] == "PASS"
    # A quality miss is a measured baseline outcome, not an integrity failure.
    assert report["retrieval_quality_status"] == "FAIL"
    assert report["overall_success"] is True
    assert report["checks"]
    assert all(tuple(record) == validation.CHECK_FIELDS for record in report["checks"])
    assert all(set(record) == set(validation.CHECK_FIELDS) for record in report["checks"])
    written = json.loads((paths.reports / "validation_report.json").read_text(encoding="utf-8"))
    assert written == report


def test_finalize_converges_reports_manifest_and_validation(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    from sqlmend_retrieval import cli

    monkeypatch.setattr(
        cli,
        "_corpus",
        lambda fixture_paths: validate_corpus(
            fixture_paths.corpus, expected_sha256="", expected_records=0
        ),
    )

    result = _finalize_release(paths)

    validation_report = result["validation"]
    manifest = result["manifest"]
    assert validation_report["engineering_status"] == "PASS"
    assert validation_report["evaluation_integrity_status"] == "PASS"
    assert manifest["engineering_status"] == validation_report["engineering_status"]
    assert manifest["evaluation_integrity_status"] == validation_report[
        "evaluation_integrity_status"
    ]
    assert manifest["baseline_report_sha256"] == sha256_file(
        paths.reports / "baseline_report.md"
    )
    assert manifest["completion_report_sha256"] == sha256_file(
        paths.reports / "completion_report.md"
    )
    assert json.loads(
        (paths.reports / "validation_report.json").read_text(encoding="utf-8")
    ) == validation_report


def test_finalize_does_not_turn_evaluation_failure_into_engineering_failure(
    tmp_path, monkeypatch
):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    from sqlmend_retrieval import cli

    monkeypatch.setattr(
        cli,
        "_corpus",
        lambda fixture_paths: validate_corpus(
            fixture_paths.corpus, expected_sha256="", expected_records=0
        ),
    )
    metrics_path = paths.evaluation / "overall_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["systems"]["hybrid"]["graded_nDCG@10"] = 0.999
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    result = _finalize_release(paths)

    assert result["validation"]["engineering_status"] == "PASS"
    assert result["validation"]["evaluation_integrity_status"] == "FAIL"
    assert result["manifest"]["engineering_status"] == "PASS"
    assert result["manifest"]["evaluation_integrity_status"] == "FAIL"
    assert result["manifest"]["release"] == "retrieval-baseline-v1-invalid"


def test_any_unjudged_top30_result_blocks_evaluation_but_not_engineering(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch, blocked=True)

    report = validation.validate_release(paths)

    assert report["engineering_status"] == "PASS"
    assert report["evaluation_integrity_status"] == "BLOCKED"
    assert report["retrieval_quality_status"] == "NOT_EVALUATED"
    assert report["overall_success"] is False
    judged = _check(report, "evaluation.judged_at_30")
    pool = _check(report, "evaluation.pool_summary")
    assert judged["status"] == "BLOCKED"
    assert judged["observed_value"]["unjudged_top30_occurrences"] == {
        "bm25": 1,
        "dense": 1,
        "hybrid": 1,
    }
    assert pool["status"] == "BLOCKED"


def test_blocked_coverage_artifact_mismatch_is_engineering_failure(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch, blocked=True)
    payload = json.loads(
        (paths.evaluation / "judged_coverage.json").read_text(encoding="utf-8")
    )
    payload["per_system"]["bm25_formal"]["Judged@30"] = 1.0
    (paths.evaluation / "judged_coverage.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )

    report = validation.validate_release(paths)

    assert report["evaluation_integrity_status"] == "BLOCKED"
    assert report["engineering_status"] == "FAIL"
    assert (
        _check(report, "engineering.judged_coverage.exact_artifact")["status"]
        == "FAIL"
    )


def test_blocked_sentinel_rejects_embedded_metric_payload(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch, blocked=True)
    overall_path = paths.evaluation / "overall_metrics.json"
    payload = json.loads(overall_path.read_text(encoding="utf-8"))
    payload["systems"] = {"hybrid": {"graded_nDCG@10": 1.0}}
    overall_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    report = validation.validate_release(paths)

    check = _check(report, "engineering.blocked_metric_suppression")
    assert check["status"] == "FAIL"
    assert check["observed_value"]["payload_exact"] is False
    assert report["evaluation_integrity_status"] == "BLOCKED"
    assert report["engineering_status"] == "FAIL"


def test_reproduction_report_requires_current_inputs_and_explicit_run_absence(
    tmp_path, monkeypatch
):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    reproduction_path = paths.reproduction / "reproduction_report.json"
    payload = json.loads(reproduction_path.read_text(encoding="utf-8"))
    payload["inputs"]["queries_sha256"] = "0" * 64
    (paths.reproduction / "dense_annotation_reproduced.trec").write_text(
        "stale\n", encoding="utf-8"
    )
    reproduction_path.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )

    report = validation.validate_release(paths)

    check = _check(report, "engineering.annotation_reproduction.evidence_contract")
    assert check["status"] == "FAIL"
    assert any("input hashes" in item for item in check["observed_value"]["violations"])
    assert any("stale run" in item for item in check["observed_value"]["violations"])


def test_invalid_run_is_an_engineering_failure_not_an_exception(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    lines = paths.dense_run.read_text(encoding="utf-8").splitlines()
    fields = lines[0].split()
    fields[3] = "2"
    lines[0] = " ".join(fields)
    paths.dense_run.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = validation.validate_release(paths)

    run_check = _check(report, "engineering.run.dense")
    assert run_check["status"] == "FAIL"
    assert "rank" in run_check["observed_value"]["message"]
    assert report["engineering_status"] == "FAIL"
    assert report["overall_success"] is False


def test_serialized_record_with_extra_gold_field_fails_whitelist_gate(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    records = [json.loads(line) for line in paths.serialized_queries.read_text(encoding="utf-8").splitlines()]
    records[0]["reference_fix_sql"] = "SECRET"
    _write_jsonl(paths.serialized_queries, records)

    report = validation.validate_release(paths)

    check = _check(report, "engineering.queries.serialized_whitelist")
    assert check["status"] == "FAIL"
    assert check["observed_value"]["violation_count"] >= 1
    assert report["engineering_status"] == "FAIL"


def test_each_query_must_have_relevance_two_even_when_label_counts_match(tmp_path, monkeypatch):
    paths, qrels = _build_release(tmp_path, monkeypatch)
    qrels.remove(QrelEntry("Q1", "c01", 2))
    qrels.remove(QrelEntry("Q2", "c03", 0))
    qrels.extend([QrelEntry("Q1", "c01", 0), QrelEntry("Q2", "c03", 2)])
    _write_jsonl(
        paths.qrels_source,
        [
            {"query_id": item.query_id, "chunk_id": item.chunk_id, "relevance": item.relevance}
            for item in qrels
        ],
    )
    write_trec_qrels(paths.qrels, qrels, known_chunk_ids=CHUNK_IDS, require_all_labels=True)

    report = validation.validate_release(paths)

    count_check = _check(report, "engineering.qrels.count_and_labels")
    coverage_check = _check(report, "engineering.qrels.query_coverage")
    assert count_check["status"] == "PASS"
    assert coverage_check["status"] == "FAIL"
    assert coverage_check["observed_value"]["missing_relevance_2_query_ids"] == ["Q1"]


def test_missing_required_report_phrase_fails_engineering(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    (paths.reports / "completion_report.md").write_text("# Completion\n", encoding="utf-8")

    report = validation.validate_release(paths)

    phrase_check = _check(report, "engineering.required_report_phrases")
    assert phrase_check["status"] == "FAIL"
    assert report["engineering_status"] == "FAIL"
    assert report["evaluation_integrity_status"] == "PASS"
    assert report["overall_success"] is False


def test_present_manifest_run_hash_mismatch_fails_determinism_gate(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    manifest = {
        "bm25_run_sha256": sha256_file(paths.bm25_run),
        "dense_run_sha256": "0" * 64,
        "hybrid_run_sha256": sha256_file(paths.hybrid_run),
    }
    (paths.retrieval / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    report = validation.validate_release(paths)

    check = _check(report, "engineering.runs.recorded_hashes")
    assert check["status"] == "FAIL"
    assert check["observed_value"]["systems"]["dense"]["actual"] != "0" * 64
    assert report["engineering_status"] == "FAIL"


def test_protected_after_snapshot_must_be_present_and_unchanged(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    protected = json.loads(paths.protected_report.read_text(encoding="utf-8"))
    protected["after"]["tree_sha256"] = "changed"
    protected["differences"]["changed"] = ["annotation/codex/dev_250.jsonl"]
    protected["protected_paths_unchanged"] = False
    paths.protected_report.write_text(
        json.dumps(protected, indent=2) + "\n", encoding="utf-8"
    )

    report = validation.validate_release(paths)

    check = _check(report, "engineering.protected_paths")
    assert check["status"] == "FAIL"
    assert report["engineering_status"] == "FAIL"
    assert report["overall_success"] is False


def test_current_protected_bytes_must_match_recorded_after_snapshot(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    with paths.queries.open("a", encoding="utf-8", newline="") as handle:
        handle.write('{"unexpected":"mutation"}\n')

    report = validation.validate_release(paths)

    check = _check(report, "engineering.protected_paths")
    assert check["status"] == "FAIL"
    assert check["observed_value"]["current_matches_recorded_after"] is False
    assert report["engineering_status"] == "FAIL"


def test_empty_latency_object_fails_engineering_evidence_gate(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    (paths.evaluation / "latency.json").write_text("{}\n", encoding="utf-8")

    report = validation.validate_release(paths)

    check = _check(report, "engineering.latency.complete_evidence")
    assert check["status"] == "FAIL"
    assert check["observed_value"]["violation_count"] > 0
    assert report["engineering_status"] == "FAIL"


def test_publishable_metric_value_must_match_recomputation(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    payload = json.loads(
        (paths.evaluation / "overall_metrics.json").read_text(encoding="utf-8")
    )
    payload["systems"]["bm25"]["graded_nDCG@10"] = 0.123456
    (paths.evaluation / "overall_metrics.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )

    report = validation.validate_release(paths)

    check = _check(report, "evaluation.complete_artifact_schemas")
    assert check["status"] == "FAIL"
    assert any(
        "differs from recomputation" in violation
        for violation in check["observed_value"]["violations"]
    )
    assert report["evaluation_integrity_status"] == "FAIL"


def test_validator_rejects_index_payload_tampering(tmp_path, monkeypatch):
    paths, _qrels = _build_release(tmp_path, monkeypatch)
    with (paths.dense_index / "embeddings.npy").open("ab") as handle:
        handle.write(b"tamper")

    report = validation.validate_release(paths)

    check = _check(report, "engineering.indices.current_corpus_config_binding")
    assert check["status"] == "FAIL"
    assert "hash mismatch" in check["observed_value"]["message"]
    assert report["engineering_status"] == "FAIL"
