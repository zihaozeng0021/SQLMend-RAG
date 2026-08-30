"""Deterministic five-system judgment-pool audit for retrieval v1.

An absent qrel is unjudged, never relevance zero.  Every unique unjudged pair
in a formal top 30 is represented once in the returned expansion data while
retaining the rank and score from every system that retrieved it.  This module
performs no file I/O so callers can validate the data before publishing any
artifact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
import math
from typing import Any

from .models import CandidatePassage, RunEntry


ALLOWED_RELEVANCE_GRADES = frozenset({0, 1, 2})
EVALUATION_LABEL = "machine-proposed development evaluation"
FORMAL_POOL_DEPTH = 30
FORMAL_SYSTEM_IDS = (
    "hybrid_rrf_frozen_control_v1",
    "hybrid_rrf_dialect_aware_v1",
    "hybrid_rrf_version_aware_v1",
    "hybrid_rrf_dialect_version_aware_v1",
    "hybrid_rrf_dialect_version_lexical_rerank_v1",
)
JUDGED_CUTOFFS = (5, 10, 20, 30)
POOL_AUDIT_SCHEMA_VERSION = "sqlmend-retrieval-v1-pool-audit-v1"
EXPANSION_REASON = "unjudged_in_formal_top30"
EXPANSION_JUDGMENT_STATUS = "human_or_separate_machine_judgment_required"
SNAPSHOT_FIELDS = (
    "dialect",
    "version",
    "version_min",
    "version_max",
    "version_status",
    "source_type",
    "title",
    "section",
    "text",
)


class PoolAuditError(ValueError):
    """Raised when inputs cannot support a valid formal pool audit."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise PoolAuditError(f"{field} must be a non-empty, trimmed string")
    return value


def _system_ids(system_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(system_ids, (str, bytes)):
        raise TypeError("system_ids must be a sequence of system identifiers")
    result = tuple(system_ids)
    if not result:
        raise PoolAuditError("system_ids cannot be empty")
    if len(result) != len(set(result)):
        raise PoolAuditError("system_ids cannot contain duplicates")
    for system_id in result:
        _identifier(system_id, "system_id")
        if any(character.isspace() for character in system_id):
            raise PoolAuditError("system IDs cannot contain whitespace")
    return result


def _depth(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PoolAuditError("depth must be a positive integer")
    if value < max(JUDGED_CUTOFFS):
        raise PoolAuditError(f"depth must be at least {max(JUDGED_CUTOFFS)}")
    return value


def _candidate_index(
    corpus: Mapping[str, CandidatePassage] | Iterable[CandidatePassage],
) -> dict[str, CandidatePassage]:
    if isinstance(corpus, Mapping):
        items = corpus.items()
    else:
        if isinstance(corpus, (str, bytes)):
            raise TypeError("corpus must contain CandidatePassage instances")
        items = ((candidate.chunk_id, candidate) for candidate in corpus)

    result: dict[str, CandidatePassage] = {}
    for key, candidate in items:
        if not isinstance(candidate, CandidatePassage):
            raise TypeError("corpus must contain CandidatePassage instances")
        chunk_id = _identifier(candidate.chunk_id, "corpus chunk_id")
        if key != chunk_id:
            raise PoolAuditError(
                f"corpus mapping key {key!r} disagrees with chunk_id {chunk_id!r}"
            )
        if chunk_id in result:
            raise PoolAuditError(f"duplicate corpus chunk_id: {chunk_id!r}")
        if not isinstance(candidate.text, str) or not candidate.text.strip():
            raise PoolAuditError(f"corpus chunk {chunk_id!r} has empty text")
        result[chunk_id] = candidate
    if not result:
        raise PoolAuditError("corpus cannot be empty")
    return result


def _normalise_qrels(
    qrels: Mapping[str, Mapping[str, int]],
    *,
    known_chunks: set[str],
) -> set[tuple[str, str]]:
    if not isinstance(qrels, Mapping):
        raise TypeError("qrels must map query IDs to document relevance mappings")
    judged_pairs: set[tuple[str, str]] = set()
    for query_id, judgments in qrels.items():
        _identifier(query_id, "qrel query_id")
        if not isinstance(judgments, Mapping):
            raise TypeError(f"qrels for {query_id!r} must be a mapping")
        for chunk_id, relevance in judgments.items():
            _identifier(chunk_id, "qrel chunk_id")
            if chunk_id not in known_chunks:
                raise PoolAuditError(f"qrels contain unknown chunk {chunk_id!r}")
            if (
                isinstance(relevance, bool)
                or not isinstance(relevance, int)
                or relevance not in ALLOWED_RELEVANCE_GRADES
            ):
                raise PoolAuditError(
                    f"qrel relevance must be one of {sorted(ALLOWED_RELEVANCE_GRADES)}"
                )
            judged_pairs.add((query_id, chunk_id))
    return judged_pairs


def _validate_run_entry(entry: Any) -> RunEntry:
    if not isinstance(entry, RunEntry):
        raise TypeError("formal runs must contain RunEntry instances")
    _identifier(entry.query_id, "run query_id")
    _identifier(entry.chunk_id, "run chunk_id")
    _identifier(entry.run_tag, "run_tag")
    if isinstance(entry.rank, bool) or not isinstance(entry.rank, int) or entry.rank <= 0:
        raise PoolAuditError("run ranks must be positive integers")
    if (
        isinstance(entry.score, bool)
        or not isinstance(entry.score, (int, float))
        or not math.isfinite(float(entry.score))
    ):
        raise PoolAuditError("run scores must be finite numbers")
    return entry


def _normalise_runs(
    runs: Mapping[str, Iterable[RunEntry]],
    *,
    system_ids: tuple[str, ...],
    depth: int,
    known_chunks: set[str],
) -> dict[str, dict[str, tuple[RunEntry, ...]]]:
    if not isinstance(runs, Mapping):
        raise TypeError("runs must map system IDs to RunEntry iterables")
    if set(runs) != set(system_ids):
        missing = sorted(set(system_ids) - set(runs))
        extra = sorted(set(runs) - set(system_ids))
        raise PoolAuditError(
            f"formal runs must match system_ids; missing={missing!r}, extra={extra!r}"
        )

    result: dict[str, dict[str, tuple[RunEntry, ...]]] = {}
    query_universe: set[str] | None = None
    for system_id in system_ids:
        rows = runs[system_id]
        if isinstance(rows, (str, bytes)):
            raise TypeError("each formal run must be an iterable of RunEntry rows")
        grouped: dict[str, list[RunEntry]] = defaultdict(list)
        tags: set[str] = set()
        for entry in rows:
            row = _validate_run_entry(entry)
            if row.chunk_id not in known_chunks:
                raise PoolAuditError(
                    f"system {system_id!r} contains unknown chunk {row.chunk_id!r}"
                )
            grouped[row.query_id].append(row)
            tags.add(row.run_tag)
        if not grouped:
            raise PoolAuditError(f"system {system_id!r} has an empty run")
        if len(tags) != 1:
            raise PoolAuditError(f"system {system_id!r} contains multiple run tags")

        ordered: dict[str, tuple[RunEntry, ...]] = {}
        for query_id in sorted(grouped):
            ranking = tuple(sorted(grouped[query_id], key=lambda row: row.rank))
            if len(ranking) != depth:
                raise PoolAuditError(
                    f"system {system_id!r}, query {query_id!r} has "
                    f"{len(ranking)} rows; expected {depth}"
                )
            if [row.rank for row in ranking] != list(range(1, depth + 1)):
                raise PoolAuditError(
                    f"system {system_id!r}, query {query_id!r} ranks are not continuous"
                )
            chunk_ids = [row.chunk_id for row in ranking]
            if len(chunk_ids) != len(set(chunk_ids)):
                raise PoolAuditError(
                    f"system {system_id!r}, query {query_id!r} has duplicate chunks"
                )
            ordered[query_id] = ranking

        observed_queries = set(ordered)
        if query_universe is None:
            query_universe = observed_queries
        elif observed_queries != query_universe:
            missing = sorted(query_universe - observed_queries)
            extra = sorted(observed_queries - query_universe)
            raise PoolAuditError(
                f"system {system_id!r} has different query coverage; "
                f"missing={missing!r}, extra={extra!r}"
            )
        result[system_id] = ordered
    return result


def _snapshot(candidate: CandidatePassage) -> dict[str, Any]:
    return {field: getattr(candidate, field) for field in SNAPSHOT_FIELDS}


def _canonical_score(value: float) -> float:
    return 0.0 if value == 0.0 else float(value)


def audit_pool(
    runs: Mapping[str, Iterable[RunEntry]],
    qrels: Mapping[str, Mapping[str, int]],
    corpus: Mapping[str, CandidatePassage] | Iterable[CandidatePassage],
    *,
    system_ids: Sequence[str] = FORMAL_SYSTEM_IDS,
    depth: int = FORMAL_POOL_DEPTH,
) -> dict[str, Any]:
    """Audit complete formal runs and return deterministic expansion data.

    ``system_ids`` defaults to the five required retrieval-v1 comparison
    systems but remains injectable for isolated tests and future controls.
    The returned ``pool_expansion_records`` are suitable for canonical JSONL;
    no judgment is invented for an absent pair.
    """

    ordered_systems = _system_ids(system_ids)
    audit_depth = _depth(depth)
    candidates = _candidate_index(corpus)
    judged_pairs = _normalise_qrels(qrels, known_chunks=set(candidates))
    grouped_runs = _normalise_runs(
        runs,
        system_ids=ordered_systems,
        depth=audit_depth,
        known_chunks=set(candidates),
    )

    judged_counts: dict[str, dict[int, int]] = {}
    per_system: dict[str, dict[str, Any]] = {}
    for system_id in ordered_systems:
        by_query = grouped_runs[system_id]
        counts = {
            cutoff: sum(
                (query_id, entry.chunk_id) in judged_pairs
                for query_id in sorted(by_query)
                for entry in by_query[query_id]
                if entry.rank <= cutoff
            )
            for cutoff in JUDGED_CUTOFFS
        }
        judged_counts[system_id] = counts
        query_count = len(by_query)
        per_system[system_id] = {
            "query_count": query_count,
            **{
                f"Judged@{cutoff}": counts[cutoff] / (query_count * cutoff)
                for cutoff in JUDGED_CUTOFFS
            },
        }

    query_count = len(next(iter(grouped_runs.values())))
    overall = {
        "system_count": len(ordered_systems),
        "query_count_per_system": query_count,
        **{
            f"Judged@{cutoff}": math.fsum(
                judged_counts[system_id][cutoff] for system_id in ordered_systems
            )
            / (len(ordered_systems) * query_count * cutoff)
            for cutoff in JUDGED_CUTOFFS
        },
    }

    unjudged: dict[tuple[str, str], dict[str, Any]] = {}
    unjudged_occurrences = 0
    for system_id in ordered_systems:
        for query_id in sorted(grouped_runs[system_id]):
            for entry in grouped_runs[system_id][query_id]:
                pair = (query_id, entry.chunk_id)
                if pair in judged_pairs:
                    continue
                unjudged_occurrences += 1
                record = unjudged.setdefault(
                    pair,
                    {
                        "query_id": query_id,
                        "chunk_id": entry.chunk_id,
                        "retrieved_by": [],
                        "ranks": {name: None for name in ordered_systems},
                        "scores": {name: None for name in ordered_systems},
                        "reason": EXPANSION_REASON,
                        "relevance": None,
                        "judgment_status": EXPANSION_JUDGMENT_STATUS,
                        "chunk_snapshot": _snapshot(candidates[entry.chunk_id]),
                    },
                )
                record["ranks"][system_id] = entry.rank
                record["scores"][system_id] = _canonical_score(entry.score)

    expansion_records: list[dict[str, Any]] = []
    for pair in sorted(unjudged):
        record = unjudged[pair]
        record["retrieved_by"] = [
            system_id
            for system_id in ordered_systems
            if record["ranks"][system_id] is not None
        ]
        expansion_records.append(record)

    expansion_required = bool(expansion_records)
    return {
        "schema_version": POOL_AUDIT_SCHEMA_VERSION,
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "evaluation_integrity_status": "BLOCKED" if expansion_required else "PASS",
        "pool_expansion_required": expansion_required,
        "required_Judged@30": 1.0,
        "cutoffs": list(JUDGED_CUTOFFS),
        "system_ids": list(ordered_systems),
        "pool_depth": audit_depth,
        "per_system": per_system,
        "overall": overall,
        "unjudged_top30_occurrence_count": unjudged_occurrences,
        "pool_expansion_record_count": len(expansion_records),
        "pool_expansion_records": expansion_records,
    }


def pool_expansion_artifact_data(
    audit_result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Split an audit into deterministic JSONL records and JSON summary data."""

    required = {
        "schema_version",
        "evaluation_label",
        "machine_proposed_development_only",
        "evaluation_integrity_status",
        "pool_expansion_required",
        "required_Judged@30",
        "cutoffs",
        "system_ids",
        "pool_depth",
        "per_system",
        "overall",
        "unjudged_top30_occurrence_count",
        "pool_expansion_record_count",
        "pool_expansion_records",
    }
    missing = sorted(required - set(audit_result))
    if missing:
        raise PoolAuditError(f"audit result is missing fields: {missing!r}")
    records = audit_result["pool_expansion_records"]
    if not isinstance(records, list):
        raise PoolAuditError("pool_expansion_records must be a list")
    if audit_result["pool_expansion_record_count"] != len(records):
        raise PoolAuditError("pool expansion record count does not match records")
    summary = {
        key: deepcopy(value)
        for key, value in audit_result.items()
        if key != "pool_expansion_records"
    }
    return deepcopy(records), summary


check_pool_completeness = audit_pool


__all__ = [
    "ALLOWED_RELEVANCE_GRADES",
    "EVALUATION_LABEL",
    "EXPANSION_JUDGMENT_STATUS",
    "EXPANSION_REASON",
    "FORMAL_POOL_DEPTH",
    "FORMAL_SYSTEM_IDS",
    "JUDGED_CUTOFFS",
    "POOL_AUDIT_SCHEMA_VERSION",
    "PoolAuditError",
    "SNAPSHOT_FIELDS",
    "audit_pool",
    "check_pool_completeness",
    "pool_expansion_artifact_data",
]
