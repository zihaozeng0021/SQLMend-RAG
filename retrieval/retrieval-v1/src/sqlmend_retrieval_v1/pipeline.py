"""Leakage-safe run construction for the four retrieval-v1 systems."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Mapping

from .io import (
    load_jsonl,
    load_json,
    load_yaml,
    read_trec_run,
    render_trec_run,
    sha256_file,
    sha256_text,
    validate_run,
    write_json,
    write_jsonl,
    write_trec_run,
)
from .models import CandidatePassage, OnlineQuery, RunEntry
from .paths import ProjectPaths
from .query import project_online_queries, write_serialized_queries
from .ranking import (
    CandidateState,
    candidate_pair_set,
    rank_metadata_aware,
    reconstruct_rrf_candidates,
    verify_frozen_hybrid_reconstruction,
    render_passage,
)


CORPUS_SHA256 = "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3"
QUERY_SHA256 = "2ce81dd27690795266fc5cc813dc1999f8c55d86ed1605fd6e1013213a416fae"
QRELS_SHA256 = "eae4aefdf6c152df36330a00adf29d8a40d2ea42a476ed7df8c0f675d7446e5d"
SERIALIZED_QUERY_SHA256 = "e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c"
BM25_RUN_SHA256 = "e72361668fc3338abac657a04c598eb36983e8a8201e506e34084d474e268f98"
DENSE_RUN_SHA256 = "eeada87a6e1457f91a577e8c6d7a3d60cb59854523a4e31a4fff81b023513cdd"
HYBRID_RUN_SHA256 = "05a907f5ab05c3e09aad872d8523db74fd61c77bf34a4108e55c7c9fc667a468"
EXPECTED_DIALECTS = {"postgresql", "mysql", "sqlite", "mariadb", "duckdb"}

SYSTEM_CONFIG_FILES = {
    "hybrid_rrf_frozen_control_v1": "frozen_hybrid_control.yaml",
    "hybrid_rrf_dialect_aware_v1": "dialect_aware.yaml",
    "hybrid_rrf_version_aware_v1": "version_aware.yaml",
    "hybrid_rrf_dialect_version_aware_v1": "dialect_version_aware.yaml",
    "hybrid_rrf_dialect_version_lexical_rerank_v1": "dialect_version_reranker.yaml",
}
RUN_FILES = {
    "hybrid_rrf_dialect_aware_v1": "hybrid_rrf_dialect_aware_dev250.trec",
    "hybrid_rrf_version_aware_v1": "hybrid_rrf_version_aware_dev250.trec",
    "hybrid_rrf_dialect_version_aware_v1": "hybrid_rrf_dialect_version_aware_dev250.trec",
    "hybrid_rrf_dialect_version_lexical_rerank_v1": "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec",
}


@dataclass(slots=True)
class OnlineInputs:
    online_queries: dict[str, OnlineQuery]
    corpus_records: list[dict[str, Any]]
    corpus_by_id: dict[str, dict[str, Any]]
    bm25_run: list[RunEntry]
    dense_run: list[RunEntry]
    frozen_hybrid_run: list[RunEntry]
    candidates: dict[str, list[CandidateState]]


def load_system_configs(paths: ProjectPaths) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for expected_id, filename in SYSTEM_CONFIG_FILES.items():
        config = load_yaml(paths.system_configs / filename)
        if config.get("system_id") != expected_id:
            raise ValueError(f"System config {filename} has an unexpected system_id")
        configs[expected_id] = config
    if len({config["run_tag"] for config in configs.values()}) != len(configs):
        raise ValueError("Every system must have an independent run tag")
    return configs


def _validate_corpus(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(records) != 12_000:
        raise ValueError(f"Corpus record count is {len(records)}, expected 12000")
    result: dict[str, dict[str, Any]] = {}
    dialects: Counter[str] = Counter()
    for record in records:
        chunk_id = record.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in result:
            raise ValueError(f"Invalid or duplicate corpus chunk_id: {chunk_id!r}")
        dialect = record.get("dialect")
        if dialect not in EXPECTED_DIALECTS:
            raise ValueError(f"Invalid corpus dialect for {chunk_id}: {dialect!r}")
        if not isinstance(record.get("text"), str) or not record["text"].strip():
            raise ValueError(f"Empty corpus text for {chunk_id}")
        result[chunk_id] = record
        dialects[dialect] += 1
    if dialects != Counter({dialect: 2400 for dialect in EXPECTED_DIALECTS}):
        raise ValueError(f"Unexpected corpus dialect distribution: {dict(dialects)}")
    return result


def _validate_queries(records: list[dict[str, Any]]) -> None:
    if len(records) != 250:
        raise ValueError(f"Query count is {len(records)}, expected 250")
    query_ids = [record.get("query_id") for record in records]
    if any(not isinstance(query_id, str) or not query_id for query_id in query_ids):
        raise ValueError("Every query must have a non-empty query_id")
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("Query IDs must be unique")
    dialects = Counter(record.get("dialect") for record in records)
    if dialects != Counter({dialect: 50 for dialect in EXPECTED_DIALECTS}):
        raise ValueError(f"Unexpected query dialect distribution: {dict(dialects)}")


def verify_and_load_online_inputs(paths: ProjectPaths, *, write_serialized: bool = True) -> OnlineInputs:
    """Load only fields and artifacts permitted on the online ranking path.

    Qrels, case flags, relevance labels, reference fixes, and protected-path
    audits deliberately do not participate in this function.  Full baseline
    byte auditing belongs to the offline release-validation path.
    """

    expected_hashes = {
        paths.corpus: CORPUS_SHA256,
        paths.queries: QUERY_SHA256,
        paths.baseline_bm25_run: BM25_RUN_SHA256,
        paths.baseline_dense_run: DENSE_RUN_SHA256,
        paths.baseline_run: HYBRID_RUN_SHA256,
        paths.baseline_serialized_queries: SERIALIZED_QUERY_SHA256,
    }
    for path, expected in expected_hashes.items():
        observed = sha256_file(path)
        if observed != expected:
            raise ValueError(f"Input hash mismatch for {path}: {observed}")

    corpus_records = load_jsonl(paths.corpus)
    corpus_by_id = _validate_corpus(corpus_records)
    raw_queries = load_jsonl(paths.queries)
    _validate_queries(raw_queries)
    online_values = project_online_queries(raw_queries)
    online_queries = {query.query_id: query for query in online_values}
    if write_serialized:
        write_serialized_queries(raw_queries, paths.serialized_queries)
        if sha256_file(paths.serialized_queries) != SERIALIZED_QUERY_SHA256:
            raise ValueError("Retrieval-v1 serialization differs from the frozen safe serializer")
        if paths.serialized_queries.read_bytes() != paths.baseline_serialized_queries.read_bytes():
            raise ValueError("Retrieval-v1 serialized query bytes differ from the frozen artifact")

    configs = load_system_configs(paths)
    control = configs["hybrid_rrf_frozen_control_v1"]
    bm25_run = validate_run(
        read_trec_run(paths.baseline_bm25_run),
        expected_query_ids=online_queries,
        known_chunk_ids=corpus_by_id,
        expected_run_tag="bm25_formal_v1",
    )
    dense_run = validate_run(
        read_trec_run(paths.baseline_dense_run),
        expected_query_ids=online_queries,
        known_chunk_ids=corpus_by_id,
        expected_run_tag="dense_formal_v1",
    )
    frozen_hybrid_run = validate_run(
        read_trec_run(paths.baseline_run),
        expected_query_ids=online_queries,
        known_chunk_ids=corpus_by_id,
        expected_run_tag=str(control["run_tag"]),
    )
    candidates = reconstruct_rrf_candidates(bm25_run, dense_run, corpus_by_id)
    verify_frozen_hybrid_reconstruction(candidates, frozen_hybrid_run)
    return OnlineInputs(
        online_queries=online_queries,
        corpus_records=corpus_records,
        corpus_by_id=corpus_by_id,
        bm25_run=bm25_run,
        dense_run=dense_run,
        frozen_hybrid_run=frozen_hybrid_run,
        candidates=candidates,
    )


def candidate_passage_index(inputs: OnlineInputs) -> dict[str, CandidatePassage]:
    index: dict[str, CandidatePassage] = {}
    for states in inputs.candidates.values():
        for state in states:
            existing = index.get(state.passage.chunk_id)
            if existing is None:
                index[state.passage.chunk_id] = state.passage
            elif (
                existing.dialect,
                existing.version,
                existing.version_min,
                existing.version_max,
                existing.version_status,
                existing.text,
            ) != (
                state.passage.dialect,
                state.passage.version,
                state.passage.version_min,
                state.passage.version_max,
                state.passage.version_status,
                state.passage.text,
            ):
                raise ValueError(f"Inconsistent candidate passage metadata: {state.passage.chunk_id}")
    return index


def corpus_passage_index(inputs: OnlineInputs) -> dict[str, CandidatePassage]:
    """Project every corpus record to corpus-owned fields for offline audit/evaluation."""

    result: dict[str, CandidatePassage] = {}
    for chunk_id, record in sorted(inputs.corpus_by_id.items()):
        result[chunk_id] = CandidatePassage(
            chunk_id=chunk_id,
            dialect=record.get("dialect") if isinstance(record.get("dialect"), str) else None,
            version=record.get("version") if isinstance(record.get("version"), str) else None,
            version_min=record.get("version_min") if isinstance(record.get("version_min"), str) else None,
            version_max=record.get("version_max") if isinstance(record.get("version_max"), str) else None,
            version_status=str(record.get("version_status") or "unknown"),
            source_type=record.get("source_type") if isinstance(record.get("source_type"), str) else None,
            title=record.get("title") if isinstance(record.get("title"), str) else None,
            section=record.get("section") if isinstance(record.get("section"), str) else None,
            text=render_passage(record),
            baseline_rank=1,
            baseline_score=0.0,
        )
    return result


def _provenance_path(paths: ProjectPaths, system_id: str) -> Path:
    return paths.runs / RUN_FILES[system_id].replace(".trec", ".provenance.jsonl")


def _run_path(paths: ProjectPaths, system_id: str) -> Path:
    return paths.runs / RUN_FILES[system_id]


def build_metadata_runs(
    paths: ProjectPaths,
    inputs: OnlineInputs,
) -> tuple[
    dict[str, list[RunEntry]],
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, list[dict[str, Any]]]],
]:
    configs = load_system_configs(paths)
    runs: dict[str, list[RunEntry]] = {
        "hybrid_rrf_frozen_control_v1": inputs.frozen_hybrid_run
    }
    provenance: dict[str, list[dict[str, Any]]] = {}
    all_scored_by_system: dict[str, dict[str, list[dict[str, Any]]]] = {}
    determinism: dict[str, Any] = {}
    for system_id in (
        "hybrid_rrf_dialect_aware_v1",
        "hybrid_rrf_version_aware_v1",
        "hybrid_rrf_dialect_version_aware_v1",
    ):
        first, first_provenance, all_scored = rank_metadata_aware(
            inputs.candidates, inputs.online_queries, configs[system_id]
        )
        second, second_provenance, second_all = rank_metadata_aware(
            inputs.candidates, inputs.online_queries, configs[system_id]
        )
        first_bytes = render_trec_run(first)
        second_bytes = render_trec_run(second)
        if first_bytes != second_bytes or first_provenance != second_provenance or all_scored != second_all:
            raise ValueError(f"Repeated metadata-aware ranking differs for {system_id}")
        validate_run(
            first,
            expected_query_ids=inputs.online_queries,
            known_chunk_ids=inputs.corpus_by_id,
            expected_run_tag=str(configs[system_id]["run_tag"]),
        )
        write_trec_run(_run_path(paths, system_id), first)
        write_jsonl(_provenance_path(paths, system_id), first_provenance)
        runs[system_id] = first
        provenance[system_id] = first_provenance
        all_scored_by_system[system_id] = all_scored
        determinism[system_id] = {
            "byte_identical": True,
            "first_sha256": sha256_text(first_bytes),
            "second_sha256": sha256_text(second_bytes),
            "provenance_identical": True,
        }

    candidate_pairs = candidate_pair_set(inputs.candidates)
    write_json(
        paths.reports / "candidate_union.json",
        {
            "schema_version": "sqlmend-retrieval-v1-candidate-union-v1",
            "source": "frozen BM25 top30 union frozen Dense top30, scored with frozen RRF k=60",
            "query_count": len(inputs.candidates),
            "unique_query_chunk_pair_count": len(candidate_pairs),
            "minimum_candidates_per_query": min(len(rows) for rows in inputs.candidates.values()),
            "maximum_candidates_per_query": max(len(rows) for rows in inputs.candidates.values()),
            "all_candidates_come_from_frozen_component_runs": True,
        },
    )
    write_json(
        paths.evaluation / "run_determinism.json",
        {
            "schema_version": "sqlmend-retrieval-v1-determinism-v1",
            "systems": determinism,
        },
    )
    return runs, provenance, all_scored_by_system


def build_all_runs(
    paths: ProjectPaths,
    inputs: OnlineInputs,
) -> dict[str, list[RunEntry]]:
    """Build all four new runs; this function has no qrels/evaluation input."""

    from .reranker import build_corpus_lexical_index, rank_field_aware

    runs, _provenance, all_metadata = build_metadata_runs(paths, inputs)
    configs = load_system_configs(paths)
    final_id = "hybrid_rrf_dialect_version_lexical_rerank_v1"
    final_config = configs[final_id]
    index_started = time.perf_counter()
    lexical_index = build_corpus_lexical_index(inputs.corpus_by_id)
    index_seconds = time.perf_counter() - index_started
    write_json(
        paths.release / "indices" / "reranker" / "metadata.json",
        {
            "schema_version": "sqlmend-field-lexical-index-v1",
            "algorithm": final_config["algorithm"],
            "tokenizer_version": lexical_index.tokenizer_version,
            "document_count": lexical_index.document_count,
            "vocabulary_size": len(lexical_index.inverse_document_frequencies),
            "average_document_length": lexical_index.average_document_length,
            "k1": lexical_index.k1,
            "b": lexical_index.b,
            "corpus_sha256": CORPUS_SHA256,
            "build_seconds": index_seconds,
            "persistence": "statistics are deterministically rebuilt; no pickle is loaded",
        },
    )
    combined_id = "hybrid_rrf_dialect_version_aware_v1"
    arguments = {
        "gamma": float(final_config["gamma"]),
        "run_tag": str(final_config["run_tag"]),
        "output_depth": int(final_config["output_depth"]),
    }
    first, first_provenance, first_all = rank_field_aware(
        inputs.candidates,
        inputs.online_queries,
        all_metadata[combined_id],
        lexical_index,
        **arguments,
    )
    second, second_provenance, second_all = rank_field_aware(
        inputs.candidates,
        inputs.online_queries,
        all_metadata[combined_id],
        lexical_index,
        **arguments,
    )
    first_bytes = render_trec_run(first)
    second_bytes = render_trec_run(second)
    if first_bytes != second_bytes or first_provenance != second_provenance or first_all != second_all:
        raise ValueError("Repeated field-aware reranking differs")
    validate_run(
        first,
        expected_query_ids=inputs.online_queries,
        known_chunk_ids=inputs.corpus_by_id,
        expected_run_tag=str(final_config["run_tag"]),
    )
    union_pairs = candidate_pair_set(inputs.candidates)
    if any((row.query_id, row.chunk_id) not in union_pairs for row in first):
        raise ValueError("Reranker introduced a candidate outside the frozen component union")
    write_trec_run(_run_path(paths, final_id), first)
    write_jsonl(_provenance_path(paths, final_id), first_provenance)
    runs[final_id] = first

    determinism_path = paths.evaluation / "run_determinism.json"
    determinism = load_json(determinism_path)
    determinism["systems"][final_id] = {
        "byte_identical": True,
        "first_sha256": sha256_text(first_bytes),
        "second_sha256": sha256_text(second_bytes),
        "provenance_identical": True,
    }
    write_json(determinism_path, determinism)
    return runs
