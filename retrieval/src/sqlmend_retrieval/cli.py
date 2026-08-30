"""Command-line orchestration for the independent formal retrieval pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import yaml

from .hashing import audit_protected_paths, sha256_file, snapshot_release_source
from .paths import ProjectPaths

COMMANDS = (
    "verify-inputs",
    "serialize-queries",
    "build-bm25",
    "build-dense",
    "run-bm25",
    "run-dense",
    "run-hybrid",
    "audit-annotation-retrievers",
    "check-pool",
    "evaluate",
    "benchmark",
    "test",
    "finalize",
    "validate",
    "all",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sqlmend-retrieval")
    parser.add_argument("--root", help="SQLMend-RAG repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit-protected-paths")
    audit.add_argument("--phase", choices=("before", "after"), required=True)
    for command in COMMANDS:
        subparsers.add_parser(command)
    return parser


def _config(paths: ProjectPaths, name: str) -> dict[str, Any]:
    path = paths.config / name
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected mapping in {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _corpus(paths: ProjectPaths) -> dict[str, Any]:
    from .corpus import EXPECTED_CORPUS_WORDS, validate_corpus

    return validate_corpus(paths.corpus, expected_words=EXPECTED_CORPUS_WORDS)


def _serialized_pairs(paths: ProjectPaths) -> list[tuple[str, str]]:
    if not paths.serialized_queries.exists():
        raise FileNotFoundError("Run serialize-queries before retrieval.")
    from .queries import ALLOWED_SOURCE_FIELDS, SERIALIZER_VERSION, load_queries, serialize_queries

    serializer_config = _config(paths, "query_serializer.yaml")
    if serializer_config.get("serializer_version") != SERIALIZER_VERSION:
        raise ValueError("query_serializer.yaml serializer_version does not match runtime")
    if set(serializer_config.get("allowed_source_fields", ())) != set(ALLOWED_SOURCE_FIELDS):
        raise ValueError("query_serializer.yaml allowed_source_fields do not match runtime whitelist")

    expected = {
        item.query_id: item.to_dict()
        for item in serialize_queries(load_queries(paths.queries))
    }
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    with paths.serialized_queries.open("r", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            query_id = record["query_id"]
            if query_id in seen:
                raise ValueError(f"Duplicate serialized query {query_id}")
            seen.add(query_id)
            if record != expected.get(query_id):
                raise ValueError(
                    f"Serialized query {query_id!r} is stale, malformed, or not an exact whitelist serialization"
                )
            pairs.append((query_id, record["serialized_text"]))
    if len(pairs) != 250 or seen != set(expected):
        raise ValueError(
            f"Serialized query set differs from protected source: observed {len(pairs)}, required {len(expected)}"
        )
    return sorted(pairs)


def verify_inputs(paths: ProjectPaths) -> dict[str, Any]:
    from .qrels import load_qrels_jsonl
    from .queries import load_queries, query_statistics

    corpus = _corpus(paths)
    query_records = load_queries(paths.queries)
    query_stats = query_statistics(query_records)
    known = {record["chunk_id"] for record in corpus["records"]}
    qrels = load_qrels_jsonl(paths.qrels_source, known_chunk_ids=known, require_all_labels=True)
    label_counts = Counter(item.relevance for item in qrels)
    rel2_queries = {item.query_id for item in qrels if item.relevance == 2}
    query_ids = {record["query_id"] for record in query_records}
    expected = {
        "query_count": 250,
        "dialect_counts": {name: 50 for name in ("duckdb", "mariadb", "mysql", "postgresql", "sqlite")},
        "dialect_sensitive_count": 174,
        "version_sensitive_count": 53,
        "qrel_count": 23452,
        "qrel_label_counts": {0: 20154, 1: 2839, 2: 459},
        "total_word_count": 1663145,
        "approximate_unique_word_count": 35646,
    }
    observed = {
        **query_stats,
        "qrel_count": len(qrels),
        "qrel_label_counts": dict(sorted(label_counts.items())),
        "queries_with_relevance_2": len(rel2_queries),
        "candidate_pool_sha256": sha256_file(paths.candidate_pools),
        "candidate_pool_record_count": sum(1 for line in paths.candidate_pools.open("rb") if line.strip()),
        "total_word_count": corpus["total_word_count"],
        "approximate_unique_word_count": corpus["approximate_unique_word_count"],
    }
    failures = []
    for key in ("query_count", "dialect_counts", "dialect_sensitive_count", "version_sensitive_count", "qrel_count", "qrel_label_counts", "total_word_count", "approximate_unique_word_count"):
        if observed[key] != expected[key]:
            failures.append(f"{key}: observed {observed[key]!r}, required {expected[key]!r}")
    if rel2_queries != query_ids:
        failures.append(f"queries with relevance-2: observed {len(rel2_queries)}, required {len(query_ids)}")
    report = {
        "status": "PASS" if not failures else "FAIL",
        "corpus": {key: value for key, value in corpus.items() if key != "records"},
        "observed": observed,
        "required": expected,
        "failures": failures,
        "machine_proposed_development_only": True,
    }
    _write_json(paths.reports / "input_validation.json", report)
    if failures:
        raise ValueError("; ".join(failures))
    return report


def serialize_queries_command(paths: ProjectPaths) -> dict[str, Any]:
    from .queries import (
        ALLOWED_SOURCE_FIELDS,
        SERIALIZER_VERSION,
        load_queries,
        serialize_queries,
        write_serialized_queries,
    )

    config = _config(paths, "query_serializer.yaml")
    if config.get("serializer_version") != SERIALIZER_VERSION:
        raise ValueError("query serializer config/runtime version mismatch")
    if set(config.get("allowed_source_fields", ())) != set(ALLOWED_SOURCE_FIELDS):
        raise ValueError("query serializer config/runtime whitelist mismatch")

    serialized = serialize_queries(load_queries(paths.queries))
    result = write_serialized_queries(serialized, paths.serialized_queries)
    if len(serialized) != 250:
        raise ValueError(f"Serialized {len(serialized)} queries, required 250")
    return result


def build_bm25_command(paths: ProjectPaths) -> dict[str, Any]:
    from .bm25 import build_bm25_index

    return build_bm25_index(_corpus(paths)["records"], paths.bm25_index, _config(paths, "bm25_baseline.yaml"))


def build_dense_command(paths: ProjectPaths) -> dict[str, Any]:
    from .dense import build_dense_index

    return build_dense_index(_corpus(paths)["records"], paths.dense_index, _config(paths, "dense_baseline.yaml"))


def _record_determinism(paths: ProjectPaths, system: str, first: bytes, second: bytes) -> None:
    import hashlib

    path = paths.evaluation / "run_determinism.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current[system] = {
        "first_sha256": hashlib.sha256(first).hexdigest(),
        "second_sha256": hashlib.sha256(second).hexdigest(),
        "byte_identical": first == second,
    }
    _write_json(path, current)
    if first != second:
        raise RuntimeError(f"Repeated official {system} run was not byte-identical")


def _format_official_run(entries: list[Any], known: set[str], tag: str) -> bytes:
    from .trec import format_trec_run

    return format_trec_run(
        entries,
        known_chunk_ids=known,
        exact_results_per_query=30,
        expected_run_tag=tag,
    ).encode("utf-8")


def run_bm25_command(paths: ProjectPaths) -> dict[str, Any]:
    from .bm25 import load_bm25_index, run_bm25, verify_bm25_index_binding

    config = _config(paths, "bm25_baseline.yaml")
    records = _corpus(paths)["records"]
    known = {record["chunk_id"] for record in records}
    index = load_bm25_index(paths.bm25_index)
    verify_bm25_index_binding(index, records, config)
    queries = _serialized_pairs(paths)
    first_entries = run_bm25(index, queries, top_k=int(config["top_k"]))
    second_entries = run_bm25(index, queries, top_k=int(config["top_k"]))
    first = _format_official_run(first_entries, known, config["retriever_id"])
    second = _format_official_run(second_entries, known, config["retriever_id"])
    _record_determinism(paths, "bm25", first, second)
    paths.bm25_run.parent.mkdir(parents=True, exist_ok=True)
    paths.bm25_run.write_bytes(first)
    return {"path": paths.bm25_run.as_posix(), "records": len(first_entries), "sha256": sha256_file(paths.bm25_run)}


def run_dense_command(paths: ProjectPaths) -> dict[str, Any]:
    from .dense import load_dense_index, verify_dense_index_binding

    config = _config(paths, "dense_baseline.yaml")
    records = _corpus(paths)["records"]
    known = {record["chunk_id"] for record in records}
    index = load_dense_index(paths.dense_index)
    verify_dense_index_binding(index, records, config)
    queries = _serialized_pairs(paths)
    first_entries = index.search_texts(queries, top_k=int(config["top_k"]))
    second_entries = index.search_texts(queries, top_k=int(config["top_k"]))
    first = _format_official_run(first_entries, known, config["retriever_id"])
    second = _format_official_run(second_entries, known, config["retriever_id"])
    _record_determinism(paths, "dense", first, second)
    paths.dense_run.parent.mkdir(parents=True, exist_ok=True)
    paths.dense_run.write_bytes(first)
    return {"path": paths.dense_run.as_posix(), "records": len(first_entries), "sha256": sha256_file(paths.dense_run)}


def run_hybrid_command(paths: ProjectPaths) -> dict[str, Any]:
    from .rrf import FUSION_DEPTH, OUTPUT_DEPTH, RRF_K, fuse_ranked_lists
    from .schemas import SearchResult
    from .trec import read_trec_run

    config = _config(paths, "hybrid_rrf_baseline.yaml")
    observed_rrf = (
        int(config["rrf_k"]),
        int(config["fusion_depth"]),
        int(config["output_depth"]),
    )
    if observed_rrf != (RRF_K, FUSION_DEPTH, OUTPUT_DEPTH):
        raise ValueError(
            f"Hybrid config/runtime drift: observed {observed_rrf}, required {(RRF_K, FUSION_DEPTH, OUTPUT_DEPTH)}"
        )
    known = {record["chunk_id"] for record in _corpus(paths)["records"]}
    bm25_tag = _config(paths, "bm25_baseline.yaml")["retriever_id"]
    dense_tag = _config(paths, "dense_baseline.yaml")["retriever_id"]
    bm25 = read_trec_run(
        paths.bm25_run,
        known_chunk_ids=known,
        exact_results_per_query=30,
        expected_run_tag=bm25_tag,
    )
    dense = read_trec_run(
        paths.dense_run,
        known_chunk_ids=known,
        exact_results_per_query=30,
        expected_run_tag=dense_tag,
    )
    by_bm25: dict[str, list[Any]] = defaultdict(list)
    by_dense: dict[str, list[Any]] = defaultdict(list)
    for item in bm25:
        by_bm25[item.query_id].append(item)
    for item in dense:
        by_dense[item.query_id].append(item)
    expected_query_ids = {query_id for query_id, _text in _serialized_pairs(paths)}
    if set(by_bm25) != expected_query_ids or set(by_dense) != expected_query_ids:
        raise ValueError(
            "Hybrid components must both cover the exact serialized-query universe"
        )
    query_ids = sorted(expected_query_ids)

    def fuse_all() -> tuple[list[SearchResult], list[dict[str, Any]]]:
        entries: list[SearchResult] = []
        provenance: list[dict[str, Any]] = []
        for query_id in query_ids:
            fused = fuse_ranked_lists(
                sorted(by_bm25[query_id], key=lambda item: item.rank),
                sorted(by_dense[query_id], key=lambda item: item.rank),
            )
            for item in fused:
                entries.append(SearchResult(query_id, item.chunk_id, item.rank, item.rrf_score, config["retriever_id"]))
                provenance.append(
                    {
                        "query_id": query_id,
                        "chunk_id": item.chunk_id,
                        "rank": item.rank,
                        "rrf_score": item.rrf_score,
                        "bm25_rank": item.bm25_rank,
                        "dense_rank": item.dense_rank,
                    }
                )
        return entries, provenance

    first_entries, provenance = fuse_all()
    second_entries, _ = fuse_all()
    first = _format_official_run(first_entries, known, config["retriever_id"])
    second = _format_official_run(second_entries, known, config["retriever_id"])
    _record_determinism(paths, "hybrid", first, second)
    paths.hybrid_run.parent.mkdir(parents=True, exist_ok=True)
    paths.hybrid_run.write_bytes(first)
    with paths.hybrid_provenance.open("w", encoding="utf-8", newline="\n") as stream:
        for record in provenance:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"path": paths.hybrid_run.as_posix(), "records": len(first_entries), "sha256": sha256_file(paths.hybrid_run)}


def audit_annotation_command(paths: ProjectPaths) -> dict[str, Any]:
    from .reporting import audit_annotation_retrievers

    return audit_annotation_retrievers(paths)


def _load_runs_and_qrels(paths: ProjectPaths):
    from .qrels import convert_qrels_jsonl_to_trec, merge_supplemental_qrels, write_trec_qrels
    from .trec import read_trec_run

    corpus = _corpus(paths)
    known = {record["chunk_id"] for record in corpus["records"]}
    base_qrels = convert_qrels_jsonl_to_trec(
        paths.qrels_source,
        paths.qrels,
        known_chunk_ids=known,
        require_all_labels=True,
    )
    runs = {
        "bm25_formal": read_trec_run(
            paths.bm25_run,
            known_chunk_ids=known,
            exact_results_per_query=30,
            expected_run_tag=_config(paths, "bm25_baseline.yaml")["retriever_id"],
        ),
        "dense_formal": read_trec_run(
            paths.dense_run,
            known_chunk_ids=known,
            exact_results_per_query=30,
            expected_run_tag=_config(paths, "dense_baseline.yaml")["retriever_id"],
        ),
        "hybrid_rrf_formal": read_trec_run(
            paths.hybrid_run,
            known_chunk_ids=known,
            exact_results_per_query=30,
            expected_run_tag=_config(paths, "hybrid_rrf_baseline.yaml")["retriever_id"],
        ),
    }
    qrels, merge_metadata = merge_supplemental_qrels(
        base_qrels,
        paths.supplemental_qrels,
        runs,
        known_chunk_ids=known,
    )
    write_trec_qrels(
        paths.effective_qrels,
        qrels,
        known_chunk_ids=known,
        require_all_labels=True,
    )
    _write_json(paths.reports / "effective_qrels.json", {
        **merge_metadata,
        "base_qrels_sha256": sha256_file(paths.qrels),
        "effective_qrels_sha256": sha256_file(paths.effective_qrels),
        "protected_source_unchanged": True,
    })
    return corpus, runs, qrels


def check_pool_command(paths: ProjectPaths) -> dict[str, Any]:
    from .pool_audit import write_pool_audit

    corpus, runs, qrels = _load_runs_and_qrels(paths)
    result = write_pool_audit(paths.pool_expansion, runs, qrels, corpus["records"])
    # Full requests (including passage snapshots) live in the JSONL artifact;
    # returning them on stdout would duplicate many megabytes in `all`.
    return {key: value for key, value in result.items() if key != "pool_expansion_records"}


def _nested_qrels(qrel_entries: list[Any]) -> dict[str, dict[str, int]]:
    nested: dict[str, dict[str, int]] = defaultdict(dict)
    for item in qrel_entries:
        nested[item.query_id][item.chunk_id] = item.relevance
    return dict(nested)


def evaluate_command(paths: ProjectPaths) -> dict[str, Any]:
    """Evaluate only when every formal top-30 document has an explicit qrel."""
    from .reporting import DEVELOPMENT_LABEL, write_json

    evaluation_config = _config(paths, "evaluation.yaml")
    if evaluation_config.get("evaluation_label") != DEVELOPMENT_LABEL:
        raise ValueError("evaluation.yaml has the wrong development-evaluation label")
    if evaluation_config.get("recall_label") != "pooled Recall":
        raise ValueError("evaluation.yaml must name Recall as pooled Recall")
    bootstrap_samples = int(evaluation_config["bootstrap_samples"])
    random_seed = int(evaluation_config["random_seed"])
    confidence_level = float(evaluation_config["confidence_level"])

    # Always recompute coverage from the current formal runs and qrels.  A
    # stale PASS summary must never authorize metric publication.
    pool = check_pool_command(paths)
    write_json(
        paths.evaluation / "judged_coverage.json",
        {
            "evaluation_label": DEVELOPMENT_LABEL,
            "unjudged_documents_are_not_relevance_zero": True,
            "per_system": pool.get("per_system", {}),
            "evaluation_integrity_status": pool.get("evaluation_integrity_status"),
        },
    )
    if pool.get("pool_expansion_required"):
        for name in (
            "per_query_metrics.csv",
            "slice_metrics.csv",
            "confidence_intervals.json",
            "pairwise_differences.json",
            "complementarity_report.json",
        ):
            stale = paths.evaluation / name
            if stale.is_file():
                stale.unlink()
        blocked = {
            "evaluation_label": DEVELOPMENT_LABEL,
            "status": "BLOCKED",
            "reason": "At least one formal top-30 document is unjudged.",
            "metrics_published": False,
            "unjudged_documents_are_not_relevance_zero": True,
            "required_action": "Obtain external judgments for pool_expansion_required.jsonl, merge them into a separately versioned evaluation qrels file without editing protected inputs, then rerun the pool audit and evaluation.",
        }
        write_json(paths.evaluation / "overall_metrics.json", blocked)
        return blocked

    from .bootstrap import bootstrap_metric_confidence_intervals, required_pairwise_comparisons
    from .metrics import evaluate_run
    from .queries import load_queries
    from .slices import build_query_slices, evaluate_slices

    _, runs, qrel_entries = _load_runs_and_qrels(paths)
    qrels = _nested_qrels(qrel_entries)
    publication_names = {
        "bm25_formal": "bm25",
        "dense_formal": "dense",
        "hybrid_rrf_formal": "hybrid",
    }
    evaluations = {
        publication_names[name]: evaluate_run(entries, qrels)
        for name, entries in runs.items()
    }
    overall = {
        "evaluation_label": DEVELOPMENT_LABEL,
        "recall_semantics": "pooled Recall",
        "systems": {name: value["overall"] for name, value in evaluations.items()},
    }
    rows: list[dict[str, Any]] = []
    for system, value in evaluations.items():
        for query_id, metrics in value["per_query"].items():
            rows.append({"query_id": query_id, "retriever": system, **metrics})
    fields = sorted({key for row in rows for key in row})
    query_records = load_queries(paths.queries)
    slice_rows: list[dict[str, Any]] = []
    for source_name, entries in runs.items():
        retriever = publication_names[source_name]
        slice_rows.extend(
            evaluate_slices(
                entries,
                qrels,
                query_records,
                retriever=retriever,
                confidence_interval_metrics=(
                    "graded_nDCG@10",
                    "MRR@10_rel2",
                    "pooled_Recall@10_rel2",
                    "HitRate@5_rel2",
                ),
                bootstrap_samples=bootstrap_samples,
                random_seed=random_seed,
                confidence_level=confidence_level,
            )
        )
    slice_fields = sorted({key for row in slice_rows for key in row})
    per_query = {name: value["per_query"] for name, value in evaluations.items()}
    confidence_intervals = {
        name: bootstrap_metric_confidence_intervals(
            value,
            n_samples=bootstrap_samples,
            seed=random_seed,
            confidence_level=confidence_level,
        )
        for name, value in per_query.items()
    }
    pairwise_differences = required_pairwise_comparisons(
            per_query,
            n_samples=bootstrap_samples,
            seed=random_seed,
            confidence_level=confidence_level,
        )
    from .reporting import compute_complementarity

    complementarity = compute_complementarity(
        runs["bm25_formal"],
        runs["dense_formal"],
        qrels,
    )

    # Publish the complete metric bundle only after every calculation and
    # staged serialization succeeds.  If any replacement fails, remove all
    # publishable outputs so a partial bundle can never masquerade as PASS.
    publication_names = (
        "overall_metrics.json",
        "per_query_metrics.csv",
        "slice_metrics.csv",
        "confidence_intervals.json",
        "pairwise_differences.json",
        "complementarity_report.json",
    )
    paths.evaluation.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".evaluation-stage-", dir=paths.evaluation
    ) as stage_name:
        stage = Path(stage_name)
        write_json(stage / "overall_metrics.json", overall)
        with (stage / "per_query_metrics.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                sorted(rows, key=lambda row: (row["query_id"], row["retriever"]))
            )
        with (stage / "slice_metrics.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=slice_fields)
            writer.writeheader()
            writer.writerows(slice_rows)
        write_json(stage / "confidence_intervals.json", confidence_intervals)
        write_json(stage / "pairwise_differences.json", pairwise_differences)
        write_json(stage / "complementarity_report.json", complementarity)
        try:
            for name in publication_names:
                os.replace(stage / name, paths.evaluation / name)
        except Exception:
            for name in publication_names:
                destination = paths.evaluation / name
                if destination.is_file():
                    destination.unlink()
            raise
    return overall


def benchmark_command(paths: ProjectPaths) -> dict[str, Any]:
    from .bm25 import load_bm25_index, verify_bm25_index_binding
    from .dense import load_dense_index, verify_dense_index_binding
    from .latency import directory_size, environment_metadata, summarize_latencies
    from .reporting import write_json
    from .rrf import FUSION_DEPTH, OUTPUT_DEPTH, RRF_K, fuse_ranked_lists

    queries = _serialized_pairs(paths)
    records = _corpus(paths)["records"]
    bm25_config = _config(paths, "bm25_baseline.yaml")
    dense_config = _config(paths, "dense_baseline.yaml")
    hybrid_config = _config(paths, "hybrid_rrf_baseline.yaml")
    if (
        int(hybrid_config["rrf_k"]),
        int(hybrid_config["fusion_depth"]),
        int(hybrid_config["output_depth"]),
    ) != (RRF_K, FUSION_DEPTH, OUTPUT_DEPTH):
        raise ValueError("Hybrid config/runtime drift")
    started = time.perf_counter()
    bm25 = load_bm25_index(paths.bm25_index)
    verify_bm25_index_binding(bm25, records, bm25_config)
    bm25_cold = time.perf_counter() - started
    started = time.perf_counter()
    dense = load_dense_index(paths.dense_index)
    verify_dense_index_binding(dense, records, dense_config)
    dense.load_model()
    dense_cold = time.perf_counter() - started
    for query_id, text in queries[:3]:
        bm25.search(query_id, text)
        dense.search_texts([(query_id, text)])
    bm25_samples: list[float] = []
    dense_encode: list[float] = []
    dense_search: list[float] = []
    fusion_samples: list[float] = []
    for query_id, text in queries:
        started = time.perf_counter()
        bm_results = bm25.search(query_id, text)
        bm25_samples.append(time.perf_counter() - started)
        started = time.perf_counter()
        vector = dense.encode_queries([text])
        dense_encode.append(time.perf_counter() - started)
        started = time.perf_counter()
        dense_results = dense.search_vectors([query_id], vector)
        dense_search.append(time.perf_counter() - started)
        started = time.perf_counter()
        fuse_ranked_lists(
            bm_results,
            dense_results,
        )
        fusion_samples.append(time.perf_counter() - started)
    dense_total = [a + b for a, b in zip(dense_encode, dense_search)]
    hybrid_total = [a + b + c + d for a, b, c, d in zip(bm25_samples, dense_encode, dense_search, fusion_samples)]
    dense_meta = dense.metadata
    dense_index_artifacts = sum(
        candidate.stat().st_size
        for candidate in (
            paths.dense_index / "embeddings.npy",
            paths.dense_index / "chunk_ids.json",
            paths.dense_index / "metadata.json",
        )
        if candidate.is_file()
    )
    result = {
        "evaluation_label": "machine-proposed development evaluation",
        "machine_proposed_development_only": True,
        "warmup_queries": 3,
        "query_count": len(queries),
        "repetitions": 1,
        "cold_start_scope": "index/model load plus frozen corpus/config binding validation; excludes process launch",
        "cold_start": {"bm25_seconds": bm25_cold, "dense_seconds": dense_cold},
        "warm_query_latency": {
            "bm25": summarize_latencies(bm25_samples),
            "dense": {
                "query_encoding": summarize_latencies(dense_encode),
                "vector_search": summarize_latencies(dense_search),
                "total": summarize_latencies(dense_total),
            },
            "hybrid": {
                "bm25_component": summarize_latencies(bm25_samples),
                "dense_component": summarize_latencies(dense_total),
                "rrf_fusion": summarize_latencies(fusion_samples),
                "total": summarize_latencies(hybrid_total),
            },
        },
        "build_performance": {
            "bm25_index_build_seconds": bm25.metadata.get("build_seconds"),
            "bm25_index_size_bytes": directory_size(paths.bm25_index),
            "dense_model_load_or_download_seconds": dense_meta.get("model_load_or_download_seconds"),
            "dense_corpus_encoding_seconds": dense_meta.get("corpus_encoding_seconds"),
            "dense_index_build_seconds": dense_meta.get("index_write_seconds"),
            "dense_embedding_index_size_bytes": dense_index_artifacts,
            "dense_model_cache_size_bytes": directory_size(paths.dense_index / "model_cache"),
        },
        "environment": environment_metadata(
            device=str(dense_meta.get("device", "cpu")),
            corpus_chunks=int(dense_meta.get("document_count", 12000)),
            embedding_dimension=int(dense_meta.get("embedding_dimension", 0)),
        ),
    }
    write_json(paths.evaluation / "latency.json", result)
    return result


def validate_command(paths: ProjectPaths) -> dict[str, Any]:
    from .validation import validate_release

    return validate_release(paths)


def test_command(paths: ProjectPaths) -> dict[str, Any]:
    """Run the complete retrieval suite and persist evidence bound to source bytes."""

    test_environment = dict(os.environ)
    test_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    test_environment["PYTHONPATH"] = str(paths.retrieval / "src")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "retrieval/tests",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    source_before = snapshot_release_source(paths)
    completed = subprocess.run(
        command,
        cwd=paths.root,
        env=test_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    source_after = snapshot_release_source(paths)
    source_stable = source_before["tree_sha256"] == source_after["tree_sha256"]
    result = {
        "status": "PASS" if completed.returncode == 0 and source_stable else "FAIL",
        "returncode": completed.returncode,
        "command": command,
        "python_executable": sys.executable,
        "python_version": sys.version,
        "source_tree_sha256": source_before["tree_sha256"],
        "source_file_count": source_before["file_count"],
        "source_tree_sha256_after": source_after["tree_sha256"],
        "source_stable_during_tests": source_stable,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    _write_json(paths.reports / "test_results.json", result)
    if completed.returncode != 0 or not source_stable:
        raise RuntimeError(
            "Retrieval tests failed or source changed during the run; see reports/test_results.json"
        )
    return result


def _finalize_release(paths: ProjectPaths) -> dict[str, Any]:
    from .queries import load_queries
    from .reporting import (
        generate_failure_analysis,
        generate_manifest,
        generate_reports,
        read_json,
    )

    audit_annotation_command(paths)
    corpus, raw_runs, qrel_entries = _load_runs_and_qrels(paths)
    runs = {
        "bm25": raw_runs["bm25_formal"],
        "dense": raw_runs["dense_formal"],
        "hybrid": raw_runs["hybrid_rrf_formal"],
    }
    generate_failure_analysis(
        paths,
        runs,
        _nested_qrels(qrel_entries),
        load_queries(paths.queries),
        corpus["records"],
    )
    pool = read_json(paths.pool_expansion / "pool_expansion_summary.json", {})
    reproduction = read_json(paths.reproduction / "reproduction_report.json", {})
    preliminary = {
        "engineering_status": "PASS",
        "evaluation_integrity_status": pool.get("evaluation_integrity_status", "FAIL"),
        "retrieval_quality_status": (
            "NOT_EVALUATED" if pool.get("pool_expansion_required") else "FAIL"
        ),
        "annotation_reproduction_status": reproduction.get(
            "annotation_reproduction_status", "NOT_REPRODUCIBLE"
        ),
    }
    statuses = preliminary
    previous_validation: dict[str, Any] | None = None
    # Reports include the preceding validation's issue list, while the manifest
    # hashes the reports.  Iterate this small dependency cycle to a fixed point:
    # report -> manifest hashes -> validation -> final statuses.  In a healthy
    # candidate this converges in two passes; an engineering failure that changes
    # the release name may require one additional pass.
    for _attempt in range(5):
        bootstrap_manifest = generate_manifest(paths, statuses)
        generate_reports(paths, statuses, bootstrap_manifest)
        # Reports deliberately do not render their own hash fields, so this
        # second manifest write stably binds their just-written bytes.
        manifest = generate_manifest(paths, statuses)
        validation = validate_command(paths)
        underlying_engineering_pass = all(
            check.get("status") == "PASS"
            for check in validation.get("checks", [])
            if isinstance(check, dict)
            and str(check.get("check_id", "")).startswith("engineering.")
            and check.get("check_id")
            != "engineering.manifest.final_status_binding"
        )
        next_statuses = {
            # A stale status field is itself an engineering failure for a
            # standalone validation pass, but the next fixed-point iteration
            # must write the underlying status it was expected to contain.
            "engineering_status": "PASS" if underlying_engineering_pass else "FAIL",
            "evaluation_integrity_status": validation["evaluation_integrity_status"],
            "retrieval_quality_status": validation["retrieval_quality_status"],
            "annotation_reproduction_status": preliminary[
                "annotation_reproduction_status"
            ],
        }
        if previous_validation == validation and next_statuses == statuses:
            return {"validation": validation, "manifest": manifest}
        previous_validation = validation
        statuses = next_statuses
    raise RuntimeError(
        "Final reports, manifest, and validation statuses did not converge after five passes."
    )


def _all(paths: ProjectPaths) -> dict[str, Any]:
    results: dict[str, Any] = {}
    before = audit_protected_paths(paths, "before")
    results["audit-protected-paths-before"] = {
        "file_count": before["before"]["file_count"],
        "tree_sha256": before["before"]["tree_sha256"],
    }
    steps: list[tuple[str, Callable[[ProjectPaths], dict[str, Any]]]] = [
        ("verify-inputs", verify_inputs),
        ("serialize-queries", serialize_queries_command),
        ("audit-annotation-retrievers", audit_annotation_command),
        ("build-bm25", build_bm25_command),
        ("build-dense", build_dense_command),
        ("run-bm25", run_bm25_command),
        ("run-dense", run_dense_command),
        ("run-hybrid", run_hybrid_command),
        ("check-pool", check_pool_command),
        ("evaluate", evaluate_command),
        ("benchmark", benchmark_command),
    ]
    try:
        for name, function in steps:
            print(f"[sqlmend-retrieval] {name}", flush=True)
            results[name] = function(paths)
        results["test"] = test_command(paths)
    except Exception:
        audit_protected_paths(paths, "after")
        raise
    after = audit_protected_paths(paths, "after")
    results["audit-protected-paths-after"] = {
        "file_count": after["after"]["file_count"],
        "tree_sha256": after["after"]["tree_sha256"],
        "protected_paths_unchanged": after["protected_paths_unchanged"],
    }
    if not after["protected_paths_unchanged"]:
        raise RuntimeError("Protected paths changed")
    results["finalize"] = _finalize_release(paths)
    return results


def _dispatch(paths: ProjectPaths, command: str) -> dict[str, Any]:
    mapping: dict[str, Callable[[ProjectPaths], dict[str, Any]]] = {
        "verify-inputs": verify_inputs,
        "serialize-queries": serialize_queries_command,
        "build-bm25": build_bm25_command,
        "build-dense": build_dense_command,
        "run-bm25": run_bm25_command,
        "run-dense": run_dense_command,
        "run-hybrid": run_hybrid_command,
        "audit-annotation-retrievers": audit_annotation_command,
        "check-pool": check_pool_command,
        "evaluate": evaluate_command,
        "benchmark": benchmark_command,
        "test": test_command,
        "finalize": _finalize_release,
        "validate": validate_command,
        "all": _all,
    }
    return mapping[command](paths)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = ProjectPaths.discover(args.root)
    try:
        if args.command == "audit-protected-paths":
            report = audit_protected_paths(paths, args.phase)
            snapshot = report[args.phase]
            result = {
                "phase": args.phase,
                "file_count": snapshot["file_count"],
                "tree_sha256": snapshot["tree_sha256"],
                "protected_paths_unchanged": report["protected_paths_unchanged"],
            }
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0 if args.phase == "before" or report["protected_paths_unchanged"] else 1
        result = _dispatch(paths, args.command)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
        if args.command == "validate" and result.get("overall_success") is False:
            return 1
        if args.command in {"all", "finalize"}:
            nested = result.get("finalize", result).get("validation", {})
            if nested.get("overall_success") is False:
                return 1
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
