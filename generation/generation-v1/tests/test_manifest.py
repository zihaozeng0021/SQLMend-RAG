from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlmend_generation_v1.audit import write_json
from sqlmend_generation_v1.manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    release_source_snapshot,
    verify_manifest,
    write_manifest,
)


def _write(path: Path, text: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _fixture(tmp_path: Path) -> SimpleNamespace:
    release = tmp_path / "generation" / "generation-v1"
    for relative in (
        "config/generation.yaml",
        "schema/answer.schema.json",
        "src/sqlmend_generation_v1/module.py",
        "tests/test_module.py",
        "pyproject.toml",
        "requirements.txt",
        "README.md",
        "prepared_inputs/online_queries.jsonl",
        "prepared_inputs/g1_evidence_top5.jsonl",
        "runs/g0_closed_book_dev250.jsonl",
        "runs/g1_retrieval_v1_rag_dev250.jsonl",
        "evaluation/generation_seal.json",
        "evaluation/overall_metrics.json",
        "reports/generation_v1_report.md",
        "reports/test_results.json",
    ):
        _write(release / relative)
    safe = tmp_path / "retrieval/retrieval-v1/serialized_queries/dev_250_queries.jsonl"
    final_run = (
        tmp_path
        / "retrieval/retrieval-v1/runs/"
        "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec"
    )
    corpus = tmp_path / "construction/data/processed/corpus.jsonl"
    _write(safe)
    _write(final_run)
    _write(corpus)
    return SimpleNamespace(
        root=tmp_path,
        release=release,
        prepared_inputs=release / "prepared_inputs",
        runs=release / "runs",
        evaluation=release / "evaluation",
        reports=release / "reports",
        frozen_serialized_queries=safe,
        final_retrieval_run=final_run,
        corpus=corpus,
    )


def test_source_snapshot_is_deterministic_and_excludes_release_cache_tmp(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    first = release_source_snapshot(paths)
    _write(paths.release / "src/sqlmend_generation_v1/__pycache__/module.pyc")
    _write(paths.release / "tests/.pytest_cache/state")
    _write(paths.release / "config/ignored.yaml.tmp")
    _write(paths.release / "evaluation/.overall_metrics.json.tmp-1234")

    second = release_source_snapshot(paths)

    assert second == first
    assert first["file_count"] == 7
    assert all("__pycache__" not in path for path in first["files"])
    assert all(not path.endswith(".tmp") for path in first["files"])
    assert all(".tmp-" not in path for path in first["files"])


def test_manifest_is_fixed_point_with_self_validation_and_cache_present(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    first = build_manifest(paths)
    assert first["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert set(first["artifact_groups"]) == {
        "frozen_inputs",
        "source_and_tests",
        "prepared_inputs",
        "runs",
        "evaluation",
        "reports",
    }
    write_json(paths.release / "manifest.json", first)
    write_json(paths.reports / "validation_report.json", {"status": "forged PASS"})
    _write(paths.reports / "scratch.tmp")
    _write(paths.reports / ".generation_v1_report.md.tmp-1234")
    _write(paths.release / "evaluation/cache/ignored.bin")

    second = build_manifest(paths)

    assert second == first
    all_paths = {
        relative
        for group in second["artifact_groups"].values()
        for relative in group["files"]
    }
    assert "generation/generation-v1/manifest.json" not in all_paths
    assert not any("validation_report.json" in path for path in all_paths)
    assert not any("/cache/" in path for path in all_paths)
    assert not any(path.endswith(".tmp") for path in all_paths)
    assert not any(".tmp-" in path for path in all_paths)


def test_manifest_per_file_hash_and_payload_hash_reject_tampering(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    manifest = write_manifest(paths)
    assert verify_manifest(paths)["status"] == "PASS"

    target = paths.release / "evaluation/overall_metrics.json"
    target.write_text("tampered\n", encoding="utf-8")
    tampered = verify_manifest(paths)
    assert tampered["status"] == "FAIL"
    assert any("artifact hash differs" in error for error in tampered["errors"])

    target.write_text("fixture\n", encoding="utf-8")
    payload = json.loads((paths.release / "manifest.json").read_text(encoding="utf-8"))
    payload["formal_answer_count"] = 499
    forged = verify_manifest(paths, payload)
    assert forged["status"] == "FAIL"
    assert "manifest payload hash differs" in forged["errors"]
