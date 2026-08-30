from __future__ import annotations

import inspect
import math

import pytest

from sqlmend_retrieval.rrf import (
    FUSION_DEPTH,
    OUTPUT_DEPTH,
    RRF_K,
    RRFError,
    fuse_ranked_lists,
    fuse_ranked_mappings,
)


def test_rrf_matches_manual_fixture_and_deterministic_tie_breaking():
    # a and b receive the same two reciprocal terms and the same best component
    # rank, so the final tie is broken by ascending chunk ID.
    results = fuse_ranked_lists(["b", "a", "x"], ["a", "b", "y"])
    assert [result.chunk_id for result in results] == ["a", "b", "x", "y"]
    assert results[0].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert results[0].bm25_rank == 2
    assert results[0].dense_rank == 1
    assert results[0].rank == 1
    assert results[1].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert results[1].bm25_rank == 1
    assert results[1].dense_rank == 2
    assert all(math.isfinite(result.rrf_score) for result in results)


def test_rrf_equal_scores_use_best_component_rank_before_chunk_id():
    # 1/63 + 1/84 == 1/72 + 1/72.  Although "a" sorts first by ID,
    # "z" has best component rank 3 versus 12 and must therefore rank first.
    bm25 = [f"bm{rank}" for rank in range(1, 25)]
    dense = [f"dn{rank}" for rank in range(1, 25)]
    bm25[2] = "z"
    bm25[11] = "a"
    dense[11] = "a"
    dense[23] = "z"
    results = fuse_ranked_lists(bm25, dense)
    positions = {result.chunk_id: result.rank for result in results}
    assert positions["z"] < positions["a"]


def test_rrf_missing_component_ranks_are_null():
    results = fuse_ranked_lists(["bm25_only"], ["dense_only"])
    by_chunk = {result.chunk_id: result for result in results}
    assert by_chunk["bm25_only"].bm25_rank == 1
    assert by_chunk["bm25_only"].dense_rank is None
    assert by_chunk["dense_only"].bm25_rank is None
    assert by_chunk["dense_only"].dense_rank == 1
    assert by_chunk["bm25_only"].to_dict()["dense_rank"] is None


def test_rrf_uses_fixed_depths_and_k():
    assert (RRF_K, FUSION_DEPTH, OUTPUT_DEPTH) == (60, 30, 30)
    bm25 = [f"b{rank:02d}" for rank in range(1, 32)]
    dense = [f"d{rank:02d}" for rank in range(1, 32)]
    results = fuse_ranked_lists(bm25, dense)
    assert len(results) == 30
    assert all(result.chunk_id not in {"b31", "d31"} for result in results)


def test_rrf_accepts_chunk_to_rank_mappings_and_query_mappings():
    single = fuse_ranked_lists({"b": 2, "a": 1}, {"a": 1, "c": 2})
    assert single[0].chunk_id == "a"
    fused = fuse_ranked_mappings(
        {"Q2": ["z"], "Q1": ["a"]},
        {"Q1": ["b"], "Q3": ["c"]},
    )
    assert list(fused) == ["Q1", "Q2", "Q3"]
    assert fused["Q2"][0].dense_rank is None
    assert fused["Q3"][0].bm25_rank is None


def test_rrf_rejects_duplicate_or_noncontinuous_component_rankings():
    with pytest.raises(RRFError, match="duplicate chunk_id"):
        fuse_ranked_lists(["a", "a"], [])
    with pytest.raises(RRFError, match="continuous"):
        fuse_ranked_lists({"a": 1, "b": 3}, {})
    with pytest.raises(RRFError, match="positions"):
        fuse_ranked_lists([{"chunk_id": "a", "rank": 2}], [])


def test_rrf_api_has_exactly_two_ranking_channels():
    parameters = inspect.signature(fuse_ranked_lists).parameters
    assert list(parameters) == ["bm25_results", "dense_results"]
    assert "relevance" not in parameters
    assert "source_link" not in parameters
    with pytest.raises(TypeError):
        fuse_ranked_lists(["a"], ["b"], ["forbidden_third_channel"])
    with pytest.raises(TypeError):
        fuse_ranked_lists(["a"], ["b"], relevance={"a": 2})
    with pytest.raises(TypeError):
        fuse_ranked_lists(["a"], ["b"], source_link=["a"])
