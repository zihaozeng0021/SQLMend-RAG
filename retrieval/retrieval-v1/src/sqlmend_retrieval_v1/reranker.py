"""Deterministic field-aware lexical reranking for retrieval v1.

The reranker is deliberately label-free.  Its online inputs are the safe
``OnlineQuery`` projection, corpus-owned candidate passages, and the score
produced by the preceding dialect+version-aware stage.  Corpus-wide document
frequencies are built once and contain no query judgments.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
from typing import Any

from .models import CandidatePassage, OnlineQuery, RunEntry
from .ranking import CandidateState, render_passage


TOKENIZER_VERSION = "sqlmend-field-lexical-v1"
DEFAULT_GAMMA = 0.001
DEFAULT_OUTPUT_DEPTH = 30
DEFAULT_RUN_TAG = "hybrid_rrf_dialect_version_field_lexical_rerank_v1"
BM25_K1 = 1.5
BM25_B = 0.75
PROBLEM_WEIGHT = 0.8
SQL_WEIGHT = 0.8
ERROR_WEIGHT = 1.5
EXACT_ERROR_WEIGHT = 2.0

_TOKEN_RE = re.compile(
    r"->>|->|::|<=|>=|<>|!=|:=|=>|\|\||&&|"
    r"\d+(?:\.\d+)+|"
    r"\d+[A-Za-z][A-Za-z0-9_]*|"
    r"[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)+|"
    r"[A-Za-z_][A-Za-z0-9_$]*|"
    r"\d+(?:\.\d+)?",
    re.IGNORECASE,
)

# Frozen rather than language-library dependent so tokenization is byte-for-byte
# reproducible across clean environments.
_STOPWORDS = frozenset(
    "a an the is are was were be been being of to in for on with as at by from "
    "or and but if then than this that these those it its how what why which who "
    "where when should would could can may might must do does did not no without "
    "into about after before around between under over question sql dialect version "
    "observed error behavior message code state".split()
)
_SQL_KEYWORDS = frozenset(
    "select from where insert into values update set delete create table alter drop "
    "with as join inner outer left right full on group by order having limit offset "
    "distinct union all null true false and or not case when then else end cast".split()
)

_QUESTION_PREFIX = "Question:\n"
_ERROR_PREFIX = "Observed error or behavior:\n"
_SQL_PREFIX = "SQL:\n"


def tokenize_field(value: str | None) -> list[str]:
    """Return the frozen lowercase identifier/operator/number token stream."""

    if value is None:
        return []
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(normalized)]


def _without(tokens: Sequence[str], excluded: frozenset[str]) -> list[str]:
    return [token for token in tokens if token not in excluded]


def _serialized_section(serialized: str, prefix: str) -> str | None:
    """Extract one exact ``sqlmend-query-v1`` section as a safe fallback."""

    marker = f"\n\n{prefix}"
    start = serialized.find(prefix)
    if start < 0:
        start = serialized.find(marker)
        if start < 0:
            return None
        start += 2
    start += len(prefix)
    end = serialized.find("\n\n", start)
    value = serialized[start:] if end < 0 else serialized[start:end]
    return value if value.strip() else None


def _query_fields(query: OnlineQuery) -> tuple[str | None, str | None, str | None]:
    problem = query.user_problem or _serialized_section(query.serialized_text, _QUESTION_PREFIX)
    sql = query.sql or _serialized_section(query.serialized_text, _SQL_PREFIX)
    explicit_errors = tuple(
        value
        for value in (
            query.error_message,
            query.error_code,
            query.sqlstate,
            query.error_symbol,
        )
        if value
    )
    error = "\n".join(explicit_errors) if explicit_errors else _serialized_section(
        query.serialized_text, _ERROR_PREFIX
    )
    return problem, sql, error


def _passage_text(value: str | Mapping[str, Any] | CandidatePassage) -> str:
    if isinstance(value, CandidatePassage):
        text = value.text
    elif isinstance(value, str):
        text = value
    elif isinstance(value, Mapping):
        text = render_passage(value)
    else:
        raise TypeError("corpus values must be passage strings, mappings, or CandidatePassage")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("corpus passages must contain non-empty text")
    return text


@dataclass(frozen=True, slots=True)
class CorpusLexicalIndex:
    """Immutable corpus statistics used by the online BM25 feature scorer."""

    term_frequencies: Mapping[str, Mapping[str, int]]
    document_lengths: Mapping[str, int]
    inverse_document_frequencies: Mapping[str, float]
    average_document_length: float
    document_count: int
    tokenizer_version: str = TOKENIZER_VERSION
    k1: float = BM25_K1
    b: float = BM25_B

    def score(self, query_tokens: Sequence[str], chunk_id: str) -> float:
        """Score one passage with the frozen BM25 formula."""

        if chunk_id not in self.term_frequencies:
            raise KeyError(f"chunk is absent from lexical index: {chunk_id!r}")
        frequencies = self.term_frequencies[chunk_id]
        length = self.document_lengths[chunk_id]
        normalization = self.k1 * (
            1.0 - self.b + self.b * length / self.average_document_length
        )
        contributions = []
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if frequency:
                contributions.append(
                    self.inverse_document_frequencies.get(token, 0.0)
                    * frequency
                    * (self.k1 + 1.0)
                    / (frequency + normalization)
                )
        return math.fsum(contributions)


def build_corpus_lexical_index(
    corpus: Mapping[str, str | Mapping[str, Any] | CandidatePassage],
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> CorpusLexicalIndex:
    """Build deterministic corpus-wide IDF and term-frequency statistics."""

    if not isinstance(corpus, Mapping) or not corpus:
        raise ValueError("corpus must be a non-empty chunk-id mapping")
    if not math.isfinite(float(k1)) or k1 <= 0:
        raise ValueError("k1 must be finite and positive")
    if not math.isfinite(float(b)) or not 0.0 <= b <= 1.0:
        raise ValueError("b must be finite and between zero and one")

    frequencies: dict[str, dict[str, int]] = {}
    lengths: dict[str, int] = {}
    document_frequencies: Counter[str] = Counter()
    for chunk_id in sorted(corpus):
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("corpus chunk IDs must be non-empty strings")
        tokens = tokenize_field(_passage_text(corpus[chunk_id]))
        token_counts = Counter(tokens)
        frequencies[chunk_id] = dict(sorted(token_counts.items()))
        lengths[chunk_id] = len(tokens)
        # One count per document, irrespective of within-document term frequency.
        document_frequencies.update(token_counts.keys())

    count = len(frequencies)
    average_length = math.fsum(lengths.values()) / count
    if average_length <= 0:
        raise ValueError("corpus token stream is empty")
    idf = {
        token: math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
        for token, frequency in sorted(document_frequencies.items())
    }
    return CorpusLexicalIndex(
        term_frequencies=frequencies,
        document_lengths=lengths,
        inverse_document_frequencies=idf,
        average_document_length=average_length,
        document_count=count,
        k1=float(k1),
        b=float(b),
    )


def _minmax(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    lower = min(values)
    upper = max(values)
    if upper == lower:
        return [0.0] * len(values)
    scale = upper - lower
    return [(value - lower) / scale for value in values]


def _metadata_by_chunk(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        chunk_id = record.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("metadata scorer records require a non-empty chunk_id")
        if chunk_id in result:
            raise ValueError(f"duplicate metadata scorer chunk: {chunk_id!r}")
        score = record.get("adjusted_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            raise ValueError(f"metadata adjusted_score is not finite for {chunk_id!r}")
        result[chunk_id] = record
    return result


def rank_field_aware(
    candidates: Mapping[str, Sequence[CandidateState]],
    online_queries: Mapping[str, OnlineQuery],
    metadata_scored: Mapping[str, Sequence[Mapping[str, Any]]],
    lexical_index: CorpusLexicalIndex,
    *,
    gamma: float = DEFAULT_GAMMA,
    run_tag: str = DEFAULT_RUN_TAG,
    output_depth: int = DEFAULT_OUTPUT_DEPTH,
) -> tuple[list[RunEntry], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Rerank dialect+version candidates using only safe lexical features.

    The exact online formula is::

        L = 0.8*problem_norm + 0.8*sql_norm + 1.5*error_norm + 2*exact
        final_score = metadata_adjusted_score + gamma*L

    The three BM25 features are independently min-max normalized within the
    current query's full candidate set. ``exact`` counts case-insensitive exact
    substring matches for the supplied error code, SQLSTATE, and error symbol.
    """

    if not isinstance(lexical_index, CorpusLexicalIndex):
        raise TypeError("lexical_index must be a CorpusLexicalIndex")
    if not math.isfinite(float(gamma)) or gamma < 0:
        raise ValueError("gamma must be finite and non-negative")
    if not isinstance(run_tag, str) or not run_tag or any(char.isspace() for char in run_tag):
        raise ValueError("run_tag must be a non-empty whitespace-free string")
    if isinstance(output_depth, bool) or not isinstance(output_depth, int) or output_depth <= 0:
        raise ValueError("output_depth must be a positive integer")
    if set(candidates) != set(online_queries) or set(candidates) != set(metadata_scored):
        raise ValueError("candidate, query, and metadata-score query coverage must match")

    run: list[RunEntry] = []
    top_provenance: list[dict[str, Any]] = []
    all_scored: dict[str, list[dict[str, Any]]] = {}

    for query_id in sorted(candidates):
        query = online_queries[query_id]
        if query.query_id != query_id:
            raise ValueError(f"online query key disagrees with query_id: {query_id!r}")
        states = list(candidates[query_id])
        if len(states) < output_depth:
            raise ValueError(
                f"query {query_id!r} has {len(states)} candidates; {output_depth} required"
            )
        if any(not isinstance(state, CandidateState) for state in states):
            raise TypeError("candidate sets must contain CandidateState instances")
        chunk_ids = [state.passage.chunk_id for state in states]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError(f"query {query_id!r} contains duplicate candidates")

        metadata = _metadata_by_chunk(metadata_scored[query_id])
        if set(metadata) != set(chunk_ids):
            raise ValueError(f"metadata scores do not match candidates for {query_id!r}")

        problem, sql, error = _query_fields(query)
        problem_tokens = _without(tokenize_field(problem), _STOPWORDS)
        sql_tokens = _without(tokenize_field(sql), _STOPWORDS | _SQL_KEYWORDS)
        error_tokens = _without(tokenize_field(error), _STOPWORDS)
        exact_values = tuple(
            str(value).strip().casefold()
            for value in (query.error_code, query.sqlstate, query.error_symbol)
            if value is not None and str(value).strip()
        )

        problem_scores = [
            lexical_index.score(problem_tokens, state.passage.chunk_id) for state in states
        ]
        sql_scores = [
            lexical_index.score(sql_tokens, state.passage.chunk_id) for state in states
        ]
        error_scores = [
            lexical_index.score(error_tokens, state.passage.chunk_id) for state in states
        ]
        problem_norm = _minmax(problem_scores)
        sql_norm = _minmax(sql_scores)
        error_norm = _minmax(error_scores)

        scored: list[dict[str, Any]] = []
        for index, state in enumerate(states):
            chunk_id = state.passage.chunk_id
            passage_folded = state.passage.text.casefold()
            exact_matches = sum(value in passage_folded for value in exact_values)
            lexical_score = math.fsum(
                (
                    PROBLEM_WEIGHT * problem_norm[index],
                    SQL_WEIGHT * sql_norm[index],
                    ERROR_WEIGHT * error_norm[index],
                    EXACT_ERROR_WEIGHT * exact_matches,
                )
            )
            metadata_score = float(metadata[chunk_id]["adjusted_score"])
            final_score = math.fsum((metadata_score, float(gamma) * lexical_score))
            metadata_rank = metadata[chunk_id].get("adjusted_rank")
            if isinstance(metadata_rank, bool) or not isinstance(metadata_rank, int) or metadata_rank <= 0:
                metadata_rank = state.passage.baseline_rank
            scored.append(
                {
                    "query_id": query_id,
                    "chunk_id": chunk_id,
                    "baseline_rrf_rank": state.passage.baseline_rank,
                    "metadata_adjusted_rank": metadata_rank,
                    "metadata_adjusted_score": metadata_score,
                    "problem_bm25": problem_scores[index],
                    "problem_norm": problem_norm[index],
                    "sql_bm25": sql_scores[index],
                    "sql_norm": sql_norm[index],
                    "error_bm25": error_scores[index],
                    "error_norm": error_norm[index],
                    "exact_error_matches": exact_matches,
                    "field_lexical_score": lexical_score,
                    "gamma": float(gamma),
                    "rerank_score": final_score,
                }
            )

        scored.sort(
            key=lambda item: (
                -item["rerank_score"],
                item["metadata_adjusted_rank"],
                item["baseline_rrf_rank"],
                item["chunk_id"],
            )
        )
        for rank, record in enumerate(scored, start=1):
            record["rerank_rank"] = rank
            if rank <= output_depth:
                run.append(
                    RunEntry(
                        query_id=query_id,
                        chunk_id=record["chunk_id"],
                        rank=rank,
                        score=record["rerank_score"],
                        run_tag=run_tag,
                    )
                )
                top_provenance.append(dict(record))
        all_scored[query_id] = scored

    return run, top_provenance, all_scored


__all__ = [
    "BM25_B",
    "BM25_K1",
    "CorpusLexicalIndex",
    "DEFAULT_GAMMA",
    "DEFAULT_OUTPUT_DEPTH",
    "DEFAULT_RUN_TAG",
    "ERROR_WEIGHT",
    "EXACT_ERROR_WEIGHT",
    "PROBLEM_WEIGHT",
    "SQL_WEIGHT",
    "TOKENIZER_VERSION",
    "build_corpus_lexical_index",
    "rank_field_aware",
    "tokenize_field",
]
