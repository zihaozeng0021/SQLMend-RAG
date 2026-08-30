"""Frozen RRF candidate reconstruction and metadata-aware online ranking."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from .compatibility import dialect_compatibility, version_compatibility
from .models import CandidatePassage, OnlineQuery, RunEntry


RRF_K = 60
COMPONENT_DEPTH = 30
OUTPUT_DEPTH = 30


@dataclass(frozen=True, slots=True)
class CandidateState:
    passage: CandidatePassage
    bm25_rank: int | None
    dense_rank: int | None


def render_passage(record: Mapping[str, Any]) -> str:
    """Match the frozen ``sqlmend-passage-v1`` renderer exactly."""

    parts: list[str] = []
    for field, label in (("title", "Title"), ("section", "Section")):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(f"{label}: {value.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))}")
    text = record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"Chunk {record.get('chunk_id')!r} has empty text")
    parts.append(f"Text:\n{text.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))}")
    return "\n".join(parts)


def _group_component(entries: Iterable[RunEntry]) -> dict[str, list[RunEntry]]:
    grouped: dict[str, list[RunEntry]] = defaultdict(list)
    for entry in entries:
        if entry.rank <= COMPONENT_DEPTH:
            grouped[entry.query_id].append(entry)
    result = {query_id: sorted(rows, key=lambda row: row.rank) for query_id, rows in sorted(grouped.items())}
    for query_id, rows in result.items():
        if len(rows) != COMPONENT_DEPTH or [row.rank for row in rows] != list(range(1, COMPONENT_DEPTH + 1)):
            raise ValueError(f"Component run for {query_id} is not a complete top {COMPONENT_DEPTH}")
    return result


def reconstruct_rrf_candidates(
    bm25_entries: Iterable[RunEntry],
    dense_entries: Iterable[RunEntry],
    corpus_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[CandidateState]]:
    """Rebuild the exact two-channel RRF union before the frozen top-30 cutoff."""

    bm25 = _group_component(bm25_entries)
    dense = _group_component(dense_entries)
    if set(bm25) != set(dense):
        raise ValueError("BM25 and dense query coverage differ")
    output: dict[str, list[CandidateState]] = {}
    for query_id in sorted(bm25):
        bm25_ranks = {row.chunk_id: row.rank for row in bm25[query_id]}
        dense_ranks = {row.chunk_id: row.rank for row in dense[query_id]}
        scored: list[tuple[str, float, int, int | None, int | None]] = []
        for chunk_id in set(bm25_ranks).union(dense_ranks):
            if chunk_id not in corpus_by_id:
                raise ValueError(f"Unknown corpus chunk in component run: {chunk_id}")
            bm25_rank = bm25_ranks.get(chunk_id)
            dense_rank = dense_ranks.get(chunk_id)
            ranks = [rank for rank in (bm25_rank, dense_rank) if rank is not None]
            score = math.fsum(1.0 / (RRF_K + rank) for rank in ranks)
            scored.append((chunk_id, score, min(ranks), bm25_rank, dense_rank))
        scored.sort(key=lambda item: (-item[1], item[2], item[0]))
        states: list[CandidateState] = []
        for union_rank, (chunk_id, score, _best_rank, bm25_rank, dense_rank) in enumerate(scored, start=1):
            record = corpus_by_id[chunk_id]
            states.append(
                CandidateState(
                    passage=CandidatePassage(
                        chunk_id=chunk_id,
                        dialect=record.get("dialect") if isinstance(record.get("dialect"), str) else None,
                        version=record.get("version") if isinstance(record.get("version"), str) else None,
                        version_min=record.get("version_min") if isinstance(record.get("version_min"), str) else None,
                        version_max=record.get("version_max") if isinstance(record.get("version_max"), str) else None,
                        version_status=str(record.get("version_status") or "unknown"),
                        source_type=record.get("source_type") if isinstance(record.get("source_type"), str) else None,
                        title=record.get("title") if isinstance(record.get("title"), str) else None,
                        section=record.get("section") if isinstance(record.get("section"), str) else None,
                        text=render_passage(record),
                        baseline_rank=union_rank,
                        baseline_score=score,
                    ),
                    bm25_rank=bm25_rank,
                    dense_rank=dense_rank,
                )
            )
        output[query_id] = states
    return output


def verify_frozen_hybrid_reconstruction(
    candidates: Mapping[str, list[CandidateState]],
    frozen_hybrid_entries: Iterable[RunEntry],
) -> None:
    frozen: dict[str, list[RunEntry]] = defaultdict(list)
    for entry in frozen_hybrid_entries:
        frozen[entry.query_id].append(entry)
    if set(frozen) != set(candidates):
        raise ValueError("Reconstructed candidates and frozen Hybrid cover different queries")
    for query_id in sorted(candidates):
        expected = [row.chunk_id for row in sorted(frozen[query_id], key=lambda row: row.rank)]
        observed = [state.passage.chunk_id for state in candidates[query_id][:OUTPUT_DEPTH]]
        if observed != expected:
            raise ValueError(f"Frozen Hybrid reconstruction differs for {query_id}")


def rank_metadata_aware(
    candidates: Mapping[str, list[CandidateState]],
    online_queries: Mapping[str, OnlineQuery],
    config: Mapping[str, Any],
) -> tuple[list[RunEntry], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Apply configured soft compatibility bonuses without labels or hard filtering."""

    dialect_bonuses = {str(key): float(value) for key, value in dict(config.get("dialect_bonuses", {})).items()}
    version_bonuses = {str(key): float(value) for key, value in dict(config.get("version_bonuses", {})).items()}
    run_tag = str(config["run_tag"])
    output_depth = int(config.get("output_depth", OUTPUT_DEPTH))
    run: list[RunEntry] = []
    top_provenance: list[dict[str, Any]] = []
    all_scored: dict[str, list[dict[str, Any]]] = {}

    for query_id in sorted(candidates):
        query = online_queries[query_id]
        scored: list[dict[str, Any]] = []
        for state in candidates[query_id]:
            dialect_category = dialect_compatibility(query.dialect, state.passage.dialect)
            version_decision = version_compatibility(query, state.passage)
            dialect_bonus = dialect_bonuses.get(dialect_category, 0.0)
            version_bonus = version_bonuses.get(version_decision.category, 0.0)
            adjusted = math.fsum((state.passage.baseline_score, dialect_bonus, version_bonus))
            scored.append(
                {
                    "query_id": query_id,
                    "chunk_id": state.passage.chunk_id,
                    "baseline_rrf_rank": state.passage.baseline_rank,
                    "baseline_rrf_score": state.passage.baseline_score,
                    "bm25_rank": state.bm25_rank,
                    "dense_rank": state.dense_rank,
                    "dialect_category": dialect_category,
                    "dialect_bonus": dialect_bonus,
                    "version_category": version_decision.category,
                    "version_reason": version_decision.reason,
                    "version_bonus": version_bonus,
                    "adjusted_score": adjusted,
                }
            )
        scored.sort(key=lambda item: (-item["adjusted_score"], item["baseline_rrf_rank"], item["chunk_id"]))
        for rank, record in enumerate(scored, start=1):
            record["adjusted_rank"] = rank
            if rank <= output_depth:
                run.append(RunEntry(query_id, record["chunk_id"], rank, record["adjusted_score"], run_tag))
                top_provenance.append(dict(record))
        all_scored[query_id] = scored
    return run, top_provenance, all_scored


def candidate_pair_set(candidates: Mapping[str, Iterable[CandidateState]]) -> set[tuple[str, str]]:
    return {
        (query_id, state.passage.chunk_id)
        for query_id, states in candidates.items()
        for state in states
    }
