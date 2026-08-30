from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlmend_generation_v1.inputs import (
    FROZEN_CORPUS_SHA256,
    FROZEN_FINAL_RUN_SHA256,
    FROZEN_SERIALIZED_QUERIES_SHA256,
    load_generation_v1_evidence,
    load_prepared_queries,
    prepare_inputs,
)
from sqlmend_generation_v1.io import sha256_file


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RELEASE_ROOT = REPOSITORY_ROOT / "generation" / "generation-v1"


def test_prepare_inputs_uses_only_frozen_safe_queries_and_final_top5(tmp_path: Path) -> None:
    prepared = tmp_path / "prepared_inputs"
    paths = SimpleNamespace(
        config_file=RELEASE_ROOT / "config" / "generation.yaml",
        frozen_serialized_queries=REPOSITORY_ROOT
        / "retrieval"
        / "retrieval-v1"
        / "serialized_queries"
        / "dev_250_queries.jsonl",
        final_retrieval_run=REPOSITORY_ROOT
        / "retrieval"
        / "retrieval-v1"
        / "runs"
        / "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec",
        corpus=REPOSITORY_ROOT / "construction" / "data" / "processed" / "corpus.jsonl",
        prepared_queries=prepared / "online_queries.jsonl",
        generation_v1_evidence=prepared / "generation_v1_evidence_top5.jsonl",
    )

    summary = prepare_inputs(paths)
    queries = load_prepared_queries(paths.prepared_queries)
    evidence = load_generation_v1_evidence(paths.generation_v1_evidence)

    assert summary["query_count"] == 250
    assert len(queries) == len(evidence) == 250
    assert summary["source_hashes"] == {
        "serialized_queries": FROZEN_SERIALIZED_QUERIES_SHA256,
        "final_run": FROZEN_FINAL_RUN_SHA256,
        "corpus": FROZEN_CORPUS_SHA256,
    }
    assert sha256_file(paths.frozen_serialized_queries) == FROZEN_SERIALIZED_QUERIES_SHA256
    first_query = queries[0]
    first_evidence = evidence[first_query.query_id]
    assert first_query.query_id == "DEV0001"
    assert set(first_query.source_fields_used) <= {
        "dialect",
        "version",
        "user_problem",
        "sql",
        "error_message",
        "error_code",
        "sqlstate",
        "error_symbol",
    }
    assert len(first_evidence.passages) == 5
    assert [passage["rank"] for passage in first_evidence.passages] == [1, 2, 3, 4, 5]
    assert first_evidence.passage_ids[0] == "smr_postgresql_2f473dad3bcfab7987275d78"
    forbidden = {
        "expected_root_cause",
        "reference_fix",
        "qrels",
        "relevance",
        "expected_behavior",
        "annotation_evidence",
        "candidate_pool_labels",
    }
    assert forbidden.isdisjoint(first_query.to_record())
    assert all(forbidden.isdisjoint(passage) for passage in first_evidence.passages)


def test_evidence_digest_detects_any_passage_mutation(tmp_path: Path) -> None:
    # Build from the real frozen inputs, then verify the strict loader catches tampering.
    prepared = tmp_path / "prepared_inputs"
    paths = SimpleNamespace(
        config_file=RELEASE_ROOT / "config" / "generation.yaml",
        frozen_serialized_queries=REPOSITORY_ROOT
        / "retrieval"
        / "retrieval-v1"
        / "serialized_queries"
        / "dev_250_queries.jsonl",
        final_retrieval_run=REPOSITORY_ROOT
        / "retrieval"
        / "retrieval-v1"
        / "runs"
        / "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec",
        corpus=REPOSITORY_ROOT / "construction" / "data" / "processed" / "corpus.jsonl",
        prepared_queries=prepared / "online_queries.jsonl",
        generation_v1_evidence=prepared / "generation_v1_evidence_top5.jsonl",
    )
    prepare_inputs(paths)
    lines = paths.generation_v1_evidence.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["passages"][0]["text"] += " tampered"
    lines[0] = json.dumps(first, ensure_ascii=False, separators=(",", ":"))
    paths.generation_v1_evidence.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    import pytest

    with pytest.raises(ValueError, match="evidence_sha256 mismatch"):
        load_generation_v1_evidence(paths.generation_v1_evidence)
