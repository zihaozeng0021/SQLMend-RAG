"""Fixed two-channel Reciprocal Rank Fusion for the formal hybrid baseline.

This module deliberately has no relevance, qrels, evidence, or source-link API.
Only the independently produced BM25 and dense ranked results can contribute.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any


RRF_K = 60
FUSION_DEPTH = 30
OUTPUT_DEPTH = 30


class RRFError(ValueError):
    """Raised when a component ranking is invalid."""


@dataclass(frozen=True, slots=True)
class RRFResult:
    """A fused result with auditable component ranks."""

    chunk_id: str
    rank: int
    rrf_score: float
    bm25_rank: int | None
    dense_rank: int | None

    @property
    def score(self) -> float:
        """TREC-compatible score alias."""

        return self.rrf_score

    def to_dict(self) -> dict[str, str | int | float | None]:
        return {
            "chunk_id": self.chunk_id,
            "rank": self.rank,
            "rrf_score": self.rrf_score,
            "bm25_rank": self.bm25_rank,
            "dense_rank": self.dense_rank,
        }


def _chunk_token(value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RRFError(f"chunk_id must be a non-empty whitespace-free token: {value!r}")
    if any(character.isspace() for character in value):
        raise RRFError(f"chunk_id must not contain whitespace: {value!r}")
    return value


def _positive_rank(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RRFError(f"component rank must be a positive integer: {value!r}")
    return value


def _item_fields(item: object, position: int) -> tuple[str, int]:
    if isinstance(item, str):
        return _chunk_token(item), position
    if isinstance(item, Mapping):
        if "chunk_id" not in item:
            raise RRFError("ranked result mapping is missing 'chunk_id'")
        chunk_id = _chunk_token(item["chunk_id"])
        rank = _positive_rank(item.get("rank", position))
        return chunk_id, rank
    try:
        chunk_id = _chunk_token(getattr(item, "chunk_id"))
    except AttributeError as exc:
        raise RRFError(f"unsupported ranked result: {item!r}") from exc
    rank = _positive_rank(getattr(item, "rank", position))
    return chunk_id, rank


def _normalize_ranking(ranking: Sequence[object] | Mapping[str, int]) -> dict[str, int]:
    """Normalize one complete ranked list to ``chunk_id -> rank``."""

    if isinstance(ranking, (str, bytes)):
        raise RRFError("a component ranking must be a ranked list or chunk-to-rank mapping")

    pairs: list[tuple[str, int]] = []
    if isinstance(ranking, Mapping):
        for chunk_id, rank in ranking.items():
            pairs.append((_chunk_token(chunk_id), _positive_rank(rank)))
    elif isinstance(ranking, Sequence):
        for position, item in enumerate(ranking, start=1):
            chunk_id, rank = _item_fields(item, position)
            if rank != position:
                raise RRFError(
                    "explicit component ranks must match ranked-list positions; "
                    f"position {position} declared rank {rank}"
                )
            pairs.append((chunk_id, rank))
    else:
        raise RRFError("a component ranking must be a ranked list or chunk-to-rank mapping")

    chunk_ids = [chunk_id for chunk_id, _ in pairs]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise RRFError("component ranking contains a duplicate chunk_id")
    ranks = [rank for _, rank in pairs]
    if len(set(ranks)) != len(ranks):
        raise RRFError("component ranking contains a duplicate rank")
    if sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise RRFError(f"component ranks must be continuous from 1; observed {sorted(ranks)!r}")
    return dict(pairs)


def fuse_ranked_lists(
    bm25_results: Sequence[object] | Mapping[str, int],
    dense_results: Sequence[object] | Mapping[str, int],
) -> list[RRFResult]:
    """Fuse exactly one BM25 ranking and one dense ranking.

    The formal constants are fixed at ``k=60``, component depth 30, and output
    depth 30. Missing component ranks remain ``None``.
    """

    bm25_ranks = _normalize_ranking(bm25_results)
    dense_ranks = _normalize_ranking(dense_results)
    candidates = {
        chunk_id
        for chunk_id, rank in bm25_ranks.items()
        if rank <= FUSION_DEPTH
    }
    candidates.update(
        chunk_id for chunk_id, rank in dense_ranks.items() if rank <= FUSION_DEPTH
    )

    scored: list[tuple[str, float, int, int | None, int | None]] = []
    for chunk_id in candidates:
        bm25_rank = bm25_ranks.get(chunk_id)
        dense_rank = dense_ranks.get(chunk_id)
        component_ranks = [
            rank
            for rank in (bm25_rank, dense_rank)
            if rank is not None and rank <= FUSION_DEPTH
        ]
        score = math.fsum(1.0 / (RRF_K + rank) for rank in component_ranks)
        best_rank = min(component_ranks)
        scored.append(
            (
                chunk_id,
                score,
                best_rank,
                bm25_rank if bm25_rank is not None and bm25_rank <= FUSION_DEPTH else None,
                dense_rank if dense_rank is not None and dense_rank <= FUSION_DEPTH else None,
            )
        )

    scored.sort(key=lambda value: (-value[1], value[2], value[0]))
    return [
        RRFResult(
            chunk_id=chunk_id,
            rank=rank,
            rrf_score=score,
            bm25_rank=bm25_rank,
            dense_rank=dense_rank,
        )
        for rank, (chunk_id, score, _best_rank, bm25_rank, dense_rank) in enumerate(
            scored[:OUTPUT_DEPTH], start=1
        )
    ]


def fuse_ranked_mappings(
    bm25_results: Mapping[str, Sequence[object] | Mapping[str, int]],
    dense_results: Mapping[str, Sequence[object] | Mapping[str, int]],
) -> dict[str, list[RRFResult]]:
    """Fuse BM25 and dense rankings for every query in deterministic order."""

    if not isinstance(bm25_results, Mapping) or not isinstance(dense_results, Mapping):
        raise RRFError("multi-query inputs must be query-to-ranking mappings")
    query_ids = set(bm25_results).union(dense_results)
    output: dict[str, list[RRFResult]] = {}
    for query_id in sorted(query_ids):
        if not isinstance(query_id, str) or not query_id:
            raise RRFError(f"query_id must be a non-empty string: {query_id!r}")
        output[query_id] = fuse_ranked_lists(
            bm25_results.get(query_id, ()), dense_results.get(query_id, ())
        )
    return output


# Public names emphasize that only two ranked channels are accepted.
reciprocal_rank_fusion = fuse_ranked_lists
fuse_rrf = fuse_ranked_lists
fuse_runs = fuse_ranked_mappings
