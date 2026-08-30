"""Independent reproduction of annotation-stage retrieval rankings.

This module intentionally mirrors the *documented historical* inputs and
algorithms, including annotation-only query fields, solely inside the
provenance audit.  Its outputs never feed the formal retrieval baselines.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import heapq
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import urllib.parse
import urllib.request
from typing import Any, Iterable

import numpy as np

from .hashing import sha256_file
from .paths import ProjectPaths
from .trec import TrecRunEntry, write_trec_run


HISTORICAL_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}|[0-9]+(?:\.[0-9]+)*")
REPRODUCTION_VERSION = "sqlmend-annotation-reproduction-v1"


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank JSONL line {path}:{line_number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"non-object JSONL line {path}:{line_number}")
            rows.append(value)
    return rows


def _historical_tokens(text: str) -> list[str]:
    return [token.lower() for token in HISTORICAL_TOKEN_RE.findall(text or "")]


def _historical_query_text(case: dict[str, Any]) -> str:
    parts = [
        str(case.get("user_problem") or ""),
        str(case.get("sql") or ""),
        str(case.get("error_message") or ""),
        str(case.get("expected_behavior") or ""),
        str(case.get("dialect") or ""),
        str(case.get("version") or ""),
    ]
    return "\n".join(part for part in parts if part)


def _historical_corpus_text(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(record.get("title") or ""),
            str(record.get("section") or ""),
            str(record.get("text") or ""),
            str(record.get("dialect") or ""),
            str(record.get("version") or ""),
        ]
    )


class HistoricalBM25:
    """Independent implementation of the saved annotation-stage BM25."""

    def __init__(self, texts: list[str], *, k1: float, b: float) -> None:
        self.k1 = k1
        self.b = b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for index, text in enumerate(texts):
            counts = Counter(_historical_tokens(text))
            self.lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                self.postings[term].append((index, frequency))
        self.document_count = len(texts)
        self.average_length = sum(self.lengths) / max(1, self.document_count)

    def search(self, text: str, top_k: int) -> list[tuple[int, float]]:
        scores: dict[int, float] = defaultdict(float)
        for term, query_frequency in Counter(_historical_tokens(text)).items():
            postings = self.postings.get(term)
            if not postings:
                continue
            document_frequency = len(postings)
            inverse_document_frequency = math.log(
                1.0
                + (self.document_count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            query_weight = 1.0 + math.log(query_frequency)
            for document_index, term_frequency in postings:
                length_norm = self.k1 * (
                    1.0
                    - self.b
                    + self.b * self.lengths[document_index] / self.average_length
                )
                scores[document_index] += (
                    inverse_document_frequency
                    * query_weight
                    * (term_frequency * (self.k1 + 1.0))
                    / (term_frequency + length_norm)
                )
        return heapq.nlargest(
            top_k, scores.items(), key=lambda item: (item[1], -item[0])
        )


def _historical_dense(
    corpus_texts: list[str],
    query_texts: list[str],
    *,
    top_k: int,
    model_name: str,
    model_path: Path,
) -> list[list[tuple[int, float]]]:
    from fastembed import TextEmbedding

    model = TextEmbedding(
        model_name=model_name,
        cache_dir=str(model_path.parent),
        specific_model_path=str(model_path),
        local_files_only=True,
        threads=4,
    )
    corpus_dense = np.asarray(
        list(model.embed(corpus_texts, batch_size=128)), dtype=np.float32
    )
    query_dense = np.asarray(
        list(model.query_embed(query_texts, batch_size=64)), dtype=np.float32
    )
    corpus_dense /= np.maximum(
        np.linalg.norm(corpus_dense, axis=1, keepdims=True), 1e-12
    )
    query_dense /= np.maximum(
        np.linalg.norm(query_dense, axis=1, keepdims=True), 1e-12
    )
    rankings: list[list[tuple[int, float]]] = []
    for query_vector in query_dense:
        scores = corpus_dense @ query_vector
        take = min(top_k, len(scores))
        indices = np.argpartition(scores, -take)[-take:]
        ordered = indices[np.argsort(scores[indices])[::-1]]
        rankings.append([(int(index), float(scores[index])) for index in ordered])
    return rankings


def _rrf(
    left: list[tuple[int, float]], right: list[tuple[int, float]], top_k: int
) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in (left, right):
        for rank, (document_index, _score) in enumerate(ranking, start=1):
            scores[document_index] += 1.0 / (60 + rank)
    return heapq.nlargest(top_k, scores.items(), key=lambda item: (item[1], -item[0]))


def _rbo(left: list[str], right: list[str], persistence: float = 0.9) -> float:
    overlap = 0
    left_seen: set[str] = set()
    right_seen: set[str] = set()
    weighted = 0.0
    depth = min(len(left), len(right))
    for index in range(depth):
        left_seen.add(left[index])
        right_seen.add(right[index])
        overlap = len(left_seen & right_seen)
        weighted += (overlap / (index + 1)) * persistence**index
    return (1.0 - persistence) * weighted + (overlap / depth) * persistence**depth if depth else 0.0


def _kendall_common(left: list[str], right: list[str]) -> float | None:
    common = [item for item in left if item in set(right)]
    if len(common) < 2:
        return None
    left_rank = {item: rank for rank, item in enumerate(left)}
    right_rank = {item: rank for rank, item in enumerate(right)}
    concordant = 0
    discordant = 0
    for i, first in enumerate(common):
        for second in common[i + 1 :]:
            same = (left_rank[first] - left_rank[second]) * (
                right_rank[first] - right_rank[second]
            )
            if same > 0:
                concordant += 1
            elif same < 0:
                discordant += 1
    pairs = concordant + discordant
    return (concordant - discordant) / pairs if pairs else None


def compare_rankings(
    reproduced: dict[str, list[str]],
    stored: dict[str, list[str]],
    pool_pairs: set[tuple[str, str]],
    known_chunks: set[str],
) -> dict[str, Any]:
    query_ids = sorted(stored)
    if set(reproduced) != set(stored):
        raise ValueError("reproduced and stored query universes differ")
    exact_sequence = 0
    exact_set = 0
    overlaps: list[float] = []
    jaccards: list[float] = []
    rbos: list[float] = []
    kendalls: list[float] = []
    outside_pool: list[tuple[str, str]] = []
    missing_stored: list[tuple[str, str]] = []
    for query_id in query_ids:
        left = reproduced[query_id]
        right = stored[query_id]
        exact_sequence += int(left == right)
        exact_set += int(set(left) == set(right))
        intersection = set(left) & set(right)
        union = set(left) | set(right)
        overlaps.append(len(intersection) / max(1, len(right)))
        jaccards.append(len(intersection) / max(1, len(union)))
        rbos.append(_rbo(left, right))
        kendall = _kendall_common(left, right)
        if kendall is not None:
            kendalls.append(kendall)
        outside_pool.extend(
            (query_id, chunk_id)
            for chunk_id in left
            if (query_id, chunk_id) not in pool_pairs
        )
        missing_stored.extend(
            (query_id, chunk_id) for chunk_id in right if chunk_id not in known_chunks
        )
    count = len(query_ids)
    return {
        "query_count": count,
        "exact_top30_sequence_match_count": exact_sequence,
        "exact_top30_sequence_match_rate": exact_sequence / count,
        "exact_top30_set_match_count": exact_set,
        "exact_top30_set_match_rate": exact_set / count,
        "mean_top30_set_overlap": math.fsum(overlaps) / count,
        "mean_jaccard_at_30": math.fsum(jaccards) / count,
        "mean_reciprocal_rank_biased_overlap": math.fsum(rbos) / count,
        "mean_kendall_correlation_on_common_documents": (
            math.fsum(kendalls) / len(kendalls) if kendalls else None
        ),
        "queries_with_out_of_pool_documents": len({query_id for query_id, _ in outside_pool}),
        "out_of_pool_query_chunk_pair_count": len(outside_pool),
        "missing_stored_documents": len(missing_stored),
        "score_differences": None,
        "score_difference_reason": "supply candidate-pool rounded scores separately when available",
    }


def compare_rounded_scores(
    reproduced: dict[str, list[tuple[str, float]]],
    pools: list[dict[str, Any]],
    system: str,
) -> dict[str, Any]:
    stored = {
        row["query_id"]: {
            candidate["chunk_id"]: candidate.get("scores", {}).get(system)
            for candidate in row["candidates"]
            if system in candidate.get("scores", {})
        }
        for row in pools
    }
    differences: list[float] = []
    exact = 0
    for query_id, ranking in reproduced.items():
        for chunk_id, score in ranking:
            stored_score = stored.get(query_id, {}).get(chunk_id)
            if not isinstance(stored_score, (int, float)):
                continue
            difference = abs(round(float(score), 8) - float(stored_score))
            differences.append(difference)
            exact += int(difference == 0.0)
    return {
        "compared_common_scores": len(differences),
        "exact_after_8_decimal_rounding_count": exact,
        "exact_after_8_decimal_rounding_rate": exact / len(differences) if differences else None,
        "mean_absolute_difference": math.fsum(differences) / len(differences) if differences else None,
        "maximum_absolute_difference": max(differences) if differences else None,
    }


def _write_reproduced_run(
    path: Path,
    rankings: dict[str, list[tuple[str, float]]],
    tag: str,
    known_chunks: set[str],
) -> None:
    entries = [
        TrecRunEntry(query_id, chunk_id, rank, score, tag)
        for query_id in sorted(rankings)
        for rank, (chunk_id, score) in enumerate(rankings[query_id], start=1)
    ]
    write_trec_run(
        path,
        entries,
        known_chunk_ids=known_chunks,
        exact_results_per_query=30,
        expected_run_tag=tag,
    )


def _validate_reproduction_inputs(
    paths: ProjectPaths,
    config: dict[str, Any],
    model_provenance: dict[str, Any],
    corpus: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    stored_rows: list[dict[str, Any]],
    pools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed before spending hours on the historical neural model."""

    declared = config.get("inputs")
    if not isinstance(declared, dict):
        raise ValueError("historical retrieval config has no input binding")
    observed_bindings = {
        "corpus_sha256": sha256_file(paths.corpus),
        "queries_sha256": sha256_file(paths.queries),
        "corpus_chunk_count": len(corpus),
        "case_count": len(cases),
    }
    required_bindings = {
        "corpus_sha256": declared.get("corpus_sha256"),
        "queries_sha256": declared.get("cases_sha256"),
        "corpus_chunk_count": declared.get("corpus_chunk_count"),
        "case_count": declared.get("case_count"),
    }
    if observed_bindings != required_bindings:
        raise ValueError(
            f"historical input binding mismatch: required={required_bindings}, observed={observed_bindings}"
        )

    chunk_ids = [row.get("chunk_id") for row in corpus]
    query_ids = [row.get("query_id") for row in cases]
    if any(not isinstance(value, str) or not value for value in chunk_ids):
        raise ValueError("historical corpus contains an invalid chunk_id")
    if any(not isinstance(value, str) or not value for value in query_ids):
        raise ValueError("historical cases contain an invalid query_id")
    if len(chunk_ids) != len(set(chunk_ids)) or len(query_ids) != len(set(query_ids)):
        raise ValueError("historical corpus or cases contain duplicate identifiers")
    expected_queries = set(query_ids)
    known_chunks = set(chunk_ids)
    if len(stored_rows) != len(query_ids) or {
        row.get("query_id") for row in stored_rows
    } != expected_queries:
        raise ValueError("stored historical rankings do not cover every query exactly once")
    for row in stored_rows:
        rankings = row.get("rankings")
        if not isinstance(rankings, dict) or set(rankings) != {
            "bm25",
            "dense",
            "hybrid_rrf",
        }:
            raise ValueError("stored historical ranking row has the wrong systems")
        for system, ranking in rankings.items():
            if (
                not isinstance(ranking, list)
                or len(ranking) != 30
                or len(set(ranking)) != 30
                or any(chunk_id not in known_chunks for chunk_id in ranking)
            ):
                raise ValueError(
                    f"stored historical {system} ranking is not a valid top-30"
                )
    if len(pools) != len(query_ids) or {row.get("query_id") for row in pools} != expected_queries:
        raise ValueError("candidate pools do not cover the historical query universe")

    dense_config = config.get("dense", {})
    for key in ("resolved_repository", "resolved_revision", "snapshot_manifest_sha256"):
        if dense_config.get(key) != model_provenance.get(key):
            raise ValueError(f"dense config and embedding provenance disagree on {key}")
    files = model_provenance.get("files")
    if not isinstance(files, list) or len(files) != model_provenance.get("snapshot_file_count"):
        raise ValueError("embedding-model manifest is incomplete")
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("sha256"), str)
        or len(item["sha256"]) != 64
        or not isinstance(item.get("size_bytes"), int)
        or item["size_bytes"] <= 0
        for item in files
    ):
        raise ValueError("embedding-model file manifest is malformed")
    return {
        "status": "PASS",
        "corpus_chunk_count": len(corpus),
        "query_count": len(cases),
        "stored_ranking_rows": len(stored_rows),
        "candidate_pool_rows": len(pools),
        "systems": ["bm25", "dense", "hybrid_rrf"],
        "top_k": 30,
        "model_file_count": len(files),
    }


def _download_and_verify_model(
    output_cache: Path, model_provenance: dict[str, Any]
) -> Path:
    repository = str(model_provenance["resolved_repository"])
    revision = str(model_provenance["resolved_revision"])
    destination = output_cache / "historical_snapshot"
    destination.mkdir(parents=True, exist_ok=True)
    expected: dict[str, tuple[str, int]] = {}
    for item in model_provenance.get("files", []):
        name = Path(str(item["path"])).name
        if not name or name in expected:
            raise ValueError("historical model manifest has duplicate or empty file names")
        expected[name] = (str(item["sha256"]), int(item["size_bytes"]))
    if len(expected) != int(model_provenance.get("snapshot_file_count", -1)):
        raise ValueError("historical model manifest file count is inconsistent")

    # ``snapshot_download`` materializes Hub snapshots with symlinks.  On a
    # default Windows account that can fail with WinError 1314 even though all
    # bytes are public.  Fetch only the five provenance-pinned files into a
    # plain directory instead.  Each file is staged, hashed, and atomically
    # replaced; a partial response can never be mistaken for a valid model.
    base_url = (
        "https://huggingface.co/"
        f"{urllib.parse.quote(repository, safe='/')}/resolve/"
        f"{urllib.parse.quote(revision, safe='')}"
    )
    for name, (expected_hash, expected_size) in sorted(expected.items()):
        target = destination / name
        if (
            target.is_file()
            and target.stat().st_size == expected_size
            and sha256_file(target) == expected_hash
        ):
            continue
        staged = destination / f".{name}.incomplete"
        request = urllib.request.Request(
            f"{base_url}/{urllib.parse.quote(name, safe='')}",
            headers={"User-Agent": "SQLMend-RAG-provenance-reproduction/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response, staged.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
            if staged.stat().st_size != expected_size:
                raise ValueError(
                    f"historical model size mismatch for {name}: "
                    f"expected={expected_size}, observed={staged.stat().st_size}"
                )
            observed_hash = sha256_file(staged)
            if observed_hash != expected_hash:
                raise ValueError(
                    f"historical model hash mismatch for {name}: "
                    f"expected={expected_hash}, observed={observed_hash}"
                )
            os.replace(staged, target)
        finally:
            if staged.exists():
                staged.unlink()

    observed = {
        path.name: sha256_file(path)
        for path in destination.iterdir()
        if path.is_file() and path.name in expected
    }
    expected_hashes = {name: value[0] for name, value in expected.items()}
    if observed != expected_hashes:
        raise ValueError(
            "downloaded historical model snapshot hash mismatch: "
            f"expected={expected_hashes}, observed={observed}"
        )
    return destination


def reproduce_annotation_retrievers(paths: ProjectPaths) -> dict[str, Any]:
    """Recompute historical BM25/dense/RRF rankings and compare saved runs."""

    config = json.loads(
        (paths.annotation / "provenance" / "retrieval_config.json").read_text(encoding="utf-8")
    )
    model_provenance = json.loads(
        (paths.annotation / "provenance" / "embedding_model.json").read_text(encoding="utf-8")
    )
    corpus = _jsonl(paths.corpus)
    cases = _jsonl(paths.queries)
    stored_rows = _jsonl(paths.annotation / "provenance" / "retrieval_runs.jsonl")
    pools = _jsonl(paths.candidate_pools)
    preflight = _validate_reproduction_inputs(
        paths, config, model_provenance, corpus, cases, stored_rows, pools
    )
    stored = {
        system: {
            row["query_id"]: list(row["rankings"][system]) for row in stored_rows
        }
        for system in ("bm25", "dense", "hybrid_rrf")
    }
    known_chunks = {row["chunk_id"] for row in corpus}
    pool_pairs = {
        (row["query_id"], candidate["chunk_id"])
        for row in pools
        for candidate in row["candidates"]
    }
    corpus_texts = [_historical_corpus_text(row) for row in corpus]
    query_texts = [_historical_query_text(row) for row in cases]
    query_ids = [row["query_id"] for row in cases]
    output = paths.reproduction
    output.mkdir(parents=True, exist_ok=True)

    bm25_config = config["bm25"]
    bm25_index = HistoricalBM25(
        corpus_texts,
        k1=float(bm25_config["k1"]),
        b=float(bm25_config["b"]),
    )
    bm25_raw = [
        bm25_index.search(text, int(bm25_config["top_k"])) for text in query_texts
    ]
    bm25_rankings = {
        query_id: [(corpus[index]["chunk_id"], score) for index, score in ranking]
        for query_id, ranking in zip(query_ids, bm25_raw, strict=True)
    }
    _write_reproduced_run(
        output / "bm25_annotation_reproduced.trec",
        bm25_rankings,
        "annotation_bm25_reproduced_v1",
        known_chunks,
    )
    systems: dict[str, Any] = {}
    bm25_comparison = compare_rankings(
        {qid: [item[0] for item in ranking] for qid, ranking in bm25_rankings.items()},
        stored["bm25"],
        pool_pairs,
        known_chunks,
    )
    bm25_comparison["score_differences"] = compare_rounded_scores(
        bm25_rankings, pools, "bm25"
    )
    bm25_comparison["score_difference_reason"] = "candidate-pool scores are historical values rounded to 8 decimals"
    systems["bm25"] = {
        "status": "PASS" if bm25_comparison["exact_top30_sequence_match_rate"] == 1.0 else "PARTIAL",
        "configuration": bm25_config,
        "comparison_metrics": bm25_comparison,
        "reproduced_run_sha256": sha256_file(output / "bm25_annotation_reproduced.trec"),
    }

    dense_run_path = output / "dense_annotation_reproduced.trec"
    hybrid_run_path = output / "hybrid_annotation_reproduced.trec"
    # A failed retry must never inherit evidence from an earlier successful or
    # partial attempt.  BM25 has already been regenerated above; the neural and
    # derived hybrid artifacts are published only by this attempt.
    for stale_path in (dense_run_path, hybrid_run_path):
        stale_path.unlink(missing_ok=True)

    dense_raw: list[list[tuple[int, float]]] | None = None
    try:
        dense_config = config["dense"]
        model_path = _download_and_verify_model(output / "model_cache", model_provenance)
        dense_raw = _historical_dense(
            corpus_texts,
            query_texts,
            top_k=int(dense_config["top_k"]),
            model_name=str(dense_config["model_name"]),
            model_path=model_path,
        )
        dense_rankings = {
            query_id: [(corpus[index]["chunk_id"], score) for index, score in ranking]
            for query_id, ranking in zip(query_ids, dense_raw, strict=True)
        }
        _write_reproduced_run(
            dense_run_path,
            dense_rankings,
            "annotation_dense_reproduced_v1",
            known_chunks,
        )
        dense_comparison = compare_rankings(
            {qid: [item[0] for item in ranking] for qid, ranking in dense_rankings.items()},
            stored["dense"],
            pool_pairs,
            known_chunks,
        )
        dense_comparison["score_differences"] = compare_rounded_scores(
            dense_rankings, pools, "dense"
        )
        dense_comparison["score_difference_reason"] = "candidate-pool scores are historical values rounded to 8 decimals"
        systems["dense"] = {
            "status": "PASS" if dense_comparison["exact_top30_sequence_match_rate"] == 1.0 else "PARTIAL",
            "configuration": dense_config,
            "model_snapshot_verified": True,
            "comparison_metrics": dense_comparison,
            "reproduced_run_sha256": sha256_file(dense_run_path),
        }

        hybrid_raw = [
            _rrf(left, right, max(len(left), len(right)))
            for left, right in zip(bm25_raw, dense_raw, strict=True)
        ]
        hybrid_rankings = {
            query_id: [(corpus[index]["chunk_id"], score) for index, score in ranking]
            for query_id, ranking in zip(query_ids, hybrid_raw, strict=True)
        }
        _write_reproduced_run(
            hybrid_run_path,
            hybrid_rankings,
            "annotation_hybrid_rrf_reproduced_v1",
            known_chunks,
        )
        hybrid_comparison = compare_rankings(
            {qid: [item[0] for item in ranking] for qid, ranking in hybrid_rankings.items()},
            stored["hybrid_rrf"],
            pool_pairs,
            known_chunks,
        )
        systems["hybrid_rrf"] = {
            "status": "PASS" if hybrid_comparison["exact_top30_sequence_match_rate"] == 1.0 else "PARTIAL",
            "configuration": {"rrf_constant": 60, "top_k": 30},
            "comparison_metrics": hybrid_comparison,
            "reproduced_run_sha256": sha256_file(hybrid_run_path),
        }
    except Exception as exc:  # Preserve BM25 evidence and report the exact dense blocker.
        # Neither a dense nor a hybrid run is valid evidence when this attempt
        # reports them as non-reproducible.
        for stale_path in (dense_run_path, hybrid_run_path):
            stale_path.unlink(missing_ok=True)
        systems["dense"] = {
            "status": "NOT_REPRODUCIBLE",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        systems["hybrid_rrf"] = {
            "status": "NOT_REPRODUCIBLE",
            "reason": "historical hybrid requires the dense reproduction",
        }

    statuses = {value["status"] for value in systems.values()}
    empirical_status = "PASS" if statuses == {"PASS"} else "PARTIAL"
    return {
        "schema_version": REPRODUCTION_VERSION,
        "attempt_completed": True,
        "annotation_reproduction_status": "PARTIAL",
        "empirical_ranking_reproduction_status": empirical_status,
        "provenance_completeness_status": "PARTIAL",
        "provenance_limitations": [
            "the historical binding does not attest the exact in-memory builder source bytes",
            "historical transitive ONNX/tokenizer/runtime versions are not fully pinned",
            "historical neural tie behavior has no explicit chunk-ID tie breaker",
        ],
        "historical_query_contains_annotation_only_fields": True,
        "historical_query_is_never_used_by_formal_baselines": True,
        "preflight_validation": preflight,
        "reproduction_runtime": {
            "python_version": platform.python_version(),
            "operating_system": platform.platform(),
            "package_versions": {
                name: importlib.metadata.version(name)
                for name in ("fastembed", "onnxruntime", "numpy", "tokenizers")
            },
        },
        "inputs": {
            "implementation_sha256": sha256_file(Path(__file__)),
            "corpus_sha256": sha256_file(paths.corpus),
            "queries_sha256": sha256_file(paths.queries),
            "stored_runs_sha256": sha256_file(
                paths.annotation / "provenance" / "retrieval_runs.jsonl"
            ),
            "candidate_pools_sha256": sha256_file(paths.candidate_pools),
            "retrieval_config_sha256": sha256_file(
                paths.annotation / "provenance" / "retrieval_config.json"
            ),
            "embedding_model_sha256": sha256_file(
                paths.annotation / "provenance" / "embedding_model.json"
            ),
            "snapshot_manifest_sha256": model_provenance.get(
                "snapshot_manifest_sha256"
            ),
        },
        "systems": systems,
    }
