"""Deterministic, pooling-aware retrieval metrics.

The qrels used by this project are pooled judgments.  A document absent from
the qrels is therefore represented as ``None`` (unjudged), never inserted as a
relevance-zero judgment.  Unjudged ranks cannot contribute a known relevant
hit, but they retain their rank position and are counted separately by
``Judged@K``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

ALLOWED_RELEVANCE_GRADES = frozenset({0, 1, 2})
EVALUATION_LABEL = "machine-proposed development evaluation"

REQUIRED_METRIC_NAMES = (
    "graded_nDCG@10",
    "MRR@10_rel2",
    "pooled_Recall@5_rel2",
    "pooled_Recall@10_rel2",
    "pooled_Recall@20_rel2",
    "Precision@5_rel2",
    "HitRate@5_rel2",
    "HitRate@10_rel2",
    "MRR@10_rel1plus",
    "pooled_Recall@5_rel1plus",
    "pooled_Recall@10_rel1plus",
    "pooled_Recall@20_rel1plus",
    "Precision@5_rel1plus",
    "HitRate@5_rel1plus",
    "Judged@5",
    "Judged@10",
    "Judged@20",
    "Judged@30",
)

PRIMARY_BOOTSTRAP_METRICS = (
    "graded_nDCG@10",
    "MRR@10_rel2",
    "pooled_Recall@10_rel2",
    "HitRate@5_rel2",
)


def _positive_k(k: int) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("k must be a positive integer")
    return k


def _field(record: Any, names: Sequence[str]) -> Any:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
    else:
        for name in names:
            if hasattr(record, name):
                return getattr(record, name)
    raise ValueError(f"Record has none of the required fields: {', '.join(names)}")


def _document_id(item: Any) -> str:
    if isinstance(item, str):
        document_id = item
    elif isinstance(item, Mapping) or any(
        hasattr(item, name) for name in ("chunk_id", "doc_id", "document_id")
    ):
        document_id = _field(item, ("chunk_id", "doc_id", "document_id"))
    elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and item:
        document_id = item[0]
    else:
        document_id = _field(item, ("chunk_id", "doc_id", "document_id"))
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("Document identifiers must be non-empty strings")
    return document_id


def _rank(item: Any) -> int | None:
    try:
        value = _field(item, ("rank",))
    except ValueError:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Run ranks must be positive integers")
    return value


def normalise_ranking(ranking: Iterable[Any]) -> tuple[str, ...]:
    """Return a ranked tuple of document IDs and reject duplicate documents.

    If every supplied run record has an explicit ``rank``, the records are
    ordered by that rank.  Plain sequences of document IDs retain their input
    order.
    """

    if isinstance(ranking, (str, bytes)):
        raise TypeError("A ranking must be an iterable of documents, not a string")
    items = list(ranking)
    ranks = [_rank(item) for item in items]
    if items and all(rank is not None for rank in ranks):
        indexed = list(enumerate(zip(items, ranks, strict=True)))
        indexed.sort(key=lambda pair: (pair[1][1], pair[0]))
        items = [pair[1][0] for pair in indexed]
    document_ids = tuple(_document_id(item) for item in items)
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("A ranking cannot contain the same document more than once")
    return document_ids


def _validate_relevance(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Relevance must be an integer in {sorted(ALLOWED_RELEVANCE_GRADES)}")
    if value not in ALLOWED_RELEVANCE_GRADES:
        raise ValueError(f"Unsupported relevance grade: {value}")
    return value


def normalise_judgments(judgments: Mapping[str, Any] | Iterable[Any]) -> dict[str, int]:
    """Normalise one query's judgments without manufacturing missing qrels."""

    result: dict[str, int] = {}
    if isinstance(judgments, Mapping):
        records = judgments.items()
        for document_id, relevance in records:
            if not isinstance(document_id, str) or not document_id:
                raise ValueError("Qrel document identifiers must be non-empty strings")
            result[document_id] = _validate_relevance(relevance)
        return result

    if isinstance(judgments, (str, bytes)):
        raise TypeError("Judgments must not be a string")
    for record in judgments:
        document_id = _field(record, ("chunk_id", "doc_id", "document_id"))
        relevance = _field(record, ("relevance", "rel"))
        if document_id in result:
            raise ValueError(f"Duplicate qrel for document {document_id!r}")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("Qrel document identifiers must be non-empty strings")
        result[document_id] = _validate_relevance(relevance)
    return result


def normalise_qrels(qrels: Mapping[Any, Any] | Iterable[Any]) -> dict[str, dict[str, int]]:
    """Normalise nested qrels or flat qrel records by query ID."""

    if isinstance(qrels, Mapping):
        if qrels and all(isinstance(key, tuple) and len(key) == 2 for key in qrels):
            nested: dict[str, dict[str, int]] = defaultdict(dict)
            for (query_id, document_id), relevance in qrels.items():
                if document_id in nested[query_id]:
                    raise ValueError(f"Duplicate qrel for {(query_id, document_id)!r}")
                nested[query_id][document_id] = _validate_relevance(relevance)
            return dict(nested)
        result: dict[str, dict[str, int]] = {}
        for query_id, judgments in qrels.items():
            if not isinstance(query_id, str) or not query_id:
                raise ValueError("Qrel query identifiers must be non-empty strings")
            result[query_id] = normalise_judgments(judgments)
        return result

    if isinstance(qrels, (str, bytes)):
        raise TypeError("Qrels must not be a string")
    result = defaultdict(dict)
    for record in qrels:
        query_id = _field(record, ("query_id", "qid"))
        document_id = _field(record, ("chunk_id", "doc_id", "document_id"))
        relevance = _field(record, ("relevance", "rel"))
        if not isinstance(query_id, str) or not query_id:
            raise ValueError("Qrel query identifiers must be non-empty strings")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("Qrel document identifiers must be non-empty strings")
        if document_id in result[query_id]:
            raise ValueError(f"Duplicate qrel for {(query_id, document_id)!r}")
        result[query_id][document_id] = _validate_relevance(relevance)
    return dict(result)


def normalise_run(run: Mapping[str, Iterable[Any]] | Iterable[Any]) -> dict[str, tuple[str, ...]]:
    """Normalise a nested run or a flat iterable of run records."""

    if isinstance(run, Mapping):
        result: dict[str, tuple[str, ...]] = {}
        for query_id, ranking in run.items():
            if not isinstance(query_id, str) or not query_id:
                raise ValueError("Run query identifiers must be non-empty strings")
            result[query_id] = normalise_ranking(ranking)
        return result

    if isinstance(run, (str, bytes)):
        raise TypeError("A run must not be a string")
    grouped: dict[str, list[Any]] = defaultdict(list)
    for record in run:
        query_id = _field(record, ("query_id", "qid"))
        if not isinstance(query_id, str) or not query_id:
            raise ValueError("Run query identifiers must be non-empty strings")
        grouped[query_id].append(record)
    return {query_id: normalise_ranking(records) for query_id, records in grouped.items()}


def ranked_relevances(
    ranking: Iterable[Any], judgments: Mapping[str, Any] | Iterable[Any]
) -> tuple[int | None, ...]:
    """Return grades aligned to ranks, preserving ``None`` for unjudged docs."""

    document_ids = normalise_ranking(ranking)
    qrels = normalise_judgments(judgments)
    return tuple(qrels[document_id] if document_id in qrels else None for document_id in document_ids)


def dcg_at_k(relevances: Iterable[int | None], k: int) -> float:
    """Compute DCG with exponential gain ``2**rel - 1``."""

    _positive_k(k)
    total = 0.0
    for rank, relevance in enumerate(list(relevances)[:k], start=1):
        if relevance is None:
            continue
        grade = _validate_relevance(relevance)
        total += (2.0**grade - 1.0) / math.log2(rank + 1.0)
    return total


def graded_ndcg_at_k(
    ranking: Iterable[Any], judgments: Mapping[str, Any] | Iterable[Any], k: int = 10
) -> float:
    """Compute graded nDCG@K using exponential gains."""

    _positive_k(k)
    qrels = normalise_judgments(judgments)
    observed = ranked_relevances(ranking, qrels)
    ideal = sorted(qrels.values(), reverse=True)
    ideal_dcg = dcg_at_k(ideal, k)
    return 0.0 if ideal_dcg == 0.0 else dcg_at_k(observed, k) / ideal_dcg


def ndcg_at_k(
    ranking: Iterable[Any], judgments: Mapping[str, Any] | Iterable[Any], k: int = 10
) -> float:
    """Alias for :func:`graded_ndcg_at_k`."""

    return graded_ndcg_at_k(ranking, judgments, k)


def reciprocal_rank_at_k(
    ranking: Iterable[Any],
    judgments: Mapping[str, Any] | Iterable[Any],
    k: int = 10,
    *,
    minimum_relevance: int = 2,
) -> float:
    """Return reciprocal rank of the first judged relevant result."""

    _positive_k(k)
    if minimum_relevance not in (1, 2):
        raise ValueError("minimum_relevance must be 1 or 2")
    for rank, relevance in enumerate(ranked_relevances(ranking, judgments)[:k], start=1):
        if relevance is not None and relevance >= minimum_relevance:
            return 1.0 / rank
    return 0.0


def mrr_at_k(
    ranking: Iterable[Any],
    judgments: Mapping[str, Any] | Iterable[Any],
    k: int = 10,
    *,
    minimum_relevance: int = 2,
) -> float:
    """Per-query reciprocal-rank contribution used to compute MRR."""

    return reciprocal_rank_at_k(
        ranking, judgments, k, minimum_relevance=minimum_relevance
    )


def pooled_recall_at_k(
    ranking: Iterable[Any],
    judgments: Mapping[str, Any] | Iterable[Any],
    k: int,
    *,
    minimum_relevance: int = 2,
) -> float:
    """Recall over explicitly judged relevant documents in the qrel pool."""

    _positive_k(k)
    if minimum_relevance not in (1, 2):
        raise ValueError("minimum_relevance must be 1 or 2")
    qrels = normalise_judgments(judgments)
    relevant = {doc_id for doc_id, rel in qrels.items() if rel >= minimum_relevance}
    if not relevant:
        return 0.0
    retrieved = set(normalise_ranking(ranking)[:k])
    return len(relevant & retrieved) / len(relevant)


def recall_at_k(
    ranking: Iterable[Any],
    judgments: Mapping[str, Any] | Iterable[Any],
    k: int,
    *,
    minimum_relevance: int = 2,
) -> float:
    """Alias whose result must be reported as *pooled Recall*."""

    return pooled_recall_at_k(
        ranking, judgments, k, minimum_relevance=minimum_relevance
    )


def precision_at_k(
    ranking: Iterable[Any],
    judgments: Mapping[str, Any] | Iterable[Any],
    k: int,
    *,
    minimum_relevance: int = 2,
) -> float:
    """Return known relevant hits divided by K (also for short rankings)."""

    _positive_k(k)
    if minimum_relevance not in (1, 2):
        raise ValueError("minimum_relevance must be 1 or 2")
    relevances = ranked_relevances(ranking, judgments)[:k]
    hits = sum(
        relevance is not None and relevance >= minimum_relevance
        for relevance in relevances
    )
    return hits / k


def hit_rate_at_k(
    ranking: Iterable[Any],
    judgments: Mapping[str, Any] | Iterable[Any],
    k: int,
    *,
    minimum_relevance: int = 2,
) -> float:
    """Return the per-query hit indicator (its macro mean is HitRate@K)."""

    _positive_k(k)
    if minimum_relevance not in (1, 2):
        raise ValueError("minimum_relevance must be 1 or 2")
    return float(
        any(
            relevance is not None and relevance >= minimum_relevance
            for relevance in ranked_relevances(ranking, judgments)[:k]
        )
    )


def judged_at_k(
    ranking: Iterable[Any], judgments: Mapping[str, Any] | Iterable[Any], k: int
) -> float:
    """Compute Judged@K with the required fixed denominator K."""

    _positive_k(k)
    qrels = normalise_judgments(judgments)
    judged = sum(document_id in qrels for document_id in normalise_ranking(ranking)[:k])
    return judged / k


def judged_coverage_at_k(
    ranking: Iterable[Any], judgments: Mapping[str, Any] | Iterable[Any], k: int
) -> float:
    """Alias for :func:`judged_at_k`."""

    return judged_at_k(ranking, judgments, k)


def evaluate_query(
    ranking: Iterable[Any], judgments: Mapping[str, Any] | Iterable[Any]
) -> dict[str, float]:
    """Compute every required metric contribution for one query."""

    ranked = normalise_ranking(ranking)
    judged = normalise_judgments(judgments)
    return {
        "graded_nDCG@10": graded_ndcg_at_k(ranked, judged, 10),
        "MRR@10_rel2": reciprocal_rank_at_k(ranked, judged, 10, minimum_relevance=2),
        "pooled_Recall@5_rel2": pooled_recall_at_k(
            ranked, judged, 5, minimum_relevance=2
        ),
        "pooled_Recall@10_rel2": pooled_recall_at_k(
            ranked, judged, 10, minimum_relevance=2
        ),
        "pooled_Recall@20_rel2": pooled_recall_at_k(
            ranked, judged, 20, minimum_relevance=2
        ),
        "Precision@5_rel2": precision_at_k(ranked, judged, 5, minimum_relevance=2),
        "HitRate@5_rel2": hit_rate_at_k(ranked, judged, 5, minimum_relevance=2),
        "HitRate@10_rel2": hit_rate_at_k(ranked, judged, 10, minimum_relevance=2),
        "MRR@10_rel1plus": reciprocal_rank_at_k(
            ranked, judged, 10, minimum_relevance=1
        ),
        "pooled_Recall@5_rel1plus": pooled_recall_at_k(
            ranked, judged, 5, minimum_relevance=1
        ),
        "pooled_Recall@10_rel1plus": pooled_recall_at_k(
            ranked, judged, 10, minimum_relevance=1
        ),
        "pooled_Recall@20_rel1plus": pooled_recall_at_k(
            ranked, judged, 20, minimum_relevance=1
        ),
        "Precision@5_rel1plus": precision_at_k(
            ranked, judged, 5, minimum_relevance=1
        ),
        "HitRate@5_rel1plus": hit_rate_at_k(ranked, judged, 5, minimum_relevance=1),
        "Judged@5": judged_at_k(ranked, judged, 5),
        "Judged@10": judged_at_k(ranked, judged, 10),
        "Judged@20": judged_at_k(ranked, judged, 20),
        "Judged@30": judged_at_k(ranked, judged, 30),
    }


def aggregate_per_query(
    per_query: Mapping[str, Mapping[str, float]],
    metric_names: Sequence[str] = REQUIRED_METRIC_NAMES,
) -> dict[str, float | None]:
    """Macro-average per-query metric contributions."""

    if not per_query:
        return {metric: None for metric in metric_names}
    aggregate: dict[str, float | None] = {}
    for metric in metric_names:
        values: list[float] = []
        for query_id, metrics in per_query.items():
            if metric not in metrics:
                raise KeyError(f"Query {query_id!r} has no metric {metric!r}")
            value = float(metrics[metric])
            if not math.isfinite(value):
                raise ValueError(f"Metric {metric!r} is not finite for query {query_id!r}")
            values.append(value)
        aggregate[metric] = math.fsum(values) / len(values)
    return aggregate


def evaluate_run(
    run: Mapping[str, Iterable[Any]] | Iterable[Any],
    qrels: Mapping[Any, Any] | Iterable[Any],
    query_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Evaluate a run and retain both macro and per-query values.

    By default the query universe is the union of run and qrel query IDs so a
    missing run query cannot disappear from the macro average.  Callers may
    pass an explicit query list (for example, for a slice).
    """

    normalised_run = normalise_run(run)
    normalised_qrels = normalise_qrels(qrels)
    if query_ids is None:
        selected = sorted(set(normalised_run) | set(normalised_qrels))
    else:
        selected = list(query_ids)
        if any(not isinstance(query_id, str) or not query_id for query_id in selected):
            raise ValueError("Query identifiers must be non-empty strings")
        if len(selected) != len(set(selected)):
            raise ValueError("query_ids cannot contain duplicates")

    per_query = {
        query_id: evaluate_query(
            normalised_run.get(query_id, ()), normalised_qrels.get(query_id, {})
        )
        for query_id in selected
    }
    return {
        "evaluation_label": EVALUATION_LABEL,
        "query_count": len(selected),
        "overall": aggregate_per_query(per_query),
        "per_query": per_query,
    }


def compute_per_query_metrics(
    run: Mapping[str, Iterable[Any]] | Iterable[Any],
    qrels: Mapping[Any, Any] | Iterable[Any],
    query_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Convenience wrapper returning only the per-query table."""

    return evaluate_run(run, qrels, query_ids)["per_query"]


def compute_metrics(
    run: Mapping[str, Iterable[Any]] | Iterable[Any],
    qrels: Mapping[Any, Any] | Iterable[Any],
    query_ids: Iterable[str] | None = None,
) -> dict[str, float | None]:
    """Convenience wrapper returning only macro-averaged metrics."""

    return evaluate_run(run, qrels, query_ids)["overall"]


# American-English aliases for callers that use ``normalize`` terminology.
normalize_ranking = normalise_ranking
normalize_judgments = normalise_judgments
normalize_qrels = normalise_qrels
normalize_run = normalise_run


__all__ = [
    "ALLOWED_RELEVANCE_GRADES",
    "EVALUATION_LABEL",
    "PRIMARY_BOOTSTRAP_METRICS",
    "REQUIRED_METRIC_NAMES",
    "aggregate_per_query",
    "compute_metrics",
    "compute_per_query_metrics",
    "dcg_at_k",
    "evaluate_query",
    "evaluate_run",
    "graded_ndcg_at_k",
    "hit_rate_at_k",
    "judged_at_k",
    "judged_coverage_at_k",
    "mrr_at_k",
    "ndcg_at_k",
    "normalise_judgments",
    "normalise_qrels",
    "normalise_ranking",
    "normalise_run",
    "normalize_judgments",
    "normalize_qrels",
    "normalize_ranking",
    "normalize_run",
    "pooled_recall_at_k",
    "precision_at_k",
    "ranked_relevances",
    "recall_at_k",
    "reciprocal_rank_at_k",
]
