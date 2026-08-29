"""Formal BM25Okapi index and exact deterministic search."""

from __future__ import annotations

import json
import math
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from rank_bm25 import BM25Okapi

from .corpus import passages
from .hashing import canonical_json_sha256, sha256_file, sha256_text
from .schemas import SearchResult
from .tokenization import TOKENIZER_VERSION, tokenize


@dataclass
class BM25Index:
    chunk_ids: list[str]
    model: BM25Okapi
    metadata: dict[str, Any]

    def search(self, query_id: str, query_text: str, *, top_k: int = 30) -> list[SearchResult]:
        scores = np.asarray(
            self.model.get_scores(
                tokenize(query_text, lowercase=bool(self.metadata.get("lowercase", True)))
            ),
            dtype=np.float64,
        )
        if scores.shape != (len(self.chunk_ids),):
            raise ValueError("BM25 returned an unexpected score vector shape")
        if not np.all(np.isfinite(scores)):
            raise ValueError("BM25 produced a non-finite score")
        order = sorted(range(len(self.chunk_ids)), key=lambda index: (-float(scores[index]), self.chunk_ids[index]))
        run_tag = str(self.metadata["retriever_id"])
        return [
            SearchResult(query_id, self.chunk_ids[index], rank, float(scores[index]), run_tag)
            for rank, index in enumerate(order[:top_k], start=1)
        ]


def build_bm25_index(
    records: list[dict[str, Any]],
    index_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: item["chunk_id"])
    chunk_ids = [record["chunk_id"] for record in ordered]
    rendered = passages(ordered)
    start = time.perf_counter()
    tokenized = [tokenize(text, lowercase=bool(config.get("lowercase", True))) for text in rendered]
    model = BM25Okapi(
        tokenized,
        k1=float(config["k1"]),
        b=float(config["b"]),
    )
    build_seconds = time.perf_counter() - start
    index_dir.mkdir(parents=True, exist_ok=True)
    payload_path = index_dir / "index.pkl"
    metadata_path = index_dir / "metadata.json"
    with payload_path.open("wb") as stream:
        pickle.dump({"chunk_ids": chunk_ids, "model": model}, stream, protocol=5)
    metadata = {
        "retriever_id": config["retriever_id"],
        "algorithm": "rank_bm25.BM25Okapi",
        "rank_bm25_version": "0.2.2",
        "k1": float(config["k1"]),
        "b": float(config["b"]),
        "lowercase": bool(config.get("lowercase", True)),
        "stemming": False,
        "stopword_removal": False,
        "tokenizer_version": TOKENIZER_VERSION,
        "document_template": config.get("document_template", "sqlmend-passage-v1"),
        "document_count": len(chunk_ids),
        "chunk_order": "ascending_chunk_id",
        "chunk_order_sha256": sha256_text("\n".join(chunk_ids) + "\n"),
        "corpus_records_sha256": canonical_json_sha256(ordered),
        "rendered_passages_sha256": canonical_json_sha256(rendered),
        "configuration_sha256": canonical_json_sha256(config),
        "build_seconds": build_seconds,
        "payload_sha256": sha256_file(payload_path),
        "payload_size_bytes": payload_path.stat().st_size,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return metadata


def verify_bm25_index_binding(
    index: BM25Index,
    records: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    """Bind a loaded index to the current corpus rendering and frozen config."""

    ordered = sorted(records, key=lambda item: item["chunk_id"])
    expected = {
        "chunk_order_sha256": sha256_text(
            "\n".join(record["chunk_id"] for record in ordered) + "\n"
        ),
        "corpus_records_sha256": canonical_json_sha256(ordered),
        "rendered_passages_sha256": canonical_json_sha256(passages(ordered)),
        "configuration_sha256": canonical_json_sha256(config),
        "retriever_id": config["retriever_id"],
        "k1": float(config["k1"]),
        "b": float(config["b"]),
        "lowercase": bool(config.get("lowercase", True)),
        "document_template": config.get("document_template", "sqlmend-passage-v1"),
    }
    mismatches = {
        key: {"recorded": index.metadata.get(key), "expected": value}
        for key, value in expected.items()
        if index.metadata.get(key) != value
    }
    if index.chunk_ids != [record["chunk_id"] for record in ordered]:
        mismatches["chunk_ids"] = "index order differs from current corpus"
    if mismatches:
        raise ValueError(f"BM25 index is not bound to current corpus/config: {mismatches}")


def load_bm25_index(index_dir: Path) -> BM25Index:
    payload_path = index_dir / "index.pkl"
    metadata_path = index_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if sha256_file(payload_path) != metadata["payload_sha256"]:
        raise ValueError("BM25 index payload hash mismatch")
    with payload_path.open("rb") as stream:
        payload = pickle.load(stream)
    return BM25Index(payload["chunk_ids"], payload["model"], metadata)


def run_bm25(
    index: BM25Index,
    queries: Iterable[tuple[str, str]],
    *,
    top_k: int = 30,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for query_id, query_text in sorted(queries):
        results.extend(index.search(query_id, query_text, top_k=top_k))
    return results
