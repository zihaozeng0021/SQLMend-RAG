"""Small immutable contracts shared by online ranking and offline evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OnlineQuery:
    query_id: str
    dialect: str | None
    version: str | None
    serialized_text: str
    user_problem: str | None = None
    sql: str | None = None
    error_message: str | None = None
    error_code: str | None = None
    sqlstate: str | None = None
    error_symbol: str | None = None


@dataclass(frozen=True, slots=True)
class CandidatePassage:
    chunk_id: str
    dialect: str | None
    version: str | None
    version_min: str | None
    version_max: str | None
    version_status: str
    source_type: str | None
    title: str | None
    section: str | None
    text: str
    baseline_rank: int
    baseline_score: float


@dataclass(frozen=True, slots=True)
class RunEntry:
    query_id: str
    chunk_id: str
    rank: int
    score: float
    run_tag: str
