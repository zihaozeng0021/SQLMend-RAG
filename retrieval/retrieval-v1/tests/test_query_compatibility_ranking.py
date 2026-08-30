from __future__ import annotations

from dataclasses import fields
import json
import math
from pathlib import Path
import sys

import pytest


RELEASE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RELEASE_ROOT / "src"))

from sqlmend_retrieval_v1.compatibility import (
    TargetVersion,
    Version,
    dialect_compatibility,
    is_wrong_dialect,
    parse_target_version,
    parse_version,
    version_compatibility,
)
from sqlmend_retrieval_v1.io import (
    group_run,
    load_jsonl,
    read_trec_run,
    sha256_file,
)
from sqlmend_retrieval_v1.models import CandidatePassage, OnlineQuery, RunEntry
from sqlmend_retrieval_v1.paths import ProjectPaths
import sqlmend_retrieval_v1.pipeline as pipeline_module
from sqlmend_retrieval_v1.query import (
    ALLOWED_SOURCE_FIELDS,
    FORBIDDEN_ONLINE_FIELDS,
    project_online_queries,
    serialize_query,
    write_serialized_queries,
)
from sqlmend_retrieval_v1.ranking import (
    CandidateState,
    candidate_pair_set,
    rank_metadata_aware,
    reconstruct_rrf_candidates,
    verify_frozen_hybrid_reconstruction,
)


FROZEN_SERIALIZED_SHA256 = (
    "e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c"
)


def _passage(
    chunk_id: str = "doc",
    *,
    dialect: str | None = "postgresql",
    version: str | None = "14.24",
    version_min: str | None = "14.24",
    version_max: str | None = "14.24",
    version_status: str = "exact",
    source_type: str | None = "official_docs",
    title: str | None = "SQL documentation",
    section: str | None = "Syntax",
    text: str = "General SQL diagnostic guidance.",
    baseline_rank: int = 1,
    baseline_score: float = 0.02,
) -> CandidatePassage:
    return CandidatePassage(
        chunk_id=chunk_id,
        dialect=dialect,
        version=version,
        version_min=version_min,
        version_max=version_max,
        version_status=version_status,
        source_type=source_type,
        title=title,
        section=section,
        text=text,
        baseline_rank=baseline_rank,
        baseline_score=baseline_score,
    )


def _state(
    chunk_id: str,
    *,
    dialect: str | None = "postgresql",
    baseline_rank: int = 1,
    baseline_score: float = 0.02,
) -> CandidateState:
    return CandidateState(
        passage=_passage(
            chunk_id,
            dialect=dialect,
            version=None,
            version_min=None,
            version_max=None,
            version_status="unknown",
            baseline_rank=baseline_rank,
            baseline_score=baseline_score,
        ),
        bm25_rank=baseline_rank,
        dense_rank=None,
    )


def _component_rows(query_id: str, chunk_ids: list[str]) -> list[RunEntry]:
    return [
        RunEntry(query_id, chunk_id, rank, 1.0 / rank, "synthetic_component")
        for rank, chunk_id in enumerate(chunk_ids, start=1)
    ]


def _corpus_record(chunk_id: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "dialect": "postgresql",
        "version": "14.24",
        "version_min": "14.24",
        "version_max": "14.24",
        "version_status": "exact",
        "source_type": "official_docs",
        "title": f"Title {chunk_id}",
        "section": "Syntax",
        "text": f"Documentation for {chunk_id}.",
    }


def test_serializer_rebuild_is_byte_identical_to_frozen_baseline(tmp_path: Path) -> None:
    source = REPO_ROOT / "annotation" / "codex" / "dev_250.jsonl"
    frozen = (
        REPO_ROOT
        / "retrieval"
        / "baseline"
        / "serialized_queries"
        / "dev_250_queries.jsonl"
    )
    rebuilt = tmp_path / "dev_250_queries.jsonl"

    write_serialized_queries(load_jsonl(source), rebuilt)

    assert rebuilt.read_bytes() == frozen.read_bytes()
    assert sha256_file(rebuilt) == FROZEN_SERIALIZED_SHA256
    assert sha256_file(frozen) == FROZEN_SERIALIZED_SHA256
    baseline_lock = json.loads(
        (RELEASE_ROOT / "config" / "baseline_lock.json").read_text(encoding="utf-8")
    )
    assert (
        baseline_lock["critical_files"]
        ["retrieval/baseline/serialized_queries/dev_250_queries.jsonl"]
        == FROZEN_SERIALIZED_SHA256
    )
    assert (
        RELEASE_ROOT.joinpath("config", "query_serializer.yaml").read_bytes()
        == REPO_ROOT.joinpath(
            "retrieval", "baseline", "config", "query_serializer.yaml"
        ).read_bytes()
    )


def test_online_query_contract_contains_only_safe_fields_and_never_serializes_labels() -> None:
    record: dict[str, object] = {
        "query_id": "Q_SAFE",
        "dialect": "postgresql",
        "version": "14.24",
        "user_problem": "Why is the relation missing?",
        "sql": "SELECT * FROM missing_relation;",
        "error_message": "relation does not exist",
        "error_code": "42P01",
        "sqlstate": "42P01",
        "error_symbol": "undefined_table",
    }
    forbidden_values: dict[str, str] = {}
    for name in sorted(FORBIDDEN_ONLINE_FIELDS):
        sentinel = f"FORBIDDEN_{name.upper()}_VALUE"
        record[name] = sentinel
        forbidden_values[name] = sentinel

    serialized = serialize_query(record)
    online = project_online_queries([record])[0]
    model_fields = {field.name for field in fields(OnlineQuery)}
    expected_model_fields = {
        "query_id",
        "dialect",
        "version",
        "serialized_text",
        "user_problem",
        "sql",
        "error_message",
        "error_code",
        "sqlstate",
        "error_symbol",
    }

    assert model_fields == expected_model_fields
    assert model_fields.isdisjoint(FORBIDDEN_ONLINE_FIELDS)
    assert set(serialized.source_fields_used).issubset(ALLOWED_SOURCE_FIELDS)
    assert online.serialized_text == serialized.serialized_text
    assert not hasattr(online, "case_flags")
    assert not hasattr(online, "evidence")
    for sentinel in forbidden_values.values():
        assert sentinel not in serialized.serialized_text
        assert sentinel not in online.serialized_text


def test_online_run_input_loader_does_not_read_qrels_or_retain_offline_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ProjectPaths.discover(REPO_ROOT)
    accessed: set[Path] = set()
    original_hash = pipeline_module.sha256_file
    original_jsonl = pipeline_module.load_jsonl

    def audited_hash(path: Path) -> str:
        accessed.add(Path(path).resolve())
        return original_hash(path)

    def audited_jsonl(path: Path) -> list[dict[str, object]]:
        accessed.add(Path(path).resolve())
        return original_jsonl(path)

    monkeypatch.setattr(pipeline_module, "sha256_file", audited_hash)
    monkeypatch.setattr(pipeline_module, "load_jsonl", audited_jsonl)

    inputs = pipeline_module.verify_and_load_online_inputs(
        paths, write_serialized=False
    )

    assert paths.qrels.resolve() not in accessed
    assert not hasattr(inputs, "raw_queries")


@pytest.mark.parametrize(
    ("query_dialect", "document_dialect", "category", "wrong"),
    [
        (" PostgreSQL ", "postgresql", "compatible", False),
        ("mysql", "mariadb", "related", True),
        ("mariadb", "mysql", "related", True),
        ("postgresql", "sqlite", "incompatible", True),
        (None, "postgresql", "unknown", False),
        ("oracle", "postgresql", "unknown", False),
        ("postgresql", None, "unknown", False),
    ],
)
def test_dialect_categories_and_wrong_dialect_metric_contract(
    query_dialect: str | None,
    document_dialect: str | None,
    category: str,
    wrong: bool,
) -> None:
    assert dialect_compatibility(query_dialect, document_dialect) == category
    # Related MySQL/MariaDB evidence receives a softer rank treatment, but it is
    # still an explicit metadata mismatch for Wrong-Dialect@5.
    assert is_wrong_dialect(query_dialect, document_dialect) is wrong


def test_numeric_and_pre_version_parsing_is_numeric_not_lexical() -> None:
    assert parse_version("v10.10.2") == Version(10, 10, 2)
    assert parse_version("10.2") == Version(10, 2, 0)
    assert parse_version("current") is None
    assert parse_target_version("8.0.30") == TargetVersion(
        Version(8, 0, 30), Version(8, 0, 31), True
    )
    assert parse_target_version("pre-3.37.0") == TargetVersion(
        None, Version(3, 37, 0), False
    )

    query = OnlineQuery("Q_NUM", "mysql", "8.0.30", "safe query")
    within_range = _passage(
        dialect="mysql",
        version="8.0.46",
        version_min="8.0.0",
        version_max="8.0.46",
        version_status="range",
    )
    assert version_compatibility(query, within_range).category == "compatible"


def test_pre_current_and_unknown_versions_are_conservative() -> None:
    legacy_query = OnlineQuery(
        "Q_PRE", "sqlite", "pre-3.37.0", "safe query", sql="SELECT 1;"
    )
    introduced_later = _passage(
        dialect="sqlite",
        text="The STRICT table option was introduced in version 3.37.0.",
    )
    assert version_compatibility(legacy_query, introduced_later).category == "incompatible"

    current_query = OnlineQuery("Q_CURRENT", "mariadb", "current", "safe query")
    unbounded_current = _passage(
        dialect="mariadb",
        version="current",
        version_min=None,
        version_max=None,
        version_status="current",
    )
    assert version_compatibility(current_query, unbounded_current).category == "unknown"

    numeric_query = OnlineQuery("Q_OLD", "mariadb", "10.7", "safe query")
    assert version_compatibility(numeric_query, unbounded_current).category == "unknown"
    absent_query_version = OnlineQuery("Q_NONE", "sqlite", None, "safe query")
    assert version_compatibility(absent_query_version, introduced_later).category == "unknown"

    unknown_document = _passage(
        dialect="mysql",
        version=None,
        version_min=None,
        version_max=None,
        version_status="unknown",
    )
    assert version_compatibility(
        OnlineQuery("Q_UNKNOWN_DOC", "mysql", "8.0", "safe query"),
        unknown_document,
    ).category == "unknown"


def test_generic_snapshot_and_explicit_boundaries_are_distinguished() -> None:
    old_query = OnlineQuery(
        "Q_BOUNDARY", "postgresql", "14.24", "safe query", sql="SELECT 1;"
    )
    generic_newer_snapshot = _passage(
        dialect="postgresql",
        version="18.6",
        version_min="18.6",
        version_max="18.6",
        text="A transaction can be rolled back after an error.",
    )
    assert version_compatibility(old_query, generic_newer_snapshot).category == "general"

    introduced = _passage(
        dialect="postgresql",
        text="This window-frame option was introduced in version 15.0.",
    )
    assert version_compatibility(old_query, introduced).category == "incompatible"
    assert version_compatibility(
        OnlineQuery("Q_NEW", "postgresql", "15.0", "safe query", sql="SELECT 1;"),
        introduced,
    ).category == "compatible"

    removed = _passage(
        dialect="postgresql",
        text="This legacy option was removed in version 15.0.",
    )
    assert version_compatibility(old_query, removed).category == "compatible"
    assert version_compatibility(
        OnlineQuery("Q_REMOVED", "postgresql", "15.0", "safe query", sql="SELECT 1;"),
        removed,
    ).category == "incompatible"


def test_excluding_boundary_can_be_directly_diagnostic_without_label_access() -> None:
    query = OnlineQuery(
        "Q_DIAGNOSTIC",
        "mariadb",
        "10.7",
        "safe query",
        sql="SELECT json_overlaps(left_doc, right_doc) FROM payloads;",
    )
    direct_evidence = _passage(
        dialect="mariadb",
        version=None,
        version_min=None,
        version_max=None,
        version_status="unknown",
        text="JSON_OVERLAPS(left_doc, right_doc) was introduced in version 10.8.",
    )

    decision = version_compatibility(query, direct_evidence)

    assert decision.category == "compatible"
    assert "directly diagnoses" in decision.reason
    assert decision.explicit_bounds


def test_percentages_and_bare_from_are_not_version_boundary_false_positives() -> None:
    query = OnlineQuery(
        "Q_FALSE_POSITIVE", "mysql", "8.0", "safe query", sql="SELECT 1;"
    )
    passage = _passage(
        dialect="mysql",
        version="9.0",
        version_min="9.0",
        version_max="9.0",
        text="The benchmark read 500% more rows from 9.0 sample files.",
    )

    decision = version_compatibility(query, passage)

    assert decision.category == "general"
    assert decision.explicit_bounds == ()


def test_cross_dialect_version_namespace_is_not_applicable() -> None:
    query = OnlineQuery(
        "Q_NAMESPACE", "postgresql", "14", "safe query", sql="SELECT 1;"
    )
    mysql_passage = _passage(
        dialect="mysql",
        text="This feature was introduced in version 15.0.",
    )

    decision = version_compatibility(query, mysql_passage)

    assert decision.category == "not_applicable"
    assert "namespaces differ" in decision.reason


@pytest.mark.parametrize(
    ("overlap", "expected_union_size"),
    [(15, 45), (7, 53), (0, 60)],
)
def test_synthetic_rrf_union_is_complete_and_deterministic(
    overlap: int, expected_union_size: int
) -> None:
    bm25_ids = [f"doc_{index:02d}" for index in range(30)]
    shared = bm25_ids[30 - overlap :] if overlap else []
    dense_ids = shared + [
        f"doc_{index:02d}" for index in range(30, 60 - overlap)
    ]
    corpus_ids = sorted(set(bm25_ids).union(dense_ids))
    corpus = {chunk_id: _corpus_record(chunk_id) for chunk_id in corpus_ids}
    bm25 = _component_rows("Q_RRF", bm25_ids)
    dense = _component_rows("Q_RRF", dense_ids)

    first = reconstruct_rrf_candidates(bm25, dense, corpus)
    second = reconstruct_rrf_candidates(
        reversed(bm25), reversed(dense), dict(reversed(list(corpus.items())))
    )

    assert first == second
    assert len(first["Q_RRF"]) == expected_union_size
    assert [state.passage.baseline_rank for state in first["Q_RRF"]] == list(
        range(1, expected_union_size + 1)
    )
    assert candidate_pair_set(first) == {
        ("Q_RRF", chunk_id) for chunk_id in corpus_ids
    }
    if shared:
        common = next(
            state for state in first["Q_RRF"] if state.passage.chunk_id == shared[0]
        )
        assert common.bm25_rank == 31 - overlap
        assert common.dense_rank == 1
        assert common.passage.baseline_score == pytest.approx(
            math.fsum((1.0 / (60 + 31 - overlap), 1.0 / 61))
        )


def test_repo_rrf_union_reconstructs_every_frozen_hybrid_top30_exactly() -> None:
    baseline = REPO_ROOT / "retrieval" / "baseline" / "runs"
    corpus = {
        record["chunk_id"]: record
        for record in load_jsonl(
            REPO_ROOT / "construction" / "data" / "processed" / "corpus.jsonl"
        )
    }
    bm25 = read_trec_run(baseline / "bm25_formal_dev250.trec")
    dense = read_trec_run(baseline / "dense_formal_dev250.trec")
    frozen = read_trec_run(baseline / "hybrid_rrf_formal_dev250.trec")

    candidates = reconstruct_rrf_candidates(bm25, dense, corpus)
    verify_frozen_hybrid_reconstruction(candidates, frozen)
    frozen_by_query = group_run(frozen)

    assert len(candidates) == 250
    assert min(map(len, candidates.values())) == 45
    assert max(map(len, candidates.values())) == 60
    for query_id, states in candidates.items():
        expected = frozen_by_query[query_id]
        assert [state.passage.chunk_id for state in states[:30]] == [
            row.chunk_id for row in expected
        ]
        for state, row in zip(states[:30], expected, strict=True):
            assert state.passage.baseline_score == pytest.approx(
                row.score, rel=0.0, abs=5e-13
            )


def test_metadata_bonus_is_soft_and_never_filters_cross_dialect_candidates() -> None:
    query = OnlineQuery("Q_SOFT", "mysql", None, "safe query")
    states = [
        _state("wrong", dialect="sqlite", baseline_rank=1, baseline_score=0.030),
        _state("related", dialect="mariadb", baseline_rank=2, baseline_score=0.029),
        _state("same", dialect="mysql", baseline_rank=3, baseline_score=0.028),
    ]
    config = {
        "run_tag": "soft_bonus_test",
        "output_depth": 3,
        "dialect_bonuses": {
            "compatible": 0.010,
            "related": 0.005,
            "unknown": 0.002,
            "incompatible": 0.0,
        },
    }

    run, provenance, all_scored = rank_metadata_aware(
        {"Q_SOFT": states}, {"Q_SOFT": query}, config
    )

    assert [row.chunk_id for row in run] == ["same", "related", "wrong"]
    assert {row.chunk_id for row in run} == {state.passage.chunk_id for state in states}
    assert {row["chunk_id"] for row in provenance} == {"same", "related", "wrong"}
    assert {row["chunk_id"] for row in all_scored["Q_SOFT"]} == {
        "same",
        "related",
        "wrong",
    }
    wrong = next(row for row in all_scored["Q_SOFT"] if row["chunk_id"] == "wrong")
    assert wrong["dialect_category"] == "incompatible"
    assert wrong["adjusted_score"] == wrong["baseline_rrf_score"]


def test_metadata_ranking_is_order_independent_and_uses_documented_ties() -> None:
    query = OnlineQuery("Q_TIE", "postgresql", None, "safe query")
    states = [
        _state("z", baseline_rank=1, baseline_score=0.02),
        _state("a", baseline_rank=1, baseline_score=0.02),
        _state("00", baseline_rank=2, baseline_score=0.02),
    ]
    config = {
        "run_tag": "tie_test",
        "output_depth": 3,
        "dialect_bonuses": {"compatible": 0.004},
    }

    first = rank_metadata_aware({"Q_TIE": states}, {"Q_TIE": query}, config)
    second = rank_metadata_aware(
        {"Q_TIE": list(reversed(states))}, {"Q_TIE": query}, config
    )

    assert first == second
    assert [row.chunk_id for row in first[0]] == ["a", "z", "00"]
