"""Audit formal-run judgment coverage and emit pool-expansion artifacts.

An absent qrel is an *unjudged* query/chunk pair.  This module deliberately
does not turn that absence into relevance zero: every missing pair in a formal
top 30 becomes a judgment request whose ``relevance`` value remains ``None``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from .metrics import normalise_qrels
from .qrels import QrelEntry, validate_qrels
from .trec import TrecRunEntry, validate_trec_run


FORMAL_SYSTEM_IDS = (
    "bm25_formal",
    "dense_formal",
    "hybrid_rrf_formal",
)
JUDGED_CUTOFFS = (5, 10, 20, 30)
FORMAL_POOL_DEPTH = 30
POOL_AUDIT_SCHEMA_VERSION = "sqlmend-pool-audit-v1"
EXPANSION_REASON = "unjudged_in_formal_top30"
EXPANSION_JUDGMENT_STATUS = "human_or_separate_machine_judgment_required"
SNAPSHOT_FIELDS = ("dialect", "version", "title", "section", "text")


class PoolAuditError(ValueError):
    """Raised when inputs cannot support a valid formal pool audit."""


def _system_ids(system_ids: Sequence[str]) -> tuple[str, ...]:
    result = tuple(system_ids)
    if not result:
        raise PoolAuditError("system_ids cannot be empty")
    if len(result) != len(set(result)):
        raise PoolAuditError("system_ids cannot contain duplicates")
    for system_id in result:
        if (
            not isinstance(system_id, str)
            or not system_id
            or system_id.strip() != system_id
            or any(character.isspace() for character in system_id)
        ):
            raise PoolAuditError(
                "system IDs must be non-empty, whitespace-free strings"
            )
    return result


def _corpus_index(
    corpus_records: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if isinstance(corpus_records, (str, bytes)):
        raise TypeError("corpus_records must be an iterable of mappings")

    index: dict[str, dict[str, Any]] = {}
    for position, record in enumerate(corpus_records, start=1):
        if not isinstance(record, Mapping):
            raise PoolAuditError(f"corpus record {position} must be a mapping")
        chunk_id = record.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise PoolAuditError(
                f"corpus record {position} has no non-empty chunk_id"
            )
        if chunk_id in index:
            raise PoolAuditError(f"duplicate corpus chunk_id: {chunk_id!r}")

        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            raise PoolAuditError(f"corpus chunk {chunk_id!r} has empty text")

        snapshot: dict[str, Any] = {}
        for field in SNAPSHOT_FIELDS:
            value = record.get(field)
            if value is not None and not isinstance(value, str):
                raise PoolAuditError(
                    f"corpus chunk {chunk_id!r} field {field!r} must be str or null"
                )
            snapshot[field] = value
        index[chunk_id] = snapshot

    if not index:
        raise PoolAuditError("corpus_records cannot be empty")
    return index


def _normalise_qrel_entries(
    qrels: Mapping[Any, Any] | Iterable[Any],
    *,
    known_chunk_ids: Iterable[str],
) -> list[QrelEntry]:
    """Accept the project's flat entries as well as convenient qrel mappings."""

    if isinstance(qrels, Mapping):
        nested = normalise_qrels(qrels)
        entries = [
            QrelEntry(query_id, chunk_id, relevance)
            for query_id, judgments in nested.items()
            for chunk_id, relevance in judgments.items()
        ]
    else:
        if isinstance(qrels, (str, bytes)):
            raise TypeError("qrels must not be a string")
        entries = list(qrels)

    return validate_qrels(
        entries,
        known_chunk_ids=known_chunk_ids,
        require_nonempty=False,
    )


def _normalise_runs(
    runs: Mapping[str, Iterable[TrecRunEntry | Mapping[str, Any] | object]],
    *,
    known_chunk_ids: Iterable[str],
    system_ids: tuple[str, ...],
) -> dict[str, dict[str, tuple[TrecRunEntry, ...]]]:
    if not isinstance(runs, Mapping):
        raise TypeError("runs must map system IDs to formal run records")
    observed_systems = set(runs)
    required_systems = set(system_ids)
    if observed_systems != required_systems:
        missing = sorted(required_systems - observed_systems)
        extra = sorted(observed_systems - required_systems)
        raise PoolAuditError(
            f"formal runs must match system_ids; missing={missing!r}, extra={extra!r}"
        )

    grouped_runs: dict[str, dict[str, tuple[TrecRunEntry, ...]]] = {}
    query_universe: set[str] | None = None
    for system_id in system_ids:
        entries = validate_trec_run(
            runs[system_id],
            known_chunk_ids=known_chunk_ids,
            min_results_per_query=FORMAL_POOL_DEPTH,
        )
        grouped: dict[str, list[TrecRunEntry]] = defaultdict(list)
        for entry in entries:
            if entry.rank <= FORMAL_POOL_DEPTH:
                grouped[entry.query_id].append(entry)
        ordered = {
            query_id: tuple(sorted(query_entries, key=lambda entry: entry.rank))
            for query_id, query_entries in grouped.items()
        }
        # min_results_per_query above guarantees ranks 1..30 exist.  Keep this
        # explicit check close to the top-30 truncation to guard future changes.
        for query_id, query_entries in ordered.items():
            if len(query_entries) != FORMAL_POOL_DEPTH:
                raise PoolAuditError(
                    f"system {system_id!r}, query {query_id!r} has no complete top 30"
                )

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
        grouped_runs[system_id] = ordered

    return grouped_runs


def _score(value: float) -> float:
    """Canonicalise negative zero before deterministic JSON serialization."""

    return 0.0 if value == 0.0 else value


def audit_pool(
    runs: Mapping[str, Iterable[TrecRunEntry | Mapping[str, Any] | object]],
    qrels: Mapping[Any, Any] | Iterable[Any],
    corpus_records: Iterable[Mapping[str, Any]],
    *,
    system_ids: Sequence[str] = FORMAL_SYSTEM_IDS,
) -> dict[str, Any]:
    """Compute Judged@5/10/20/30 and build deterministic judgment requests.

    ``runs`` must contain one complete formal top 30 for every query and every
    requested system.  Explicit relevance-zero qrels count as judged.  An
    absent qrel remains absent and is represented only by a pool-expansion
    record with ``relevance: None``.
    """

    ordered_systems = _system_ids(system_ids)
    corpus = _corpus_index(corpus_records)
    normalised_qrels = _normalise_qrel_entries(
        qrels, known_chunk_ids=corpus
    )
    judged_pairs = {
        (qrel.query_id, qrel.chunk_id) for qrel in normalised_qrels
    }
    grouped_runs = _normalise_runs(
        runs,
        known_chunk_ids=corpus,
        system_ids=ordered_systems,
    )

    per_system: dict[str, dict[str, Any]] = {}
    judged_counts: dict[str, dict[int, int]] = {}
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
            f"Judged@{cutoff}": sum(
                judged_counts[system_id][cutoff]
                for system_id in ordered_systems
            )
            / (len(ordered_systems) * query_count * cutoff)
            for cutoff in JUDGED_CUTOFFS
        },
    }

    unjudged: dict[tuple[str, str], dict[str, Any]] = {}
    unjudged_occurrences = 0
    for system_id in ordered_systems:
        by_query = grouped_runs[system_id]
        for query_id in sorted(by_query):
            for entry in by_query[query_id]:
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
                        # Missing judgments are intentionally never filled in.
                        "relevance": None,
                        "judgment_status": EXPANSION_JUDGMENT_STATUS,
                        "chunk_snapshot": dict(corpus[entry.chunk_id]),
                    },
                )
                record["ranks"][system_id] = entry.rank
                record["scores"][system_id] = _score(entry.score)

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
        "evaluation_label": "machine-proposed development evaluation",
        "machine_proposed_development_only": True,
        "evaluation_integrity_status": "BLOCKED" if expansion_required else "PASS",
        "pool_expansion_required": expansion_required,
        "required_Judged@30": 1.0,
        "cutoffs": list(JUDGED_CUTOFFS),
        "per_system": per_system,
        "overall": overall,
        "unjudged_top30_occurrence_count": unjudged_occurrences,
        "pool_expansion_record_count": len(expansion_records),
        "pool_expansion_records": expansion_records,
    }


def _summary(audit_result: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "evaluation_integrity_status",
        "pool_expansion_required",
        "required_Judged@30",
        "cutoffs",
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
    return {
        key: audit_result[key]
        for key in audit_result
        if key != "pool_expansion_records"
    }


def write_pool_expansion_artifacts(
    output_directory: str | Path,
    audit_result: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Write canonical JSONL and summary JSON using UTF-8 with LF endings."""

    summary = _summary(audit_result)
    records = audit_result["pool_expansion_records"]
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    records_path = output / "pool_expansion_required.jsonl"
    summary_path = output / "pool_expansion_summary.json"

    with records_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")

    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            summary,
            handle,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    return records_path, summary_path


def write_pool_audit(
    output_directory: str | Path,
    runs: Mapping[str, Iterable[TrecRunEntry | Mapping[str, Any] | object]],
    qrels: Mapping[Any, Any] | Iterable[Any],
    corpus_records: Iterable[Mapping[str, Any]],
    *,
    system_ids: Sequence[str] = FORMAL_SYSTEM_IDS,
) -> dict[str, Any]:
    """Run the audit, write both required artifacts, and return the result."""

    result = audit_pool(
        runs,
        qrels,
        corpus_records,
        system_ids=system_ids,
    )
    write_pool_expansion_artifacts(output_directory, result)
    return result


# A descriptive alias for callers implementing the ``check-pool`` CLI command.
check_pool_completeness = audit_pool


__all__ = [
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
    "write_pool_audit",
    "write_pool_expansion_artifacts",
]
