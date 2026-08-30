"""Build leakage-safe online inputs from frozen Retrieval-v1 artifacts."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    EVIDENCE_SCHEMA_VERSION,
    FINAL_RETRIEVAL_SYSTEM_ID,
    QUERY_SCHEMA_VERSION,
    EvidenceRow,
    GenerationConfig,
    PreparedQuery,
)
from .io import (
    load_jsonl,
    load_yaml,
    read_trec_run,
    sha256_file,
    sha256_json,
    write_jsonl,
)
from .paths import ProjectPaths


FROZEN_SERIALIZED_QUERIES_SHA256 = (
    "e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c"
)
FROZEN_FINAL_RUN_SHA256 = (
    "774d2d1c90e8e8d58479130a9e016e8a4699cd9ff4b8f72dbf95a3b6f49be566"
)
FROZEN_CORPUS_SHA256 = (
    "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3"
)
EXPECTED_FINAL_RUN_DEPTH = 30

_FROZEN_QUERY_FIELDS = frozenset(
    {
        "query_id",
        "source_fields_used",
        "serialized_text",
        "serialized_text_sha256",
        "serializer_version",
    }
)
_PASSAGE_FIELDS = (
    "dialect",
    "version",
    "version_min",
    "version_max",
    "version_status",
    "source_type",
    "source_name",
    "source_url",
    "title",
    "section",
    "content_hash",
)


def load_generation_config(path: Path) -> GenerationConfig:
    return GenerationConfig.from_mapping(load_yaml(path))


def _project_frozen_query(record: Mapping[str, Any]) -> PreparedQuery:
    if set(record) != _FROZEN_QUERY_FIELDS:
        raise ValueError("Frozen serialized query contains an unexpected field")
    projected = dict(record)
    projected["schema_version"] = QUERY_SCHEMA_VERSION
    return PreparedQuery.from_record(projected)


def load_prepared_queries(path: Path) -> list[PreparedQuery]:
    queries = [PreparedQuery.from_record(record) for record in load_jsonl(path)]
    ids = [query.query_id for query in queries]
    if not queries:
        raise ValueError("Prepared query file is empty")
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("Prepared query IDs must be unique and sorted")
    return queries


def load_g1_evidence(path: Path) -> dict[str, EvidenceRow]:
    rows = [EvidenceRow.from_record(record) for record in load_jsonl(path)]
    ids = [row.query_id for row in rows]
    if not rows:
        raise ValueError("G1 evidence file is empty")
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("G1 evidence query IDs must be unique and sorted")
    if len({row.run_sha256 for row in rows}) != 1:
        raise ValueError("G1 evidence rows do not share one run hash")
    return {row.query_id: row for row in rows}


def _validate_final_run(
    rows: list[dict[str, Any]],
    *,
    query_ids: tuple[str, ...],
    top_k: int,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["run_tag"] != FINAL_RETRIEVAL_SYSTEM_ID:
            raise ValueError(f"Unexpected Final run tag: {row['run_tag']!r}")
        grouped[str(row["query_id"])].append(row)
    if set(grouped) != set(query_ids):
        raise ValueError("Final run query coverage differs from safe queries")
    selected: dict[str, list[dict[str, Any]]] = {}
    for query_id in query_ids:
        query_rows = sorted(grouped[query_id], key=lambda item: int(item["rank"]))
        if len(query_rows) != EXPECTED_FINAL_RUN_DEPTH:
            raise ValueError(f"{query_id} Final run depth is not 30")
        if [row["rank"] for row in query_rows] != list(range(1, EXPECTED_FINAL_RUN_DEPTH + 1)):
            raise ValueError(f"{query_id} Final run ranks are not continuous")
        passage_ids = [str(row["passage_id"]) for row in query_rows]
        if len(passage_ids) != len(set(passage_ids)):
            raise ValueError(f"{query_id} Final run contains duplicate passage IDs")
        selected[query_id] = query_rows[:top_k]
    return selected


def _load_selected_corpus(path: Path, passage_ids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if not raw.strip():
                raise ValueError(f"Blank corpus line at {path}:{line_number}")
            import json

            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError(f"Corpus row is not an object at {path}:{line_number}")
            chunk_id = record.get("chunk_id")
            if chunk_id in passage_ids:
                if chunk_id in selected:
                    raise ValueError(f"Duplicate selected corpus passage: {chunk_id}")
                selected[str(chunk_id)] = record
    missing = sorted(passage_ids - set(selected))
    if missing:
        raise ValueError(f"Final run passages are missing from corpus: {missing[:3]}")
    return selected


def _passage_record(run_row: Mapping[str, Any], corpus_record: Mapping[str, Any]) -> dict[str, Any]:
    text = corpus_record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Empty corpus text: {run_row['passage_id']}")
    result: dict[str, Any] = {
        "passage_id": str(run_row["passage_id"]),
        "rank": int(run_row["rank"]),
        "score": float(run_row["score"]),
    }
    for field in _PASSAGE_FIELDS:
        value = corpus_record.get(field)
        result[field] = value if isinstance(value, str) else None
    result["text"] = text
    # Keep the schema-defined order, independent of corpus field order.
    return {
        "passage_id": result["passage_id"],
        "rank": result["rank"],
        "score": result["score"],
        "dialect": result["dialect"],
        "version": result["version"],
        "version_min": result["version_min"],
        "version_max": result["version_max"],
        "version_status": result["version_status"],
        "source_type": result["source_type"],
        "source_name": result["source_name"],
        "source_url": result["source_url"],
        "title": result["title"],
        "section": result["section"],
        "text": result["text"],
        "content_hash": result["content_hash"],
    }


def prepare_inputs(paths: ProjectPaths) -> dict[str, Any]:
    """Materialize the safe query projection and G1 Final Top-5 evidence.

    This function has no annotation, qrels, expected answer, reference fix, or
    relevance-label path.  The three permitted frozen inputs are hash-pinned
    before any prepared artifact is written.
    """

    config = load_generation_config(paths.config_file)
    expected_hashes = {
        paths.frozen_serialized_queries: FROZEN_SERIALIZED_QUERIES_SHA256,
        paths.final_retrieval_run: FROZEN_FINAL_RUN_SHA256,
        paths.corpus: FROZEN_CORPUS_SHA256,
    }
    for path, expected in expected_hashes.items():
        observed = sha256_file(path)
        if observed != expected:
            raise ValueError(f"Frozen online input hash mismatch for {path}: {observed}")

    frozen_query_records = load_jsonl(paths.frozen_serialized_queries)
    queries = [_project_frozen_query(record) for record in frozen_query_records]
    query_ids = tuple(query.query_id for query in queries)
    if len(queries) != config.expected_query_count:
        raise ValueError(
            f"Safe query count is {len(queries)}, expected {config.expected_query_count}"
        )
    if list(query_ids) != sorted(query_ids) or len(query_ids) != len(set(query_ids)):
        raise ValueError("Safe query IDs must be unique and sorted")

    selected_run = _validate_final_run(
        read_trec_run(paths.final_retrieval_run),
        query_ids=query_ids,
        top_k=config.top_k,
    )
    selected_ids = {
        str(row["passage_id"])
        for query_rows in selected_run.values()
        for row in query_rows
    }
    corpus_by_id = _load_selected_corpus(paths.corpus, selected_ids)

    evidence_records: list[dict[str, Any]] = []
    for query_id in query_ids:
        passages = [
            _passage_record(row, corpus_by_id[str(row["passage_id"])])
            for row in selected_run[query_id]
        ]
        evidence: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "query_id": query_id,
            "retrieval_system_id": config.retrieval_system_id,
            "top_k": config.top_k,
            "run_sha256": FROZEN_FINAL_RUN_SHA256,
            "passages": passages,
        }
        evidence["evidence_sha256"] = sha256_json(evidence)
        EvidenceRow.from_record(evidence)
        evidence_records.append(evidence)

    write_jsonl(paths.prepared_queries, (query.to_record() for query in queries))
    write_jsonl(paths.g1_evidence, evidence_records)
    # Reload the exact bytes that downstream generation will consume.
    reloaded_queries = load_prepared_queries(paths.prepared_queries)
    reloaded_evidence = load_g1_evidence(paths.g1_evidence)
    if [query.query_id for query in reloaded_queries] != list(query_ids):
        raise ValueError("Prepared query round trip changed the query universe")
    if set(reloaded_evidence) != set(query_ids):
        raise ValueError("Prepared G1 evidence differs from the query universe")
    return {
        "schema_version": "sqlmend-prepared-input-summary-v1",
        "query_count": len(queries),
        "top_k": config.top_k,
        "retrieval_system_id": config.retrieval_system_id,
        "source_hashes": {
            "serialized_queries": FROZEN_SERIALIZED_QUERIES_SHA256,
            "final_run": FROZEN_FINAL_RUN_SHA256,
            "corpus": FROZEN_CORPUS_SHA256,
        },
        "artifacts": {
            "queries": str(paths.prepared_queries),
            "queries_sha256": sha256_file(paths.prepared_queries),
            "g1_evidence": str(paths.g1_evidence),
            "g1_evidence_sha256": sha256_file(paths.g1_evidence),
        },
    }
