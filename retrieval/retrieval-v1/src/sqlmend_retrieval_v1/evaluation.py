"""Pooling-aware evaluation for retrieval v1.

Missing qrels are deliberately preserved as unjudged results.  They occupy
their retrieved rank, cannot contribute a known relevance gain, and are
reported separately through ``Judged@K``.  Compatibility metrics consume only
the safe :class:`OnlineQuery` projection and corpus-owned passage metadata.
The development-only ``case_flags`` enter solely in the offline slice join.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from .compatibility import dialect_compatibility, is_wrong_dialect, version_compatibility
from .models import CandidatePassage, OnlineQuery, RunEntry


ALLOWED_RELEVANCE_GRADES = frozenset({0, 1, 2})
DIALECTS = ("postgresql", "mysql", "sqlite", "mariadb", "duckdb")
EVALUATION_LABEL = "machine-proposed development evaluation"
EVALUATION_SCHEMA_VERSION = "sqlmend-retrieval-v1-evaluation-v1"
METRIC_NAMES = (
    "graded_nDCG@10",
    "MRR@10_rel2",
    "pooled_Recall@10_rel2",
    "HitRate@5_rel2",
    "Judged@5",
    "Judged@10",
    "Judged@20",
    "Judged@30",
    "Wrong-Dialect@5",
    "Wrong-Version@5",
    "Unknown-Version@5",
    "Unresolved-Current@5",
)


@dataclass(frozen=True, slots=True)
class QuerySlice:
    """A deterministic offline query slice."""

    slice_name: str
    slice_value: str
    source_field: str
    query_ids: tuple[str, ...]


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} must be a non-empty, trimmed string")
    return value


def _validate_entry(entry: Any) -> RunEntry:
    if not isinstance(entry, RunEntry):
        raise TypeError("run rows must be RunEntry instances")
    _require_identifier(entry.query_id, "run query_id")
    _require_identifier(entry.chunk_id, "run chunk_id")
    _require_identifier(entry.run_tag, "run_tag")
    if isinstance(entry.rank, bool) or not isinstance(entry.rank, int) or entry.rank <= 0:
        raise ValueError("run ranks must be positive integers")
    if not isinstance(entry.score, (int, float)) or isinstance(entry.score, bool):
        raise ValueError("run scores must be numeric")
    if not math.isfinite(float(entry.score)):
        raise ValueError("run scores must be finite")
    return entry


def normalise_run(
    run: Mapping[str, Iterable[RunEntry]] | Iterable[RunEntry],
) -> dict[str, tuple[RunEntry, ...]]:
    """Return query-grouped, rank-ordered rows with no implicit re-ranking."""

    grouped: dict[str, list[RunEntry]] = defaultdict(list)
    if isinstance(run, Mapping):
        for query_id, ranking in run.items():
            _require_identifier(query_id, "run query_id")
            if isinstance(ranking, (str, bytes)):
                raise TypeError("a query ranking must be an iterable of RunEntry rows")
            for entry in ranking:
                row = _validate_entry(entry)
                if row.query_id != query_id:
                    raise ValueError(
                        f"run mapping key {query_id!r} disagrees with row query_id "
                        f"{row.query_id!r}"
                    )
                grouped[query_id].append(row)
    else:
        if isinstance(run, (str, bytes)):
            raise TypeError("run must be an iterable of RunEntry rows")
        for entry in run:
            row = _validate_entry(entry)
            grouped[row.query_id].append(row)

    result: dict[str, tuple[RunEntry, ...]] = {}
    for query_id in sorted(grouped):
        rows = tuple(sorted(grouped[query_id], key=lambda row: row.rank))
        ranks = [row.rank for row in rows]
        if ranks != list(range(1, len(rows) + 1)):
            raise ValueError(f"run ranks are not continuous for query {query_id!r}")
        chunk_ids = [row.chunk_id for row in rows]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError(f"run contains duplicate chunks for query {query_id!r}")
        if len({row.run_tag for row in rows}) > 1:
            raise ValueError(f"run contains multiple tags for query {query_id!r}")
        result[query_id] = rows
    return result


def _normalise_qrels(
    qrels: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    if not isinstance(qrels, Mapping):
        raise TypeError("qrels must map query IDs to document relevance mappings")
    result: dict[str, dict[str, int]] = {}
    for query_id, judgments in qrels.items():
        _require_identifier(query_id, "qrel query_id")
        if not isinstance(judgments, Mapping):
            raise TypeError(f"qrels for {query_id!r} must be a mapping")
        query_judgments: dict[str, int] = {}
        for chunk_id, relevance in judgments.items():
            _require_identifier(chunk_id, "qrel chunk_id")
            if (
                isinstance(relevance, bool)
                or not isinstance(relevance, int)
                or relevance not in ALLOWED_RELEVANCE_GRADES
            ):
                raise ValueError(
                    f"qrel relevance must be one of {sorted(ALLOWED_RELEVANCE_GRADES)}"
                )
            query_judgments[chunk_id] = relevance
        result[query_id] = query_judgments
    return result


def _query_index(
    queries: Mapping[str, OnlineQuery] | Iterable[OnlineQuery],
) -> dict[str, OnlineQuery]:
    if isinstance(queries, Mapping):
        items = queries.items()
    else:
        if isinstance(queries, (str, bytes)):
            raise TypeError("queries must contain OnlineQuery instances")
        items = ((query.query_id, query) for query in queries)

    result: dict[str, OnlineQuery] = {}
    for key, query in items:
        if not isinstance(query, OnlineQuery):
            raise TypeError("queries must contain OnlineQuery instances")
        query_id = _require_identifier(query.query_id, "query_id")
        if key != query_id:
            raise ValueError(f"query mapping key {key!r} disagrees with {query_id!r}")
        if query_id in result:
            raise ValueError(f"duplicate OnlineQuery: {query_id!r}")
        result[query_id] = query
    if not result:
        raise ValueError("queries cannot be empty")
    return dict(sorted(result.items()))


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
        chunk_id = _require_identifier(candidate.chunk_id, "corpus chunk_id")
        if key != chunk_id:
            raise ValueError(f"corpus mapping key {key!r} disagrees with {chunk_id!r}")
        if chunk_id in result:
            raise ValueError(f"duplicate corpus chunk_id: {chunk_id!r}")
        result[chunk_id] = candidate
    if not result:
        raise ValueError("corpus cannot be empty")
    return result


def _dcg(relevances: Sequence[int | None], cutoff: int) -> float:
    total = 0.0
    for rank, relevance in enumerate(relevances[:cutoff], start=1):
        if relevance is not None:
            total += (2.0**relevance - 1.0) / math.log2(rank + 1.0)
    return total


def _same_explicit_dialect(query: OnlineQuery, candidate: CandidatePassage) -> bool:
    return dialect_compatibility(query.dialect, candidate.dialect) == "compatible"


def evaluate_query(
    ranking: Iterable[RunEntry],
    judgments: Mapping[str, int],
    query: OnlineQuery,
    corpus: Mapping[str, CandidatePassage] | Iterable[CandidatePassage],
) -> dict[str, float]:
    """Compute all required per-query metrics.

    ``Unknown-Version@5`` is intentionally narrow: the query and passage have
    the same explicit dialect and the corpus says ``version_status=unknown``.
    ``Unresolved-Current@5`` counts same-dialect current-documentation passages
    for which the conservative version classifier cannot establish a numeric
    compatibility decision.
    """

    query = _query_index((query,))[query.query_id]
    rows_by_query = normalise_run(tuple(ranking))
    if len(rows_by_query) > 1:
        raise ValueError("evaluate_query accepts rows for exactly one query")
    rows = next(iter(rows_by_query.values()), ())
    if rows and rows[0].query_id != query.query_id:
        raise ValueError("ranking query_id does not match OnlineQuery")
    qrels = _normalise_qrels({query.query_id: judgments})[query.query_id]
    candidates = _candidate_index(corpus)
    unknown_chunks = sorted({row.chunk_id for row in rows} - set(candidates))
    if unknown_chunks:
        raise ValueError(f"run contains unknown chunks: {unknown_chunks[:3]}")

    ranked_relevances = tuple(qrels.get(row.chunk_id) for row in rows)
    ideal_relevances = tuple(sorted(qrels.values(), reverse=True))
    ideal_dcg = _dcg(ideal_relevances, 10)
    ndcg = 0.0 if ideal_dcg == 0.0 else _dcg(ranked_relevances, 10) / ideal_dcg

    reciprocal_rank = 0.0
    for rank, relevance in enumerate(ranked_relevances[:10], start=1):
        if relevance is not None and relevance >= 2:
            reciprocal_rank = 1.0 / rank
            break

    relevant = {chunk_id for chunk_id, relevance in qrels.items() if relevance >= 2}
    retrieved_at_10 = {row.chunk_id for row in rows[:10]}
    pooled_recall = len(relevant & retrieved_at_10) / len(relevant) if relevant else 0.0
    hit_rate = float(
        any(relevance is not None and relevance >= 2 for relevance in ranked_relevances[:5])
    )

    top_five = [candidates[row.chunk_id] for row in rows[:5]]
    wrong_dialect = sum(
        is_wrong_dialect(query.dialect, candidate.dialect) for candidate in top_five
    )
    version_decisions = [version_compatibility(query, candidate) for candidate in top_five]
    wrong_version = sum(decision.category == "incompatible" for decision in version_decisions)
    unknown_version = sum(
        _same_explicit_dialect(query, candidate)
        and candidate.version_status.strip().casefold() == "unknown"
        for candidate in top_five
    )
    unresolved_current = sum(
        _same_explicit_dialect(query, candidate)
        and candidate.version_status.strip().casefold() == "current"
        and decision.category == "unknown"
        for candidate, decision in zip(top_five, version_decisions, strict=True)
    )

    return {
        "graded_nDCG@10": ndcg,
        "MRR@10_rel2": reciprocal_rank,
        "pooled_Recall@10_rel2": pooled_recall,
        "HitRate@5_rel2": hit_rate,
        **{
            f"Judged@{cutoff}": sum(
                row.chunk_id in qrels for row in rows[:cutoff]
            )
            / cutoff
            for cutoff in (5, 10, 20, 30)
        },
        "Wrong-Dialect@5": wrong_dialect / 5.0,
        "Wrong-Version@5": wrong_version / 5.0,
        "Unknown-Version@5": unknown_version / 5.0,
        "Unresolved-Current@5": unresolved_current / 5.0,
    }


def aggregate_per_query(
    per_query: Mapping[str, Mapping[str, float]],
) -> dict[str, float | None]:
    """Macro-average the fixed retrieval-v1 metric set."""

    if not per_query:
        return {metric: None for metric in METRIC_NAMES}
    aggregate: dict[str, float | None] = {}
    for metric in METRIC_NAMES:
        values: list[float] = []
        for query_id in sorted(per_query):
            if metric not in per_query[query_id]:
                raise KeyError(f"query {query_id!r} has no metric {metric!r}")
            value = float(per_query[query_id][metric])
            if not math.isfinite(value):
                raise ValueError(f"metric {metric!r} is non-finite for {query_id!r}")
            values.append(value)
        aggregate[metric] = math.fsum(values) / len(values)
    return aggregate


def evaluate_run(
    run: Mapping[str, Iterable[RunEntry]] | Iterable[RunEntry],
    qrels: Mapping[str, Mapping[str, int]],
    queries: Mapping[str, OnlineQuery] | Iterable[OnlineQuery],
    corpus: Mapping[str, CandidatePassage] | Iterable[CandidatePassage],
) -> dict[str, Any]:
    """Evaluate a complete system run over the explicit safe query universe."""

    grouped_run = normalise_run(run)
    query_index = _query_index(queries)
    qrel_index = _normalise_qrels(qrels)
    candidate_index = _candidate_index(corpus)
    if set(grouped_run) != set(query_index):
        missing = sorted(set(query_index) - set(grouped_run))
        extra = sorted(set(grouped_run) - set(query_index))
        raise ValueError(f"run query coverage differs; missing={missing!r}, extra={extra!r}")

    per_query = {
        query_id: evaluate_query(
            grouped_run[query_id],
            qrel_index.get(query_id, {}),
            query_index[query_id],
            candidate_index,
        )
        for query_id in sorted(query_index)
    }
    return {
        "evaluation_label": EVALUATION_LABEL,
        "query_count": len(query_index),
        "overall": aggregate_per_query(per_query),
        "per_query": per_query,
    }


def _case_flag_index(
    case_records: Iterable[Mapping[str, Any]],
    *,
    expected_query_ids: set[str],
) -> dict[str, tuple[bool, bool]]:
    """Extract only the two offline flags needed for required slices."""

    if isinstance(case_records, (str, bytes, Mapping)):
        raise TypeError("case_records must be an iterable of query mappings")
    result: dict[str, tuple[bool, bool]] = {}
    for record in case_records:
        if not isinstance(record, Mapping):
            raise TypeError("case_records must contain mappings")
        query_id = _require_identifier(record.get("query_id"), "case record query_id")
        if query_id in result:
            raise ValueError(f"duplicate case record: {query_id!r}")
        flags = record.get("case_flags")
        if not isinstance(flags, Mapping):
            raise ValueError(f"missing explicit case_flags for query {query_id!r}")
        values: list[bool] = []
        for field in ("requires_dialect_reasoning", "requires_version_reasoning"):
            value = flags.get(field)
            if not isinstance(value, bool):
                raise ValueError(
                    f"case_flags.{field} must be an explicit boolean for {query_id!r}"
                )
            values.append(value)
        result[query_id] = (values[0], values[1])
    if set(result) != expected_query_ids:
        missing = sorted(expected_query_ids - set(result))
        extra = sorted(set(result) - expected_query_ids)
        raise ValueError(
            f"offline case-record coverage differs; missing={missing!r}, extra={extra!r}"
        )
    return result


def build_query_slices(
    queries: Mapping[str, OnlineQuery] | Iterable[OnlineQuery],
    case_records: Iterable[Mapping[str, Any]],
) -> tuple[QuerySlice, ...]:
    """Build required slices without exposing case flags to online ranking."""

    query_index = _query_index(queries)
    flags = _case_flag_index(case_records, expected_query_ids=set(query_index))
    dialect_members: dict[str, list[str]] = {dialect: [] for dialect in DIALECTS}
    for query_id, query in query_index.items():
        dialect = query.dialect.strip().casefold() if isinstance(query.dialect, str) else None
        if dialect not in dialect_members:
            raise ValueError(f"unknown or missing query dialect for {query_id!r}: {query.dialect!r}")
        dialect_members[dialect].append(query_id)

    slices = [
        QuerySlice(
            "dialect",
            dialect,
            "dialect",
            tuple(sorted(dialect_members[dialect])),
        )
        for dialect in DIALECTS
    ]
    slices.extend(
        (
            QuerySlice(
                "case_flag",
                "dialect-sensitive",
                "case_flags.requires_dialect_reasoning",
                tuple(sorted(query_id for query_id, value in flags.items() if value[0])),
            ),
            QuerySlice(
                "case_flag",
                "version-sensitive",
                "case_flags.requires_version_reasoning",
                tuple(sorted(query_id for query_id, value in flags.items() if value[1])),
            ),
        )
    )
    return tuple(slices)


def evaluate_slices(
    per_query: Mapping[str, Mapping[str, float]],
    queries: Mapping[str, OnlineQuery] | Iterable[OnlineQuery],
    case_records: Iterable[Mapping[str, Any]],
    *,
    system_id: str,
) -> list[dict[str, Any]]:
    """Offline-join explicit slice membership to an existing per-query table."""

    _require_identifier(system_id, "system_id")
    query_index = _query_index(queries)
    if set(per_query) != set(query_index):
        raise ValueError("per-query metrics do not match the safe query universe")
    rows: list[dict[str, Any]] = []
    for query_slice in build_query_slices(query_index, case_records):
        selected = {query_id: per_query[query_id] for query_id in query_slice.query_ids}
        row: dict[str, Any] = {
            "slice_name": query_slice.slice_name,
            "slice_value": query_slice.slice_value,
            "source_field": query_slice.source_field,
            "query_count": len(query_slice.query_ids),
            "system_id": system_id,
            **aggregate_per_query(selected),
        }
        row["estimate_warning"] = (
            "empty slice; metrics are undefined"
            if not selected
            else "small sample; estimates may be unstable"
            if len(selected) < 30
            else None
        )
        rows.append(row)
    return rows


def evaluate_system(
    run: Mapping[str, Iterable[RunEntry]] | Iterable[RunEntry],
    qrels: Mapping[str, Mapping[str, int]],
    queries: Mapping[str, OnlineQuery] | Iterable[OnlineQuery],
    corpus: Mapping[str, CandidatePassage] | Iterable[CandidatePassage],
    case_records: Iterable[Mapping[str, Any]],
    *,
    system_id: str,
) -> dict[str, Any]:
    """Return overall, per-query, and required slice metrics for one system."""

    _require_identifier(system_id, "system_id")
    query_values = tuple(queries.values()) if isinstance(queries, Mapping) else tuple(queries)
    case_values = tuple(case_records)
    evaluation = evaluate_run(run, qrels, query_values, corpus)
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluation_label": EVALUATION_LABEL,
        "system_id": system_id,
        "query_count": evaluation["query_count"],
        "overall": evaluation["overall"],
        "slices": evaluate_slices(
            evaluation["per_query"],
            query_values,
            case_values,
            system_id=system_id,
        ),
        "per_query": evaluation["per_query"],
    }


# American-English aliases for callers that use ``normalize`` terminology.
normalize_run = normalise_run


__all__ = [
    "ALLOWED_RELEVANCE_GRADES",
    "DIALECTS",
    "EVALUATION_LABEL",
    "EVALUATION_SCHEMA_VERSION",
    "METRIC_NAMES",
    "QuerySlice",
    "aggregate_per_query",
    "build_query_slices",
    "evaluate_query",
    "evaluate_run",
    "evaluate_slices",
    "evaluate_system",
    "normalise_run",
    "normalize_run",
]
