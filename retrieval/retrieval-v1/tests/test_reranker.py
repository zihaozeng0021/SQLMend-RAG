from __future__ import annotations

import math
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "src")))

from sqlmend_retrieval_v1.models import CandidatePassage, OnlineQuery
from sqlmend_retrieval_v1.ranking import CandidateState
from sqlmend_retrieval_v1.reranker import (
    DEFAULT_GAMMA,
    TOKENIZER_VERSION,
    build_corpus_lexical_index,
    rank_field_aware,
    tokenize_field,
)


def _state(chunk_id: str, text: str, rank: int, score: float = 0.02) -> CandidateState:
    return CandidateState(
        passage=CandidatePassage(
            chunk_id=chunk_id,
            dialect="postgresql",
            version="14",
            version_min="14.0",
            version_max="14.x",
            version_status="range",
            source_type="official_docs",
            title=chunk_id,
            section=chunk_id,
            text=text,
            baseline_rank=rank,
            baseline_score=score,
        ),
        bm25_rank=rank,
        dense_rank=None,
    )


def _metadata(states: list[CandidateState], score: float = 0.02):
    return [
        {
            "chunk_id": state.passage.chunk_id,
            "adjusted_rank": index,
            "adjusted_score": score,
        }
        for index, state in enumerate(states, start=1)
    ]


def test_tokenizer_and_index_are_deterministic_and_corpus_wide():
    assert tokenize_field("Foo::BAR >= 14.2 and SQLSTATE 42P01") == [
        "foo",
        "::",
        "bar",
        ">=",
        "14.2",
        "and",
        "sqlstate",
        "42p01",
    ]
    first = build_corpus_lexical_index(
        {"b": "common beta", "a": "common common alpha", "c": "common gamma"}
    )
    second = build_corpus_lexical_index(
        {"c": "common gamma", "a": "common common alpha", "b": "common beta"}
    )
    assert first.tokenizer_version == TOKENIZER_VERSION
    assert first.document_count == 3
    assert first.inverse_document_frequencies == second.inverse_document_frequencies
    assert first.term_frequencies == second.term_frequencies
    assert first.inverse_document_frequencies["alpha"] > first.inverse_document_frequencies["common"]
    assert math.isclose(
        first.inverse_document_frequencies["common"],
        math.log(1.0 + (3 - 3 + 0.5) / (3 + 0.5)),
    )


def test_exact_error_and_field_bm25_promote_direct_evidence():
    states = [
        _state("generic", "The table lookup fails for an unspecified reason.", 1),
        _state(
            "direct",
            "PostgreSQL reports SQLSTATE 42P01 (undefined_table) when a relation is missing.",
            2,
        ),
    ]
    query = OnlineQuery(
        query_id="Q1",
        dialect="postgresql",
        version="14",
        serialized_text=(
            "Dialect: postgresql\n\nVersion: 14\n\n"
            "Question:\nWhy is relation orders missing?\n\n"
            "Observed error or behavior:\nError code: 42P01\nSQLSTATE: 42P01\n"
            "Error symbol: undefined_table\n\nSQL:\nSELECT * FROM orders;"
        ),
        user_problem="Why is relation orders missing?",
        sql="SELECT * FROM orders;",
        error_code="42P01",
        sqlstate="42P01",
        error_symbol="undefined_table",
    )
    index = build_corpus_lexical_index(
        {state.passage.chunk_id: state.passage for state in states}
    )
    run, provenance, all_scored = rank_field_aware(
        {"Q1": states},
        {"Q1": query},
        {"Q1": _metadata(states)},
        index,
        output_depth=2,
    )
    assert [row.chunk_id for row in run] == ["direct", "generic"]
    direct = next(row for row in provenance if row["chunk_id"] == "direct")
    assert direct["exact_error_matches"] == 3
    assert direct["field_lexical_score"] > 0
    assert math.isclose(
        direct["rerank_score"],
        direct["metadata_adjusted_score"] + DEFAULT_GAMMA * direct["field_lexical_score"],
    )
    assert all_scored["Q1"][0]["rerank_rank"] == 1


def test_serializer_fallback_uses_only_safe_sections():
    states = [
        _state("unrelated", "A transaction can be committed.", 1),
        _state("matching", "Use coalesce to replace a NULL value.", 2),
    ]
    query = OnlineQuery(
        query_id="Q2",
        dialect="postgresql",
        version="14",
        serialized_text=(
            "Dialect: postgresql\n\nVersion: 14\n\n"
            "Question:\nHow should a NULL value be replaced?\n\n"
            "SQL:\nSELECT coalesce(value, 0) FROM t;"
        ),
    )
    index = build_corpus_lexical_index(
        {state.passage.chunk_id: state.passage for state in states}
    )
    run, _, _ = rank_field_aware(
        {"Q2": states},
        {"Q2": query},
        {"Q2": _metadata(states)},
        index,
        output_depth=2,
    )
    assert [row.chunk_id for row in run] == ["matching", "unrelated"]


def test_ties_use_metadata_rank_then_baseline_rank_then_chunk_id():
    states = [
        _state("z", "identical passage", 2),
        _state("a", "identical passage", 1),
    ]
    query = OnlineQuery("Q3", "postgresql", "14", "Dialect: postgresql")
    index = build_corpus_lexical_index(
        {state.passage.chunk_id: state.passage for state in states}
    )
    metadata = [
        {"chunk_id": "z", "adjusted_rank": 1, "adjusted_score": 0.02},
        {"chunk_id": "a", "adjusted_rank": 2, "adjusted_score": 0.02},
    ]
    first, _, _ = rank_field_aware(
        {"Q3": states}, {"Q3": query}, {"Q3": metadata}, index, output_depth=2
    )
    second, _, _ = rank_field_aware(
        {"Q3": list(reversed(states))},
        {"Q3": query},
        {"Q3": list(reversed(metadata))},
        index,
        output_depth=2,
    )
    assert first == second
    assert [row.chunk_id for row in first] == ["z", "a"]


def test_rejects_missing_index_entries_and_nonfinite_gamma():
    state = _state("candidate", "some text", 1)
    query = OnlineQuery("Q4", "postgresql", "14", "Dialect: postgresql")
    index = build_corpus_lexical_index({"other": "other text"})
    with pytest.raises(KeyError, match="absent from lexical index"):
        rank_field_aware(
            {"Q4": [state]},
            {"Q4": query},
            {"Q4": _metadata([state])},
            index,
            output_depth=1,
        )
    with pytest.raises(ValueError, match="gamma"):
        rank_field_aware(
            {"Q4": [state]},
            {"Q4": query},
            {"Q4": _metadata([state])},
            index,
            gamma=float("nan"),
            output_depth=1,
        )
