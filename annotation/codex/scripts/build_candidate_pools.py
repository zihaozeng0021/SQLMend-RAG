from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import Normalizer


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}|[0-9]+(?:\.[0-9]+)*")
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "by", "can", "could",
    "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "into",
    "is", "it", "my", "of", "on", "or", "our", "should", "so", "that", "the",
    "their", "then", "this", "to", "using", "was", "we", "what", "when", "where",
    "which", "why", "will", "with", "would", "query", "sql", "database", "table",
    "postgresql", "postgres", "mysql", "sqlite", "mariadb", "duckdb", "select",
    "insert", "update", "delete", "create", "alter", "drop", "join", "where",
    "group", "order", "having", "null", "true", "false", "error"
}


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def embedding_model_provenance(cache_dir: Path, requested_model: str) -> dict[str, Any]:
    repositories = sorted(path for path in cache_dir.glob("models--*") if path.is_dir())
    requested_slug = requested_model.rsplit("/", 1)[-1].lower()
    matching = [path for path in repositories if requested_slug in path.name.lower()]
    if len(matching) == 1:
        repository = matching[0]
    elif len(repositories) == 1:
        repository = repositories[0]
    else:
        raise ValueError(
            f"Cannot uniquely resolve requested model {requested_model!r} in {repositories}"
        )
    revision = (repository / "refs" / "main").read_text(encoding="utf-8").strip()
    snapshot = repository / "snapshots" / revision
    files = [
        {
            "path": str(path.relative_to(cache_dir)).replace("\\", "/"),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(snapshot.rglob("*"))
        if path.is_file()
    ]
    manifest_digest = hashlib.sha256(
        json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "requested_model": requested_model,
        "resolved_repository": repository.name.replace("models--", "").replace("--", "/"),
        "resolved_revision": revision,
        "cache_dir": "annotation/codex/work/model_cache",
        "snapshot_file_count": len(files),
        "snapshot_manifest_sha256": manifest_digest,
        "files": files,
    }


def tokens(text: str, *, content_only: bool = False) -> list[str]:
    output = [token.lower() for token in TOKEN_RE.findall(text or "")]
    if content_only:
        output = [token for token in output if token not in STOPWORDS and len(token) > 2]
    return output


def query_text(case: dict[str, Any]) -> str:
    parts = [
        str(case.get("user_problem") or ""),
        str(case.get("sql") or ""),
        str(case.get("error_message") or ""),
        str(case.get("expected_behavior") or ""),
        str(case.get("dialect") or ""),
        str(case.get("version") or ""),
    ]
    return "\n".join(part for part in parts if part)


class BM25Index:
    def __init__(self, texts: list[str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for index, text in enumerate(texts):
            counts = Counter(tokens(text))
            self.lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                self.postings[term].append((index, frequency))
        self.document_count = len(texts)
        self.average_length = sum(self.lengths) / max(1, self.document_count)

    def search(self, text: str, top_k: int) -> list[tuple[int, float]]:
        scores: dict[int, float] = defaultdict(float)
        query_counts = Counter(tokens(text))
        for term, query_frequency in query_counts.items():
            postings = self.postings.get(term)
            if not postings:
                continue
            document_frequency = len(postings)
            inverse_document_frequency = math.log(
                1.0 + (self.document_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            query_weight = 1.0 + math.log(query_frequency)
            for document_index, term_frequency in postings:
                length_norm = self.k1 * (
                    1.0 - self.b + self.b * self.lengths[document_index] / self.average_length
                )
                scores[document_index] += (
                    inverse_document_frequency
                    * query_weight
                    * (term_frequency * (self.k1 + 1.0))
                    / (term_frequency + length_norm)
                )
        return heapq.nlargest(top_k, scores.items(), key=lambda item: (item[1], -item[0]))


def lsa_dense_rankings(
    corpus_texts: list[str], query_texts: list[str], top_k: int, dimensions: int
) -> tuple[list[list[tuple[int, float]]], dict[str, Any]]:
    vectorizer = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.98,
        max_features=30000,
        sublinear_tf=True,
        dtype=np.float32,
    )
    corpus_sparse = vectorizer.fit_transform(corpus_texts)
    actual_dimensions = min(dimensions, max(2, corpus_sparse.shape[1] - 1))
    reducer = TruncatedSVD(n_components=actual_dimensions, random_state=20260828)
    corpus_dense = reducer.fit_transform(corpus_sparse).astype(np.float32, copy=False)
    query_dense = reducer.transform(vectorizer.transform(query_texts)).astype(np.float32, copy=False)
    normalizer = Normalizer(copy=False)
    corpus_dense = normalizer.transform(corpus_dense)
    query_dense = normalizer.transform(query_dense)
    rankings: list[list[tuple[int, float]]] = []
    for query_vector in query_dense:
        scores = corpus_dense @ query_vector
        take = min(top_k, len(scores))
        indices = np.argpartition(scores, -take)[-take:]
        ordered = indices[np.argsort(scores[indices])[::-1]]
        rankings.append([(int(index), float(scores[index])) for index in ordered])
    metadata = {
        "method": "tfidf_truncated_svd_lsa_cosine",
        "dimensions": actual_dimensions,
        "vocabulary_size": len(vectorizer.vocabulary_),
        "explained_variance_ratio_sum": float(reducer.explained_variance_ratio_.sum()),
        "random_seed": 20260828,
    }
    return rankings, metadata


def fastembed_dense_rankings(
    corpus_texts: list[str],
    query_texts: list[str],
    top_k: int,
    model_name: str,
    cache_dir: Path,
) -> tuple[list[list[tuple[int, float]]], dict[str, Any]]:
    """Encode corpus and queries with a pinned lightweight neural text model."""
    from fastembed import TextEmbedding

    cache_dir.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir), threads=4)
    corpus_dense = np.asarray(list(model.embed(corpus_texts, batch_size=128)), dtype=np.float32)
    query_dense = np.asarray(list(model.query_embed(query_texts, batch_size=64)), dtype=np.float32)
    corpus_dense /= np.maximum(np.linalg.norm(corpus_dense, axis=1, keepdims=True), 1e-12)
    query_dense /= np.maximum(np.linalg.norm(query_dense, axis=1, keepdims=True), 1e-12)
    rankings: list[list[tuple[int, float]]] = []
    for query_vector in query_dense:
        scores = corpus_dense @ query_vector
        take = min(top_k, len(scores))
        indices = np.argpartition(scores, -take)[-take:]
        ordered = indices[np.argsort(scores[indices])[::-1]]
        rankings.append([(int(index), float(scores[index])) for index in ordered])
    metadata = {
        "method": "fastembed_neural_text_embedding_cosine",
        "model_name": model_name,
        "dimensions": int(corpus_dense.shape[1]),
        "cache_dir": "annotation/codex/work/model_cache",
    }
    return rankings, metadata


def reciprocal_rank_fusion(
    bm25: list[tuple[int, float]], dense: list[tuple[int, float]], top_k: int, constant: int = 60
) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in (bm25, dense):
        for rank, (document_index, _) in enumerate(ranking, 1):
            scores[document_index] += 1.0 / (constant + rank)
    return heapq.nlargest(top_k, scores.items(), key=lambda item: (item[1], -item[0]))


def discounted_cumulative_gain(relevances: list[int]) -> float:
    return sum((2**relevance - 1) / math.log2(rank + 1) for rank, relevance in enumerate(relevances, 1))


def ranking_metrics(ranking: list[str], qrels: dict[str, int], cutoffs: tuple[int, ...] = (5, 10, 30)) -> dict[str, float]:
    unjudged = set(ranking) - set(qrels)
    if unjudged:
        raise ValueError(
            "Cannot score unjudged ranking entries as irrelevant: "
            f"{sorted(unjudged)[:10]}"
        )
    relevant = {chunk_id for chunk_id, relevance in qrels.items() if relevance > 0}
    output: dict[str, float] = {}
    reciprocal_rank = 0.0
    for rank, chunk_id in enumerate(ranking, 1):
        if qrels[chunk_id] > 0:
            reciprocal_rank = 1.0 / rank
            break
    output["mrr"] = reciprocal_rank
    for cutoff in cutoffs:
        selected = ranking[:cutoff]
        relevant_count = sum(qrels[chunk_id] > 0 for chunk_id in selected)
        output[f"precision@{cutoff}"] = relevant_count / cutoff
        output[f"recall@{cutoff}"] = relevant_count / max(1, len(relevant))
        observed = [qrels[chunk_id] for chunk_id in selected]
        ideal = sorted(qrels.values(), reverse=True)[:cutoff]
        ideal_score = discounted_cumulative_gain(ideal)
        output[f"ndcg@{cutoff}"] = discounted_cumulative_gain(observed) / ideal_score if ideal_score else 0.0
    return output


def longest_common_contiguous(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    best = 0
    for left_token in left:
        current = [0] * (len(right) + 1)
        for index, right_token in enumerate(right, 1):
            if left_token == right_token:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def leakage_check(case: dict[str, Any], evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    maximum_run = 0
    maximum_five_gram = 0.0
    maximum_sequence_ratio = 0.0
    full_sentence_copy = False
    worst_chunk_id = None
    exempt_tokens = set(
        tokens(
            " ".join(
                str(case.get(field) or "")
                for field in (
                    "sql",
                    "error_message",
                    "error_code",
                    "sqlstate",
                    "error_symbol",
                    "version",
                    "version_min",
                    "version_max",
                    "dialect",
                )
            )
        )
    )

    def natural_tokens(value: str) -> list[str]:
        return [token for token in tokens(value, content_only=True) if token not in exempt_tokens]

    natural_language_fields = (
        str(case.get("user_problem") or ""),
        str(case.get("expected_behavior") or ""),
    )
    for query in natural_language_fields:
        query_tokens = natural_tokens(query)
        query_ngrams = {
            tuple(query_tokens[index : index + 5])
            for index in range(max(0, len(query_tokens) - 4))
        }
        for evidence in evidence_rows:
            clean_text = CODE_BLOCK_RE.sub(" ", str(evidence.get("text") or ""))
            evidence_tokens = natural_tokens(clean_text)
            run = longest_common_contiguous(query_tokens, evidence_tokens)
            evidence_ngrams = {
                tuple(evidence_tokens[index : index + 5])
                for index in range(max(0, len(evidence_tokens) - 4))
            }
            containment = len(query_ngrams & evidence_ngrams) / max(1, len(query_ngrams))
            ratio = SequenceMatcher(None, query_tokens, evidence_tokens, autojunk=False).ratio()
            copied_sentence = any(
                len(natural_tokens(sentence)) >= 8
                and " ".join(natural_tokens(sentence)) in " ".join(evidence_tokens)
                for sentence in SENTENCE_RE.split(query)
            )
            severity = (run, containment, ratio)
            current = (maximum_run, maximum_five_gram, maximum_sequence_ratio)
            if severity > current:
                worst_chunk_id = evidence.get("chunk_id")
            maximum_run = max(maximum_run, run)
            maximum_five_gram = max(maximum_five_gram, containment)
            maximum_sequence_ratio = max(maximum_sequence_ratio, ratio)
            full_sentence_copy = full_sentence_copy or copied_sentence
    hard_fail = maximum_run > 12 or full_sentence_copy
    warning = hard_fail or maximum_five_gram > 0.45 or maximum_sequence_ratio > 0.55
    return {
        "query_id": case["query_id"],
        "max_contiguous_natural_language_tokens": maximum_run,
        "max_query_5gram_containment": round(maximum_five_gram, 6),
        "max_sequence_ratio": round(maximum_sequence_ratio, 6),
        "full_explanatory_sentence_copied": full_sentence_copy,
        "worst_chunk_id": worst_chunk_id,
        "status": "FAIL" if hard_fail else ("WARN" if warning else "PASS"),
        "rules": {
            "maximum_contiguous_tokens": 12,
            "warning_5gram_containment": 0.45,
            "warning_sequence_ratio": 0.55,
            "checked_fields": ["user_problem", "expected_behavior"],
            "exemptions": (
                "Fenced evidence code plus tokens present in the case SQL, exact error/code/state/"
                "symbol, version fields, dialect, and the common SQL/product stopword list."
            ),
        },
    }


def machine_relevance(
    case: dict[str, Any], chunk: dict[str, Any], retrieval_sources: set[str], explicit: dict[str, int]
) -> int:
    chunk_id = chunk["chunk_id"]
    if chunk_id in explicit:
        return int(explicit[chunk_id])
    case_terms = set(
        tokens(
            " ".join(
                [
                    str(case.get("user_problem") or ""),
                    str(case.get("root_cause") or ""),
                    str(case.get("expected_behavior") or ""),
                    str(case.get("reference_fix_sql") or ""),
                ]
            ),
            content_only=True,
        )
    )
    chunk_terms = set(tokens(str(chunk.get("title") or "") + " " + str(chunk.get("section") or "") + " " + str(chunk.get("text") or ""), content_only=True))
    overlap = len(case_terms & chunk_terms)
    overlap_ratio = overlap / max(1, min(len(case_terms), 30))
    same_dialect = chunk.get("dialect") == case.get("dialect")
    if same_dialect and overlap >= 3 and overlap_ratio >= 0.12:
        return 1
    if same_dialect and {"bm25", "dense"}.issubset(retrieval_sources) and overlap >= 2:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--bm25-top-k", type=int, default=30)
    parser.add_argument("--dense-top-k", type=int, default=30)
    parser.add_argument(
        "--dense-backend",
        choices=("fastembed", "lsa"),
        default="fastembed",
        help="Use fastembed for final artifacts; lsa is a diagnostic fallback rejected by the final validator.",
    )
    parser.add_argument("--fastembed-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--dense-dimensions", type=int, default=96)
    parser.add_argument(
        "--leakage-only",
        action="store_true",
        help=(
            "Refresh leakage, deterministic pooled judgments, and metric reports from saved "
            "rankings without re-embedding. Refuses changed retrieval query text."
        ),
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    annotation = root / "annotation" / "codex"
    corpus_path = root / "construction" / "data" / "processed" / "corpus.jsonl"
    cases_path = annotation / "dev_250.jsonl"
    corpus = list(iter_jsonl(corpus_path))
    cases = list(iter_jsonl(cases_path))
    chunk_by_id = {row["chunk_id"]: row for row in corpus}
    frozen_query_hash_rows = [
        {
            "query_id": case["query_id"],
            "query_text_hash": hashlib.sha256(query_text(case).encode("utf-8")).hexdigest(),
        }
        for case in sorted(cases, key=lambda row: row["query_id"])
    ]
    frozen_inputs = {
        "corpus_path": "construction/data/processed/corpus.jsonl",
        "corpus_sha256": sha256_file(corpus_path),
        "corpus_chunk_count": len(corpus),
        "cases_path": "annotation/codex/dev_250.jsonl",
        "cases_sha256": sha256_file(cases_path),
        "case_count": len(cases),
        "query_hash_set_sha256": hashlib.sha256(
            json.dumps(
                frozen_query_hash_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    if args.leakage_only:
        pools_path = annotation / "candidate_pools.jsonl"
        qrels_path = annotation / "qrels_machine_proposed.jsonl"
        leakage_path = annotation / "query_source_leakage.jsonl"
        runs_path = annotation / "provenance" / "retrieval_runs.jsonl"
        config_path = annotation / "provenance" / "retrieval_config.json"
        model_provenance_path = annotation / "provenance" / "embedding_model.json"
        leakage_report_path = annotation / "reports" / "leakage_report.json"
        metrics_path = annotation / "reports" / "retrieval_metrics.json"

        # A leakage-only refresh is not a recovery path for an incomplete
        # retrieval run. Finish every saved-input check before changing any
        # derived object or invoking an output helper.
        required_inputs = (pools_path, runs_path, config_path, model_provenance_path)
        missing_inputs = [str(path) for path in required_inputs if not path.is_file()]
        if missing_inputs:
            raise FileNotFoundError(
                f"Leakage-only refresh requires completed retrieval artifacts: {missing_inputs}"
            )
        saved_pools = list(iter_jsonl(pools_path))
        saved_runs = list(iter_jsonl(runs_path))
        retrieval_config = json.loads(config_path.read_text(encoding="utf-8"))
        model_provenance = json.loads(model_provenance_path.read_text(encoding="utf-8"))

        expected_query_ids = [f"DEV{index:04d}" for index in range(1, 251)]

        def exact_ordered_query_ids(
            label: str, rows: list[dict[str, Any]]
        ) -> list[str]:
            query_ids = [row.get("query_id") for row in rows]
            if len(query_ids) != 250:
                raise ValueError(f"{label}: expected 250 rows, observed {len(query_ids)}")
            if any(not isinstance(query_id, str) for query_id in query_ids):
                raise ValueError(f"{label}: every query_id must be a string")
            if len(set(query_ids)) != len(query_ids):
                raise ValueError(f"{label}: duplicate query_id values are not allowed")
            if query_ids != expected_query_ids:
                raise ValueError(
                    f"{label}: query IDs must be exactly DEV0001..DEV0250 in order"
                )
            return query_ids

        case_ids = exact_ordered_query_ids("cases", cases)
        pool_ids = exact_ordered_query_ids("candidate pools", saved_pools)
        run_ids = exact_ordered_query_ids("retrieval runs", saved_runs)
        if pool_ids != case_ids or run_ids != case_ids:
            raise ValueError(
                "Cases, candidate pools, and retrieval runs have different ordered query IDs"
            )
        case_by_id = {case["query_id"]: case for case in cases}
        pool_by_id = {pool["query_id"]: pool for pool in saved_pools}

        query_hash_rows = frozen_query_hash_rows
        for row in query_hash_rows:
            if pool_by_id[row["query_id"]].get("query_text_hash") != row["query_text_hash"]:
                raise ValueError(
                    f"{row['query_id']}: query text changed after retrieval; rankings must be rebuilt"
                )
        current_inputs = frozen_inputs
        if retrieval_config.get("inputs") != current_inputs:
            raise ValueError(
                "Retrieval config inputs differ from the current corpus, cases, or query-hash set"
            )

        dense_config = retrieval_config.get("dense")
        bm25_config = retrieval_config.get("bm25")
        if not isinstance(dense_config, dict) or not isinstance(bm25_config, dict):
            raise ValueError("Retrieval config must contain dense and bm25 objects")
        requested_model = dense_config.get("model_name")
        if not isinstance(requested_model, str) or not requested_model:
            raise ValueError("Retrieval config dense.model_name is required")
        if dense_config.get("method") != "fastembed_neural_text_embedding_cosine":
            raise ValueError("Leakage-only final refresh requires saved FastEmbed rankings")
        recomputed_model = embedding_model_provenance(
            annotation / "work" / "model_cache", requested_model
        )
        if model_provenance != recomputed_model:
            raise ValueError("Saved embedding model snapshot no longer matches the local cache")
        model_field_pairs = {
            "model_name": "requested_model",
            "cache_dir": "cache_dir",
            "resolved_repository": "resolved_repository",
            "resolved_revision": "resolved_revision",
            "snapshot_manifest_sha256": "snapshot_manifest_sha256",
        }
        for config_field, model_field in model_field_pairs.items():
            if dense_config.get(config_field) != model_provenance.get(model_field):
                raise ValueError(
                    f"Retrieval config is not bound to embedding model field {config_field}"
                )

        bm25_top_k = bm25_config.get("top_k")
        dense_top_k = dense_config.get("top_k")
        if (
            not isinstance(bm25_top_k, int)
            or isinstance(bm25_top_k, bool)
            or bm25_top_k <= 0
            or not isinstance(dense_top_k, int)
            or isinstance(dense_top_k, bool)
            or dense_top_k <= 0
        ):
            raise ValueError("Retrieval top_k values must be positive integers")
        expected_ranking_lengths = {
            "bm25": bm25_top_k,
            "dense": dense_top_k,
            "hybrid_rrf": max(bm25_top_k, dense_top_k),
        }
        for pool, run in zip(saved_pools, saved_runs, strict=True):
            query_id = pool["query_id"]
            candidates = pool.get("candidates")
            if not isinstance(candidates, list):
                raise ValueError(f"{query_id}: candidates must be a list")
            candidate_ids = [candidate.get("chunk_id") for candidate in candidates]
            if any(not isinstance(chunk_id, str) for chunk_id in candidate_ids):
                raise ValueError(f"{query_id}: every candidate chunk_id must be a string")
            if len(set(candidate_ids)) != len(candidate_ids):
                raise ValueError(f"{query_id}: duplicate pool candidates are not allowed")
            missing_chunks = sorted(set(candidate_ids) - set(chunk_by_id))
            if missing_chunks:
                raise ValueError(
                    f"{query_id}: saved pool references missing chunks {missing_chunks[:10]}"
                )
            case = case_by_id[query_id]
            evidence_ids = [evidence.get("chunk_id") for evidence in case.get("evidence", [])]
            if any(not isinstance(chunk_id, str) for chunk_id in evidence_ids):
                raise ValueError(f"{query_id}: every evidence chunk_id must be a string")
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ValueError(f"{query_id}: duplicate evidence chunk IDs are not allowed")
            if not set(evidence_ids).issubset(candidate_ids):
                raise ValueError(f"{query_id}: saved pool is missing source-linked evidence")
            for candidate in candidates:
                sources = candidate.get("retrieved_by")
                if not isinstance(sources, list) or any(
                    not isinstance(source, str) for source in sources
                ):
                    raise ValueError(f"{query_id}: candidate retrieved_by must be a string list")
                if candidate["chunk_id"] in evidence_ids and "source_link" not in sources:
                    raise ValueError(
                        f"{query_id}: evidence {candidate['chunk_id']} lacks source_link provenance"
                    )
                ranks = candidate.get("ranks")
                if not isinstance(ranks, dict) or any(
                    not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0
                    for rank in ranks.values()
                ):
                    raise ValueError(f"{query_id}: candidate ranks must be positive integers")

            rankings = run.get("rankings")
            if not isinstance(rankings, dict) or set(rankings) != set(
                expected_ranking_lengths
            ):
                raise ValueError(
                    f"{query_id}: run must contain exactly bm25, dense, and hybrid_rrf rankings"
                )
            candidate_id_set = set(candidate_ids)
            for system_name, expected_length in expected_ranking_lengths.items():
                ranking = rankings[system_name]
                if not isinstance(ranking, list) or len(ranking) != expected_length:
                    observed = len(ranking) if isinstance(ranking, list) else "non-list"
                    raise ValueError(
                        f"{query_id}/{system_name}: expected {expected_length} ranked chunks, "
                        f"observed {observed}"
                    )
                if any(not isinstance(chunk_id, str) for chunk_id in ranking):
                    raise ValueError(
                        f"{query_id}/{system_name}: ranking entries must be chunk IDs"
                    )
                if len(set(ranking)) != len(ranking):
                    raise ValueError(
                        f"{query_id}/{system_name}: duplicate ranking entries are not allowed"
                    )
                outside_pool = sorted(set(ranking) - candidate_id_set)
                if outside_pool:
                    raise ValueError(
                        f"{query_id}/{system_name}: ranking entries missing from pool "
                        f"{outside_pool[:10]}"
                    )

        # All preflight checks have passed. Refresh the derived objects in
        # memory; no output helper is called until every calculation succeeds.
        refreshed_pools: list[dict[str, Any]] = []
        refreshed_qrels: list[dict[str, Any]] = []
        for pool in saved_pools:
            query_id = pool["query_id"]
            case = case_by_id[query_id]
            explicit = {
                evidence["chunk_id"]: int(evidence["relevance"])
                for evidence in case["evidence"]
            }
            refreshed_candidates: list[dict[str, Any]] = []
            for candidate in pool["candidates"]:
                candidate = dict(candidate)
                chunk_id = candidate["chunk_id"]
                sources = set(candidate.get("retrieved_by", []))
                candidate["relevance"] = machine_relevance(
                    case, chunk_by_id[chunk_id], sources, explicit
                )
                candidate["judgment_origin"] = "codex_machine_proposed"
                candidate["judgment_method"] = (
                    "explicit_case_evidence"
                    if chunk_id in explicit
                    else "deterministic_contextual_heuristic"
                )
                refreshed_candidates.append(candidate)
            refreshed_pool = dict(pool)
            refreshed_pool["candidates"] = sorted(
                refreshed_candidates,
                key=lambda row: (
                    min(row["ranks"].values()) if row["ranks"] else 9999,
                    -int(row["relevance"]),
                    row["chunk_id"],
                ),
            )
            refreshed_pools.append(refreshed_pool)
            refreshed_qrels.extend(
                {
                    "query_id": query_id,
                    "chunk_id": candidate["chunk_id"],
                    "relevance": int(candidate["relevance"]),
                    "judgment_origin": "codex_machine_proposed",
                    "judgment_method": candidate["judgment_method"],
                }
                for candidate in sorted(
                    refreshed_pool["candidates"], key=lambda row: row["chunk_id"]
                )
            )

        leakage_rows = [
            leakage_check(
                case,
                [chunk_by_id[evidence["chunk_id"]] for evidence in case["evidence"]],
            )
            for case in cases
        ]
        leakage_report = {
            "total_cases": len(leakage_rows),
            "pass": sum(row["status"] == "PASS" for row in leakage_rows),
            "warn": sum(row["status"] == "WARN" for row in leakage_rows),
            "fail": sum(row["status"] == "FAIL" for row in leakage_rows),
            "failed_query_ids": [
                row["query_id"] for row in leakage_rows if row["status"] == "FAIL"
            ],
            "warning_query_ids": [
                row["query_id"] for row in leakage_rows if row["status"] == "WARN"
            ],
        }
        qrels_by_query: dict[str, dict[str, int]] = defaultdict(dict)
        for row in refreshed_qrels:
            qrels_by_query[row["query_id"]][row["chunk_id"]] = int(row["relevance"])
        metric_rows: list[dict[str, Any]] = []
        for run in saved_runs:
            for system_name, ranking in run["rankings"].items():
                metric_rows.append(
                    {
                        "query_id": run["query_id"],
                        "system": system_name,
                        **ranking_metrics(ranking, qrels_by_query[run["query_id"]]),
                    }
                )
        refreshed_config = json.loads(json.dumps(retrieval_config))
        refreshed_config["judgment_policy"] = {
            "explicit_evidence": "case evidence relevance is preserved",
            "non_evidence": "deterministic same-dialect context-overlap/retrieval-agreement heuristic",
            "metric_caveat": "circular exploratory development diagnostic; not independent evaluation",
        }
        metric_summary: dict[str, Any] = {
            "scope": "pooled machine-proposed development evaluation",
            "warning": (
                "Exploratory only: non-evidence labels are deterministic machine heuristics that "
                "partly use lexical overlap/retrieval agreement, so these metrics are circular and "
                "are not a substitute for independent human qrels or held-out test metrics."
            ),
            "systems": {},
            "dense_metadata": refreshed_config["dense"],
        }
        for system_name in sorted({row["system"] for row in metric_rows}):
            system_rows = [row for row in metric_rows if row["system"] == system_name]
            metric_names = [name for name in system_rows[0] if name not in {"query_id", "system"}]
            metric_summary["systems"][system_name] = {
                name: round(sum(float(row[name]) for row in system_rows) / len(system_rows), 6)
                for name in metric_names
            }

        # Force serialization while the filesystem is still untouched. The
        # following block performs only the coordinated output writes.
        for rows in (refreshed_pools, refreshed_qrels, leakage_rows):
            for row in rows:
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for document in (leakage_report, refreshed_config, metric_summary):
            json.dumps(document, ensure_ascii=False, indent=2)

        write_jsonl(pools_path, refreshed_pools)
        write_jsonl(qrels_path, refreshed_qrels)
        write_jsonl(leakage_path, leakage_rows)
        write_json(leakage_report_path, leakage_report)
        write_json(config_path, refreshed_config)
        write_json(metrics_path, metric_summary)
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "leakage_failures": sum(row["status"] == "FAIL" for row in leakage_rows),
                    "metric_runs": len(saved_runs),
                },
                indent=2,
            )
        )
        return 0
    corpus_texts = [
        "\n".join(
            [
                str(row.get("title") or ""),
                str(row.get("section") or ""),
                str(row.get("text") or ""),
                str(row.get("dialect") or ""),
                str(row.get("version") or ""),
            ]
        )
        for row in corpus
    ]
    case_query_texts = [query_text(case) for case in cases]

    bm25_index = BM25Index(corpus_texts)
    bm25_rankings = [bm25_index.search(text, args.bm25_top_k) for text in case_query_texts]
    if args.dense_backend == "fastembed":
        dense_results, dense_metadata = fastembed_dense_rankings(
            corpus_texts,
            case_query_texts,
            args.dense_top_k,
            args.fastembed_model,
            annotation / "work" / "model_cache",
        )
        model_provenance = embedding_model_provenance(
            annotation / "work" / "model_cache", args.fastembed_model
        )
        write_json(annotation / "provenance" / "embedding_model.json", model_provenance)
        dense_metadata.update(
            {
                "resolved_repository": model_provenance["resolved_repository"],
                "resolved_revision": model_provenance["resolved_revision"],
                "snapshot_manifest_sha256": model_provenance["snapshot_manifest_sha256"],
            }
        )
    else:
        dense_results, dense_metadata = lsa_dense_rankings(
            corpus_texts, case_query_texts, args.dense_top_k, args.dense_dimensions
        )

    pools: list[dict[str, Any]] = []
    qrel_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    leakage_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []

    for case_index, case in enumerate(cases):
        pool: dict[str, dict[str, Any]] = {}
        for method, ranking in (
            ("bm25", bm25_rankings[case_index]),
            ("dense", dense_results[case_index]),
        ):
            for rank, (document_index, score) in enumerate(ranking, 1):
                chunk_id = corpus[document_index]["chunk_id"]
                item = pool.setdefault(
                    chunk_id,
                    {"chunk_id": chunk_id, "retrieved_by": [], "ranks": {}, "scores": {}},
                )
                item["retrieved_by"].append(method)
                item["ranks"][method] = rank
                item["scores"][method] = round(score, 8)

        explicit = {evidence["chunk_id"]: int(evidence["relevance"]) for evidence in case["evidence"]}
        for chunk_id in explicit:
            if chunk_id not in chunk_by_id:
                raise ValueError(f"{case['query_id']}: missing evidence chunk {chunk_id}")
            item = pool.setdefault(
                chunk_id,
                {"chunk_id": chunk_id, "retrieved_by": [], "ranks": {}, "scores": {}},
            )
            if "source_link" not in item["retrieved_by"]:
                item["retrieved_by"].append("source_link")

        for item in pool.values():
            item["retrieved_by"] = sorted(set(item["retrieved_by"]))
            relevance = machine_relevance(
                case, chunk_by_id[item["chunk_id"]], set(item["retrieved_by"]), explicit
            )
            item["relevance"] = relevance
            item["judgment_origin"] = "codex_machine_proposed"
            item["judgment_method"] = (
                "explicit_case_evidence" if item["chunk_id"] in explicit else "deterministic_contextual_heuristic"
            )

        ordered_candidates = sorted(
            pool.values(),
            key=lambda row: (
                min(row["ranks"].values()) if row["ranks"] else 9999,
                -row["relevance"],
                row["chunk_id"],
            ),
        )
        pools.append(
            {
                "query_id": case["query_id"],
                "query_text_hash": hashlib.sha256(case_query_texts[case_index].encode("utf-8")).hexdigest(),
                "pool_config": {
                    "bm25_top_k": args.bm25_top_k,
                    "dense_top_k": args.dense_top_k,
                    "dense_method": dense_metadata["method"],
                },
                "candidates": ordered_candidates,
            }
        )
        case_qrels = {item["chunk_id"]: int(item["relevance"]) for item in ordered_candidates}
        qrel_rows.extend(
            {
                "query_id": case["query_id"],
                "chunk_id": chunk_id,
                "relevance": relevance,
                "judgment_origin": "codex_machine_proposed",
                "judgment_method": next(
                    item["judgment_method"] for item in ordered_candidates if item["chunk_id"] == chunk_id
                ),
            }
            for chunk_id, relevance in sorted(case_qrels.items())
        )

        hybrid = reciprocal_rank_fusion(
            bm25_rankings[case_index], dense_results[case_index], max(args.bm25_top_k, args.dense_top_k)
        )
        ranking_ids = {
            "bm25": [corpus[index]["chunk_id"] for index, _ in bm25_rankings[case_index]],
            "dense": [corpus[index]["chunk_id"] for index, _ in dense_results[case_index]],
            "hybrid_rrf": [corpus[index]["chunk_id"] for index, _ in hybrid],
        }
        run_rows.append(
            {
                "query_id": case["query_id"],
                "rankings": ranking_ids,
                "rrf_constant": 60,
            }
        )
        for system_name, ranking in ranking_ids.items():
            metric_rows.append(
                {
                    "query_id": case["query_id"],
                    "system": system_name,
                    **ranking_metrics(ranking, case_qrels),
                }
            )
        evidence_rows = [chunk_by_id[evidence["chunk_id"]] for evidence in case["evidence"]]
        leakage_rows.append(leakage_check(case, evidence_rows))

    write_jsonl(annotation / "candidate_pools.jsonl", pools)
    write_jsonl(annotation / "qrels_machine_proposed.jsonl", qrel_rows)
    write_jsonl(annotation / "query_source_leakage.jsonl", leakage_rows)
    write_jsonl(annotation / "provenance" / "retrieval_runs.jsonl", run_rows)

    metric_summary: dict[str, Any] = {
        "scope": "pooled machine-proposed development evaluation",
        "warning": (
            "Exploratory only: non-evidence labels are deterministic machine heuristics that "
            "partly use lexical overlap/retrieval agreement, so these metrics are circular and "
            "are not a substitute for independent human qrels or held-out test metrics."
        ),
        "systems": {},
    }
    for system_name in sorted({row["system"] for row in metric_rows}):
        system_rows = [row for row in metric_rows if row["system"] == system_name]
        metric_names = [name for name in system_rows[0] if name not in {"query_id", "system"}]
        metric_summary["systems"][system_name] = {
            name: round(sum(float(row[name]) for row in system_rows) / len(system_rows), 6)
            for name in metric_names
        }
    metric_summary["dense_metadata"] = dense_metadata | {"top_k": args.dense_top_k}
    write_json(annotation / "reports" / "retrieval_metrics.json", metric_summary)
    write_json(
        annotation / "reports" / "leakage_report.json",
        {
            "total_cases": len(leakage_rows),
            "pass": sum(row["status"] == "PASS" for row in leakage_rows),
            "warn": sum(row["status"] == "WARN" for row in leakage_rows),
            "fail": sum(row["status"] == "FAIL" for row in leakage_rows),
            "failed_query_ids": [row["query_id"] for row in leakage_rows if row["status"] == "FAIL"],
            "warning_query_ids": [row["query_id"] for row in leakage_rows if row["status"] == "WARN"],
        },
    )
    write_json(
        annotation / "provenance" / "retrieval_config.json",
        {
            "bm25": {"k1": 1.2, "b": 0.75, "top_k": args.bm25_top_k},
            "dense": dense_metadata | {"top_k": args.dense_top_k},
            "pooling": "union of BM25, dense embedding, and source-linked case evidence",
            "qrels": "all pooled candidates judged 0/1/2; absent pairs are unjudged",
            "inputs": frozen_inputs,
            "judgment_policy": {
                "explicit_evidence": "case evidence relevance is preserved",
                "non_evidence": "deterministic same-dialect context-overlap/retrieval-agreement heuristic",
                "metric_caveat": "circular exploratory development diagnostic; not independent evaluation",
            },
        },
    )
    print(
        json.dumps(
            {
                "cases": len(cases),
                "corpus_chunks": len(corpus),
                "candidate_records": len(pools),
                "qrels": len(qrel_rows),
                "leakage_failures": sum(row["status"] == "FAIL" for row in leakage_rows),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
