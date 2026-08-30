"""Pinned zero-shot E5 embeddings with normalized float32 exact search."""

from __future__ import annotations

import importlib.metadata
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .corpus import passages
from .hashing import canonical_json_sha256, sha256_file, sha256_text
from .schemas import SearchResult


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Cannot L2-normalize a zero vector")
    normalized = array / norms
    return np.asarray(normalized, dtype=np.float32)


def _set_determinism(seed: int, cpu_threads: int = 1) -> None:
    random.seed(seed)
    np.random.seed(seed)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        import torch

        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True)
        torch.set_num_threads(max(1, int(cpu_threads)))
    except ImportError:
        pass


def _load_model(config: dict[str, Any], cache_dir: Path):
    from sentence_transformers import SentenceTransformer

    _set_determinism(
        int(config.get("random_seed", 42)),
        int(config.get("cpu_threads", 1)),
    )
    model = SentenceTransformer(
        config["model_id"],
        revision=config["model_revision"],
        device=config.get("device", "cpu"),
        cache_folder=str(cache_dir),
        trust_remote_code=False,
    )
    model.max_seq_length = int(config.get("max_input_length", model.max_seq_length))
    if config.get("model_inference_precision") == "dynamic_int8_cpu":
        import torch

        model = torch.ao.quantization.quantize_dynamic(
            model,
            {torch.nn.Linear},
            dtype=torch.qint8,
            inplace=False,
        )
    return model


def _encode(
    model,
    texts: list[str],
    config: dict[str, Any],
    *,
    show_progress_bar: bool = False,
) -> np.ndarray:
    vectors = model.encode(
        texts,
        batch_size=int(config.get("batch_size", 16)),
        show_progress_bar=show_progress_bar,
        convert_to_numpy=True,
        normalize_embeddings=bool(config.get("normalize_embeddings", True)),
    )
    vectors = np.asarray(vectors, dtype=np.float32)
    if config.get("normalize_embeddings", True):
        vectors = l2_normalize(vectors)
    if not np.all(np.isfinite(vectors)):
        raise ValueError("Dense encoder produced non-finite values")
    return vectors


@dataclass
class DenseIndex:
    chunk_ids: list[str]
    embeddings: np.ndarray
    metadata: dict[str, Any]
    index_dir: Path
    _model: Any = None

    def load_model(self):
        if self._model is None:
            config = self.metadata["configuration"]
            self._model = _load_model(config, self.index_dir / "model_cache")
        return self._model

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        config = self.metadata["configuration"]
        prefix = str(config["query_prefix"])
        return _encode(self.load_model(), [prefix + text for text in texts], config)

    def search_vectors(
        self,
        query_ids: list[str],
        query_vectors: np.ndarray,
        *,
        top_k: int = 30,
    ) -> list[SearchResult]:
        queries = l2_normalize(query_vectors)
        scores = np.asarray(queries @ np.asarray(self.embeddings, dtype=np.float32).T, dtype=np.float32)
        if not np.all(np.isfinite(scores)):
            raise ValueError("Dense search produced non-finite scores")
        results: list[SearchResult] = []
        tag = str(self.metadata["retriever_id"])
        for row, query_id in enumerate(query_ids):
            order = sorted(
                range(len(self.chunk_ids)),
                key=lambda index: (-float(scores[row, index]), self.chunk_ids[index]),
            )
            results.extend(
                SearchResult(query_id, self.chunk_ids[index], rank, float(scores[row, index]), tag)
                for rank, index in enumerate(order[:top_k], start=1)
            )
        return results

    def search_texts(
        self, queries: Iterable[tuple[str, str]], *, top_k: int = 30
    ) -> list[SearchResult]:
        ordered = sorted(queries)
        query_ids = [item[0] for item in ordered]
        vectors = self.encode_queries([item[1] for item in ordered])
        return self.search_vectors(query_ids, vectors, top_k=top_k)


def build_dense_index(
    records: list[dict[str, Any]],
    index_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    index_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda item: item["chunk_id"])
    chunk_ids = [record["chunk_id"] for record in ordered]
    prefix = str(config["document_prefix"])
    texts = [prefix + text for text in passages(ordered)]
    load_started = time.perf_counter()
    model = _load_model(config, index_dir / "model_cache")
    model_load_or_download_seconds = time.perf_counter() - load_started
    encode_started = time.perf_counter()
    embeddings = _encode(model, texts, config, show_progress_bar=True)
    corpus_encoding_seconds = time.perf_counter() - encode_started
    write_started = time.perf_counter()
    embeddings_path = index_dir / "embeddings.npy"
    chunk_ids_path = index_dir / "chunk_ids.json"
    metadata_path = index_dir / "metadata.json"
    np.save(embeddings_path, embeddings, allow_pickle=False)
    chunk_ids_path.write_text(
        json.dumps(chunk_ids, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    index_write_seconds = time.perf_counter() - write_started
    configuration = dict(config)
    metadata = {
        "retriever_id": config["retriever_id"],
        "model_id": config["model_id"],
        "model_revision": config["model_revision"],
        "embedding_dimension": int(embeddings.shape[1]),
        "maximum_input_length": int(config.get("max_input_length", getattr(model, "max_seq_length", 512))),
        "model_inference_precision": config.get("model_inference_precision", "float32"),
        "pooling_method": config.get("pooling", "mean"),
        "query_prefix": config["query_prefix"],
        "document_prefix": config["document_prefix"],
        "normalization": "L2",
        "similarity_function": "inner_product_equivalent_to_cosine",
        "search_method": "exact_matrix_multiplication",
        "dtype": "float32",
        "device": config.get("device", "cpu"),
        "batch_size": int(config.get("batch_size", 16)),
        "document_count": len(chunk_ids),
        "chunk_order": "ascending_chunk_id",
        "chunk_order_sha256": sha256_text("\n".join(chunk_ids) + "\n"),
        "corpus_records_sha256": canonical_json_sha256(ordered),
        "rendered_passages_sha256": canonical_json_sha256(passages(ordered)),
        "configuration_sha256": canonical_json_sha256(config),
        "model_load_or_download_seconds": model_load_or_download_seconds,
        "corpus_encoding_seconds": corpus_encoding_seconds,
        "index_write_seconds": index_write_seconds,
        "embeddings_sha256": sha256_file(embeddings_path),
        "chunk_ids_sha256": sha256_file(chunk_ids_path),
        "embeddings_size_bytes": embeddings_path.stat().st_size,
        "package_versions": {
            name: importlib.metadata.version(name)
            for name in ("sentence-transformers", "transformers", "torch", "numpy", "huggingface-hub")
        },
        "configuration": configuration,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return metadata


def verify_dense_index_binding(
    index: DenseIndex,
    records: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    """Bind dense rows and embeddings to the current corpus/config identity."""

    ordered = sorted(records, key=lambda item: item["chunk_id"])
    expected_ids = [record["chunk_id"] for record in ordered]
    expected = {
        "chunk_order_sha256": sha256_text("\n".join(expected_ids) + "\n"),
        "corpus_records_sha256": canonical_json_sha256(ordered),
        "rendered_passages_sha256": canonical_json_sha256(passages(ordered)),
        "configuration_sha256": canonical_json_sha256(config),
        "retriever_id": config["retriever_id"],
        "model_id": config["model_id"],
        "model_revision": config["model_revision"],
        "configuration": config,
    }
    mismatches = {
        key: {"recorded": index.metadata.get(key), "expected": value}
        for key, value in expected.items()
        if index.metadata.get(key) != value
    }
    if index.chunk_ids != expected_ids:
        mismatches["chunk_ids"] = "index order differs from current corpus"
    if mismatches:
        raise ValueError(f"Dense index is not bound to current corpus/config: {mismatches}")


def load_dense_index(index_dir: Path) -> DenseIndex:
    metadata_path = index_dir / "metadata.json"
    embeddings_path = index_dir / "embeddings.npy"
    chunk_ids_path = index_dir / "chunk_ids.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if sha256_file(embeddings_path) != metadata["embeddings_sha256"]:
        raise ValueError("Dense embeddings hash mismatch")
    if sha256_file(chunk_ids_path) != metadata["chunk_ids_sha256"]:
        raise ValueError("Dense chunk-ID mapping hash mismatch")
    chunk_ids = json.loads(chunk_ids_path.read_text(encoding="utf-8"))
    embeddings = np.load(embeddings_path, mmap_mode="r", allow_pickle=False)
    if embeddings.shape[0] != len(chunk_ids):
        raise ValueError("Dense embedding/chunk-ID row count mismatch")
    return DenseIndex(chunk_ids, embeddings, metadata, index_dir)
