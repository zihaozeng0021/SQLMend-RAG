from __future__ import annotations

import inspect
import json

import numpy as np
import pytest
from rank_bm25 import BM25Okapi

from sqlmend_retrieval.bm25 import (
    BM25Index,
    build_bm25_index,
    load_bm25_index,
    verify_bm25_index_binding,
)
from sqlmend_retrieval.dense import (
    DenseIndex,
    build_dense_index,
    l2_normalize,
    load_dense_index,
    verify_dense_index_binding,
)
from sqlmend_retrieval.hashing import canonical_json_sha256, sha256_file, sha256_text
from sqlmend_retrieval.corpus import passages
from sqlmend_retrieval.tokenization import tokenize


def test_tokenizer_preserves_required_sql_tokens():
    text = """SQLSTATE 42803 DISTINCT ON GROUP_CONCAT json_extract jsonb_path_query
    date_trunc ->> -> :: <= >= <> != 8.0 3.35.0 schema.table"""
    tokens = tokenize(text)
    for expected in (
        "sqlstate",
        "42803",
        "distinct",
        "on",
        "group_concat",
        "json_extract",
        "jsonb_path_query",
        "date_trunc",
        "->>",
        "->",
        "::",
        "<=",
        ">=",
        "<>",
        "!=",
        "8.0",
        "3.35.0",
        "schema.table",
    ):
        assert expected in tokens


def test_bm25_deterministic_tie_breaking_and_finite_scores():
    ids = ["chunk-b", "chunk-a", "chunk-c"]
    model = BM25Okapi([["same"], ["same"], ["other"]], k1=1.5, b=0.75)
    index = BM25Index(ids, model, {"retriever_id": "bm25_formal_v1"})
    first = index.search("Q1", "same", top_k=3)
    second = index.search("Q1", "same", top_k=3)
    assert first == second
    tied_ids = [item.chunk_id for item in first if item.score == first[0].score]
    assert tied_ids == sorted(tied_ids)
    assert len({item.chunk_id for item in first}) == 3
    assert all(np.isfinite(item.score) for item in first)


def test_l2_normalization_and_inner_product_cosine_equivalence():
    vectors = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    normalized = l2_normalize(vectors)
    np.testing.assert_allclose(np.linalg.norm(normalized, axis=1), np.ones(2), atol=1e-7)
    cosine = float(np.dot(vectors[0], vectors[1]) / (np.linalg.norm(vectors[0]) * np.linalg.norm(vectors[1])))
    assert float(normalized[0] @ normalized[1]) == pytest.approx(cosine, abs=1e-7)


def test_dense_exact_search_deterministic_tie_breaking(tmp_path):
    embeddings = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    metadata = {
        "retriever_id": "dense_formal_v1",
        "configuration": {"query_prefix": "query: "},
    }
    index = DenseIndex(["chunk-b", "chunk-a", "chunk-c"], embeddings, metadata, tmp_path)
    results = index.search_vectors(["Q1"], np.array([[1.0, 0.0]], dtype=np.float32), top_k=3)
    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b", "chunk-c"]
    assert len({result.chunk_id for result in results}) == 3


def test_dense_query_and_document_prefixes_are_applied_exactly(tmp_path, monkeypatch):
    captured: list[list[str]] = []

    class FakeModel:
        max_seq_length = 256

        def encode(self, texts, **_kwargs):
            captured.append(list(texts))
            return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

    config = {
        "retriever_id": "dense_formal_v1",
        "model_id": "fixture/model",
        "model_revision": "revision",
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
        "normalize_embeddings": True,
        "batch_size": 2,
        "device": "cpu",
    }
    index = DenseIndex(
        ["c1"],
        np.asarray([[1.0, 0.0]], dtype=np.float32),
        {"retriever_id": "dense_formal_v1", "configuration": config},
        tmp_path,
        _model=FakeModel(),
    )
    index.encode_queries(["SELECT 1"])
    assert captured.pop() == ["query: SELECT 1"]

    import sqlmend_retrieval.dense as dense_module

    fake = FakeModel()
    monkeypatch.setattr(dense_module, "_load_model", lambda *_args, **_kwargs: fake)
    records = [
        {
            "chunk_id": "c1",
            "dialect": "sqlite",
            "title": "Select",
            "section": "Syntax",
            "text": "SELECT returns rows.",
        }
    ]
    build_dense_index(records, tmp_path / "built", config)
    assert len(captured) == 1
    assert captured[0][0].startswith("passage: Title: Select\nSection: Syntax\nText:\n")


def test_retrieval_modules_do_not_import_evaluation_or_annotation_inputs():
    import sqlmend_retrieval.bm25 as bm25
    import sqlmend_retrieval.dense as dense

    combined = inspect.getsource(bm25) + inspect.getsource(dense)
    assert "qrels" not in combined.lower()
    assert "candidate_pool" not in combined.lower()
    assert "source_link" not in combined.lower()


def test_search_executes_when_qrels_and_candidate_pools_do_not_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "qrels").exists()
    assert not (tmp_path / "candidate_pools.jsonl").exists()

    bm25 = BM25Index(
        ["c1", "c2"],
        BM25Okapi([["alpha"], ["beta"]]),
        {"retriever_id": "bm25_formal_v1", "lowercase": True},
    )
    dense = DenseIndex(
        ["c1", "c2"],
        np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        {"retriever_id": "dense_formal_v1", "configuration": {"query_prefix": "query: "}},
        tmp_path,
    )

    assert len(bm25.search("Q1", "alpha", top_k=2)) == 2
    assert len(
        dense.search_vectors(
            ["Q1"], np.asarray([[1.0, 0.0]], dtype=np.float32), top_k=2
        )
    ) == 2


def test_bm25_index_binding_rejects_corpus_and_config_tampering(tmp_path):
    records = [
        {"chunk_id": "c1", "title": "A", "section": "S", "text": "alpha", "dialect": "sqlite"},
        {"chunk_id": "c2", "title": "B", "section": "S", "text": "beta", "dialect": "sqlite"},
    ]
    config = {
        "retriever_id": "bm25_formal_v1",
        "k1": 1.5,
        "b": 0.75,
        "lowercase": True,
        "document_template": "sqlmend-passage-v1",
    }
    build_bm25_index(records, tmp_path, config)
    index = load_bm25_index(tmp_path)
    verify_bm25_index_binding(index, records, config)

    changed_records = [dict(record) for record in records]
    changed_records[0]["text"] = "tampered"
    with pytest.raises(ValueError, match="not bound"):
        verify_bm25_index_binding(index, changed_records, config)
    with pytest.raises(ValueError, match="not bound"):
        verify_bm25_index_binding(index, records, {**config, "k1": 1.6})


def test_dense_loader_and_binding_reject_tampering(tmp_path):
    records = [
        {"chunk_id": "c1", "title": "A", "section": "S", "text": "alpha", "dialect": "sqlite"},
        {"chunk_id": "c2", "title": "B", "section": "S", "text": "beta", "dialect": "sqlite"},
    ]
    config = {
        "retriever_id": "dense_formal_v1",
        "model_id": "example/model",
        "model_revision": "revision",
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
    }
    ordered = sorted(records, key=lambda record: record["chunk_id"])
    chunk_ids = [record["chunk_id"] for record in ordered]
    embeddings = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    embeddings_path = tmp_path / "embeddings.npy"
    chunk_ids_path = tmp_path / "chunk_ids.json"
    np.save(embeddings_path, embeddings, allow_pickle=False)
    chunk_ids_path.write_text(json.dumps(chunk_ids) + "\n", encoding="utf-8")
    metadata = {
        "retriever_id": config["retriever_id"],
        "model_id": config["model_id"],
        "model_revision": config["model_revision"],
        "configuration": config,
        "chunk_order_sha256": sha256_text("\n".join(chunk_ids) + "\n"),
        "corpus_records_sha256": canonical_json_sha256(ordered),
        "rendered_passages_sha256": canonical_json_sha256(passages(ordered)),
        "configuration_sha256": canonical_json_sha256(config),
        "embeddings_sha256": sha256_file(embeddings_path),
        "chunk_ids_sha256": sha256_file(chunk_ids_path),
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    index = load_dense_index(tmp_path)
    verify_dense_index_binding(index, records, config)

    with embeddings_path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_dense_index(tmp_path)
