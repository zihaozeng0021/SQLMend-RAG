"""Deterministic parsing, validation, and writing of TREC run files.

The project uses the standard six-column run form::

    query_id Q0 chunk_id rank score run_tag

Official output is canonicalized by query ID and rank, uses a single stable run
tag, and writes every finite score with exactly twelve digits after the decimal
point.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SCORE_DECIMAL_PLACES = 12
_CANONICAL_SCORE_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{12}$")


class TrecRunError(ValueError):
    """Raised when a TREC run is malformed or violates run invariants."""


@dataclass(frozen=True, slots=True)
class TrecRunEntry:
    """One standard TREC run row."""

    query_id: str
    chunk_id: str
    rank: int
    score: float
    run_tag: str


# A short alias is convenient for retriever implementations.
RunEntry = TrecRunEntry


def _nonempty_token(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise TrecRunError(f"{field} must be a non-empty whitespace-free token")
    if any(character.isspace() for character in value):
        raise TrecRunError(f"{field} must not contain whitespace: {value!r}")
    return value


def _finite_score(value: object) -> float:
    if isinstance(value, bool):
        raise TrecRunError("score must be a finite real number, not bool")
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrecRunError(f"score is not numeric: {value!r}") from exc
    if not math.isfinite(score):
        raise TrecRunError(f"score must be finite: {value!r}")
    return score


def format_trec_score(value: object) -> str:
    """Return the canonical finite twelve-decimal score representation."""

    score = _finite_score(value)
    # Do not serialize negative zero: it is numerically indistinguishable from
    # zero but would otherwise produce different bytes.
    if score == 0.0:
        score = 0.0
    return f"{score:.{SCORE_DECIMAL_PLACES}f}"


def _coerce_entry(value: TrecRunEntry | Mapping[str, Any] | object) -> TrecRunEntry:
    if isinstance(value, TrecRunEntry):
        entry = value
    elif isinstance(value, Mapping):
        try:
            entry = TrecRunEntry(
                query_id=value["query_id"],
                chunk_id=value["chunk_id"],
                rank=value["rank"],
                score=value["score"],
                run_tag=value["run_tag"],
            )
        except KeyError as exc:
            raise TrecRunError(f"run entry is missing field {exc.args[0]!r}") from exc
    else:
        try:
            entry = TrecRunEntry(
                query_id=getattr(value, "query_id"),
                chunk_id=getattr(value, "chunk_id"),
                rank=getattr(value, "rank"),
                score=getattr(value, "score"),
                run_tag=getattr(value, "run_tag"),
            )
        except AttributeError as exc:
            raise TrecRunError(f"unsupported run entry: {value!r}") from exc

    query_id = _nonempty_token(entry.query_id, "query_id")
    chunk_id = _nonempty_token(entry.chunk_id, "chunk_id")
    run_tag = _nonempty_token(entry.run_tag, "run_tag")
    if isinstance(entry.rank, bool) or not isinstance(entry.rank, int) or entry.rank < 1:
        raise TrecRunError(f"rank must be a positive integer: {entry.rank!r}")
    return TrecRunEntry(query_id, chunk_id, entry.rank, _finite_score(entry.score), run_tag)


def validate_trec_run(
    entries: Iterable[TrecRunEntry | Mapping[str, Any] | object],
    *,
    known_chunk_ids: Iterable[str] | None = None,
    min_results_per_query: int | None = None,
    exact_results_per_query: int | None = None,
    expected_run_tag: str | None = None,
    require_nonempty: bool = True,
) -> list[TrecRunEntry]:
    """Validate run invariants and return normalized entries.

    ``known_chunk_ids`` enables the frozen-corpus membership check without
    coupling run I/O to corpus loading.  The result-count constraints are
    optional so small fixtures and full official runs share the same validator.
    """

    if min_results_per_query is not None and min_results_per_query < 1:
        raise ValueError("min_results_per_query must be positive")
    if exact_results_per_query is not None and exact_results_per_query < 1:
        raise ValueError("exact_results_per_query must be positive")
    if min_results_per_query is not None and exact_results_per_query is not None:
        raise ValueError("choose either min_results_per_query or exact_results_per_query")

    normalized = [_coerce_entry(entry) for entry in entries]
    if require_nonempty and not normalized:
        raise TrecRunError("TREC run is empty")

    known = None if known_chunk_ids is None else frozenset(known_chunk_ids)
    by_query: dict[str, list[TrecRunEntry]] = defaultdict(list)
    seen_pairs: set[tuple[str, str]] = set()
    tags: set[str] = set()

    for entry in normalized:
        pair = (entry.query_id, entry.chunk_id)
        if pair in seen_pairs:
            raise TrecRunError(
                f"duplicate query/chunk run entry: {entry.query_id!r}, {entry.chunk_id!r}"
            )
        seen_pairs.add(pair)
        if known is not None and entry.chunk_id not in known:
            raise TrecRunError(f"unknown chunk_id in run: {entry.chunk_id!r}")
        by_query[entry.query_id].append(entry)
        tags.add(entry.run_tag)

    if len(tags) > 1:
        raise TrecRunError(f"run must use one stable run_tag; observed {sorted(tags)!r}")
    if expected_run_tag is not None:
        _nonempty_token(expected_run_tag, "expected_run_tag")
        if tags and tags != {expected_run_tag}:
            raise TrecRunError(
                f"unexpected run_tag: observed {next(iter(tags))!r}, expected {expected_run_tag!r}"
            )

    for query_id, query_entries in by_query.items():
        ranks = [entry.rank for entry in query_entries]
        if len(set(ranks)) != len(ranks):
            raise TrecRunError(f"duplicate rank for query {query_id!r}: {sorted(ranks)!r}")
        expected_ranks = list(range(1, len(query_entries) + 1))
        if sorted(ranks) != expected_ranks:
            raise TrecRunError(
                f"ranks for query {query_id!r} must be continuous from 1; "
                f"observed {sorted(ranks)!r}"
            )
        if min_results_per_query is not None and len(query_entries) < min_results_per_query:
            raise TrecRunError(
                f"query {query_id!r} has {len(query_entries)} results; "
                f"at least {min_results_per_query} required"
            )
        if exact_results_per_query is not None and len(query_entries) != exact_results_per_query:
            raise TrecRunError(
                f"query {query_id!r} has {len(query_entries)} results; "
                f"exactly {exact_results_per_query} required"
            )

    return normalized


def parse_trec_run(
    text: str,
    *,
    known_chunk_ids: Iterable[str] | None = None,
    min_results_per_query: int | None = None,
    exact_results_per_query: int | None = None,
    expected_run_tag: str | None = None,
    require_canonical_scores: bool = True,
) -> list[TrecRunEntry]:
    """Parse and validate standard six-column TREC run text."""

    if not isinstance(text, str):
        raise TypeError("text must be str")
    entries: list[TrecRunEntry] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise TrecRunError(f"blank line at run line {line_number}")
        fields = line.split()
        if len(fields) != 6:
            raise TrecRunError(
                f"run line {line_number} must have 6 columns; observed {len(fields)}"
            )
        query_id, iteration, chunk_id, rank_text, score_text, run_tag = fields
        if iteration != "Q0":
            raise TrecRunError(
                f"run line {line_number} column 2 must be 'Q0'; observed {iteration!r}"
            )
        if require_canonical_scores and not _CANONICAL_SCORE_RE.fullmatch(score_text):
            raise TrecRunError(
                f"run line {line_number} score must use fixed 12-decimal format: {score_text!r}"
            )
        try:
            rank = int(rank_text, 10)
        except ValueError as exc:
            raise TrecRunError(f"invalid rank at run line {line_number}: {rank_text!r}") from exc
        try:
            entry = TrecRunEntry(query_id, chunk_id, rank, float(score_text), run_tag)
            entries.append(_coerce_entry(entry))
        except TrecRunError as exc:
            raise TrecRunError(f"invalid run line {line_number}: {exc}") from exc

    return validate_trec_run(
        entries,
        known_chunk_ids=known_chunk_ids,
        min_results_per_query=min_results_per_query,
        exact_results_per_query=exact_results_per_query,
        expected_run_tag=expected_run_tag,
    )


def read_trec_run(
    path: str | Path,
    *,
    known_chunk_ids: Iterable[str] | None = None,
    min_results_per_query: int | None = None,
    exact_results_per_query: int | None = None,
    expected_run_tag: str | None = None,
    require_canonical_scores: bool = True,
) -> list[TrecRunEntry]:
    """Read a UTF-8 TREC run file and validate it."""

    run_path = Path(path)
    try:
        text = run_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TrecRunError(f"run is not valid UTF-8: {run_path}") from exc
    return parse_trec_run(
        text,
        known_chunk_ids=known_chunk_ids,
        min_results_per_query=min_results_per_query,
        exact_results_per_query=exact_results_per_query,
        expected_run_tag=expected_run_tag,
        require_canonical_scores=require_canonical_scores,
    )


def format_trec_run(
    entries: Iterable[TrecRunEntry | Mapping[str, Any] | object],
    *,
    known_chunk_ids: Iterable[str] | None = None,
    min_results_per_query: int | None = None,
    exact_results_per_query: int | None = None,
    expected_run_tag: str | None = None,
) -> str:
    """Validate and serialize a run in deterministic byte order."""

    normalized = validate_trec_run(
        entries,
        known_chunk_ids=known_chunk_ids,
        min_results_per_query=min_results_per_query,
        exact_results_per_query=exact_results_per_query,
        expected_run_tag=expected_run_tag,
    )
    ordered = sorted(normalized, key=lambda entry: (entry.query_id, entry.rank, entry.chunk_id))
    return "".join(
        f"{entry.query_id} Q0 {entry.chunk_id} {entry.rank} "
        f"{format_trec_score(entry.score)} {entry.run_tag}\n"
        for entry in ordered
    )


def write_trec_run(
    path: str | Path,
    entries: Iterable[TrecRunEntry | Mapping[str, Any] | object],
    *,
    known_chunk_ids: Iterable[str] | None = None,
    min_results_per_query: int | None = None,
    exact_results_per_query: int | None = None,
    expected_run_tag: str | None = None,
) -> Path:
    """Write a canonical UTF-8/LF TREC run and return its path."""

    output = format_trec_run(
        entries,
        known_chunk_ids=known_chunk_ids,
        min_results_per_query=min_results_per_query,
        exact_results_per_query=exact_results_per_query,
        expected_run_tag=expected_run_tag,
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(output)
    return output_path


# Explicit verb aliases keep call sites readable without changing semantics.
load_trec_run = read_trec_run
dump_trec_run = write_trec_run
