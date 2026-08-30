from __future__ import annotations

import json
from pathlib import Path

import pytest

from sqlmend_retrieval_v1.io import sha256_file
from sqlmend_retrieval_v1.manifest import (
    EVALUATION_LABEL,
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    release_source_snapshot,
)
from sqlmend_retrieval_v1.paths import ProjectPaths
from sqlmend_retrieval_v1.pipeline import RUN_FILES, SYSTEM_CONFIG_FILES
from sqlmend_retrieval_v1.pool import FORMAL_SYSTEM_IDS
from sqlmend_retrieval_v1.validation import (
    PASS,
    ReleaseValidationError,
    _all_passed_flags,
    _validate_latency,
    _validate_manifest,
    _validate_manifest_hashes,
    _validate_test_evidence,
    validate_release,
)


def _write(path: Path, value: str = "fixture\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    return path


def _write_json(path: Path, value: object) -> Path:
    return _write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _make_paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(tmp_path)
    for directory in (
        tmp_path / "construction",
        tmp_path / "annotation" / "codex",
        tmp_path / "retrieval" / "baseline",
        paths.config,
        paths.system_configs,
        paths.runs,
        paths.evaluation,
        paths.reports,
        paths.pool_expansion,
        paths.release / "serialized_queries",
        paths.release / "src" / "sqlmend_retrieval_v1",
        paths.release / "tests",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def _make_source_tree(paths: ProjectPaths) -> None:
    _write(paths.config / "evaluation.yaml", "phase7: {}\n")
    _write(paths.release / "src" / "sqlmend_retrieval_v1" / "module.py", "VALUE = 1\n")
    _write(paths.release / "tests" / "test_module.py", "def test_value():\n    assert True\n")
    _write(paths.release / "pyproject.toml", "[project]\nname = 'fixture'\n")
    _write(paths.release / "requirements.txt", "PyYAML==6.0.1\n")


def _make_manifest_fixture(tmp_path: Path) -> ProjectPaths:
    paths = _make_paths(tmp_path)
    _make_source_tree(paths)

    _write(paths.corpus, '{"chunk_id":"d1"}\n')
    _write(paths.queries, '{"query_id":"q1"}\n')
    _write(paths.qrels, "q1 0 d1 2\n")
    _write(paths.baseline_bm25_run, "q1 Q0 d1 1 1.000000000000 bm25_formal_v1\n")
    _write(paths.baseline_dense_run, "q1 Q0 d1 1 1.000000000000 dense_formal_v1\n")
    _write(paths.baseline_run, "q1 Q0 d1 1 1.000000000000 hybrid_rrf_formal_v1\n")
    _write(paths.baseline_run.with_suffix(".provenance.jsonl"), '{"query_id":"q1","chunk_id":"d1"}\n')
    _write(paths.baseline_serialized_queries, '{"query_id":"q1"}\n')
    _write_json(paths.baseline / "evaluation" / "latency.json", {"fixture": True})
    frozen_hash = sha256_file(paths.baseline_run)
    _write_json(
        paths.baseline / "evaluation" / "run_determinism.json",
        {
            "hybrid": {
                "byte_identical": True,
                "first_sha256": frozen_hash,
                "second_sha256": frozen_hash,
            }
        },
    )
    _write_json(paths.config / "baseline_lock.json", {"fixture": True})
    _write(paths.serialized_queries, '{"query_id":"q1"}\n')

    determinism: dict[str, object] = {}
    for index, system_id in enumerate(FORMAL_SYSTEM_IDS):
        config_name = SYSTEM_CONFIG_FILES[system_id]
        run_tag = "hybrid_rrf_formal_v1" if index == 0 else f"tag_{index}"
        _write(
            paths.system_configs / config_name,
            f"system_id: {system_id}\nrun_tag: {run_tag}\n",
        )
        if index:
            run_path = paths.runs / RUN_FILES[system_id]
            _write(run_path, f"q1 Q0 d1 1 1.000000000000 {run_tag}\n")
            _write(run_path.with_suffix(".provenance.jsonl"), '{"query_id":"q1","chunk_id":"d1"}\n')
            run_hash = sha256_file(run_path)
            determinism[system_id] = {
                "byte_identical": True,
                "first_sha256": run_hash,
                "second_sha256": run_hash,
                "provenance_identical": True,
            }

    required_evaluation = (
        "acceptance.json",
        "comparison_results.json",
        "evaluation_status.json",
        "judged_coverage.json",
        "overall_metrics.json",
        "per_query_metrics.csv",
        "slice_metrics.csv",
    )
    for name in required_evaluation:
        _write(paths.evaluation / name)
    _write_json(
        paths.evaluation / "run_determinism.json",
        {"schema_version": "fixture", "systems": determinism},
    )
    for name in (
        "candidate_union.json",
        "protected_paths_before.json",
        "protected_paths_after.json",
        "latency.json",
        "test_results.json",
    ):
        _write(paths.reports / name)
    _write(paths.reports / "retrieval_v1_report.md", "# fixture report\n")
    _write(paths.pool_expansion / "pool_expansion_required.jsonl", "")
    _write_json(paths.pool_expansion / "pool_expansion_summary.json", {"fixture": True})
    _write_json(paths.release / "indices" / "reranker" / "metadata.json", {"fixture": True})
    return paths


def _summary(mean: float, p50: float, p95: float) -> dict[str, float]:
    return {"mean_ms": mean, "p50_ms": p50, "p95_ms": p95}


def _sum(*values: dict[str, float]) -> dict[str, float]:
    return {
        field: sum(value[field] for value in values)
        for field in ("mean_ms", "p50_ms", "p95_ms")
    }


def _latency_payload() -> dict[str, object]:
    baseline = _summary(10.0, 9.0, 15.0)
    stages = {
        "dialect_metadata_rerank": _summary(1.0, 0.8, 1.5),
        "version_metadata_rerank": _summary(1.2, 1.0, 1.8),
        "dialect_version_metadata_rerank": _summary(1.5, 1.2, 2.0),
        "lexical_reranker": _summary(2.0, 1.7, 2.8),
    }
    stage_ids = dict(zip(stages, FORMAL_SYSTEM_IDS[1:], strict=True))
    measured = {
        stage: {
            "system_id": stage_ids[stage],
            "latency_type": "measured_increment",
            "query_count": 250,
            "repetitions": 1,
            "sample_count": 250,
            **summary,
        }
        for stage, summary in stages.items()
    }
    systems: dict[str, object] = {
        FORMAL_SYSTEM_IDS[0]: {
            "system_id": FORMAL_SYSTEM_IDS[0],
            "total_latency_type": "frozen_measured_reference",
            "total_latency_ms": baseline,
        }
    }
    for system_id, stage in zip(FORMAL_SYSTEM_IDS[1:4], tuple(stages)[:3], strict=True):
        systems[system_id] = {
            "system_id": system_id,
            "total_latency_type": "estimate",
            "incremental_online_latency_ms": stages[stage],
            "total_latency_estimate_ms": _sum(baseline, stages[stage]),
        }
    final_increment = _sum(
        stages["dialect_version_metadata_rerank"], stages["lexical_reranker"]
    )
    systems[FORMAL_SYSTEM_IDS[4]] = {
        "system_id": FORMAL_SYSTEM_IDS[4],
        "total_latency_type": "estimate",
        "incremental_online_latency_ms": final_increment,
        "total_latency_estimate_ms": _sum(baseline, final_increment),
    }
    return {
        "schema_version": "sqlmend-retrieval-v1-latency-v1",
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "query_count": 250,
        "repetitions": 1,
        "each_query_measured_at_least_once": True,
        "frozen_hybrid_reference": {
            "system_id": FORMAL_SYSTEM_IDS[0],
            "latency_type": "frozen_measured_reference",
            "total_latency_ms": baseline,
        },
        "measured_incremental_online_latency": measured,
        "systems": systems,
    }


def test_release_source_snapshot_is_deterministic_and_excludes_cache_temp(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _make_source_tree(paths)
    first = release_source_snapshot(paths)

    _write(paths.release / "src" / "sqlmend_retrieval_v1" / "__pycache__" / "module.pyc")
    _write(paths.release / "tests" / ".pytest_cache" / "state")
    _write(paths.config / "ignored.yaml.tmp")
    second = release_source_snapshot(paths)

    assert first == second
    assert first["file_count"] == 5
    assert all("__pycache__" not in path for path in first["files"])
    assert all(not path.endswith(".tmp") for path in first["files"])


def test_manifest_records_five_systems_hashes_and_excludes_self_validation_cache(tmp_path: Path) -> None:
    paths = _make_manifest_fixture(tmp_path)
    first = build_manifest(paths)
    assert first["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert first["machine_proposed_development_only"] is True
    assert first["system_order"] == list(FORMAL_SYSTEM_IDS)
    assert len(first["systems"]) == 5
    assert first["systems"][0]["run"]["path"] == "retrieval/baseline/runs/hybrid_rrf_formal_dev250.trec"
    assert first["systems"][0]["frozen_baseline_reference"] is True
    assert all(
        value["actual_run_sha256"] == value["first_sha256"] == value["second_sha256"]
        for value in first["deterministic_run_hashes"].values()
    )

    _write_json(paths.release / "manifest.json", first)
    _write_json(paths.reports / "validation_report.json", {"status": PASS})
    _write_json(paths.reports / "validation_results.json", {"status": PASS})
    _write(paths.reports / "scratch.tmp")
    _write(paths.release / "indices" / "reranker" / "model_cache" / "ignored.bin")
    second = build_manifest(paths)
    assert second == first
    all_paths = {
        path
        for group in second["artifact_groups"].values()
        for path in group["files"]
    }
    assert "retrieval/retrieval-v1/manifest.json" not in all_paths
    assert not any("validation_report.json" in path for path in all_paths)
    assert not any("validation_results.json" in path for path in all_paths)
    assert not any("model_cache" in path for path in all_paths)
    assert not any(path.endswith(".tmp") for path in all_paths)


def test_manifest_hash_validation_rejects_tampered_bytes_despite_manifest_shape(tmp_path: Path) -> None:
    paths = _make_manifest_fixture(tmp_path)
    manifest = build_manifest(paths)
    _validate_manifest_hashes(paths, manifest)
    _write(paths.reports / "candidate_union.json", "tampered\n")
    with pytest.raises(ReleaseValidationError, match="Manifest hash differs"):
        _validate_manifest_hashes(paths, manifest)


def test_manifest_can_be_rebuilt_exactly_while_validation_report_is_present(tmp_path: Path) -> None:
    paths = _make_manifest_fixture(tmp_path)
    manifest = build_manifest(paths)
    _write_json(paths.release / "manifest.json", manifest)
    _write_json(paths.reports / "validation_report.json", {"status": "forged PASS"})
    details = _validate_manifest(paths, {})
    assert details["rebuilt_byte_contract_identical"] is True


def test_test_evidence_binds_before_after_and_current_source_hash(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    _make_source_tree(paths)
    snapshot = release_source_snapshot(paths)
    evidence = {
        "command": ["python", "-m", "pytest"],
        "returncode": 0,
        "source_file_count": snapshot["file_count"],
        "source_stable_during_tests": True,
        "source_tree_sha256": snapshot["tree_sha256"],
        "source_tree_sha256_after": snapshot["tree_sha256"],
        "status": PASS,
        "stdout": "42 passed in 1.00s\n",
    }
    _write_json(paths.reports / "test_results.json", evidence)
    details = _validate_test_evidence(paths, {})
    assert details["before_after_current_identical"] is True

    evidence["source_tree_sha256"] = "0" * 64
    evidence["status"] = PASS
    _write_json(paths.reports / "test_results.json", evidence)
    with pytest.raises(ReleaseValidationError, match="Source changed"):
        _validate_test_evidence(paths, {})


def test_acceptance_nested_pass_claims_are_not_silently_trusted() -> None:
    assert _all_passed_flags({"status": PASS, "gate": {"passed": True}}) == []
    assert _all_passed_flags({"status": PASS, "gate": {"passed": False}}) == [
        "acceptance.gate.passed"
    ]


def test_latency_schema_and_componentwise_arithmetic_are_recomputed(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    payload = _latency_payload()
    _write_json(paths.reports / "latency.json", payload)
    details = _validate_latency(paths, {})
    assert details["query_count"] == 250
    assert list(details["systems"]) == list(FORMAL_SYSTEM_IDS)

    payload["systems"][FORMAL_SYSTEM_IDS[4]]["total_latency_estimate_ms"]["mean_ms"] += 1.0
    _write_json(paths.reports / "latency.json", payload)
    with pytest.raises(ReleaseValidationError, match="formula differs"):
        _validate_latency(paths, {})


def test_validate_release_returns_explicit_fail_report_for_missing_disk_artifacts(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    report = validate_release(paths)
    assert report["status"] == "FAIL"
    assert report["overall_success"] is False
    assert report["failed_check_count"] > 0
    assert report["passed_check_count"] + report["failed_check_count"] == report["check_count"]
    assert report["errors"]
    assert all(check["status"] in {"PASS", "FAIL"} for check in report["checks"])
