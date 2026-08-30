from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sqlmend_retrieval_v1 import cli


def _paths(tmp_path: Path) -> SimpleNamespace:
    release = tmp_path / "retrieval" / "retrieval-v1"
    reports = release / "reports"
    reports.mkdir(parents=True)
    return SimpleNamespace(root=tmp_path, release=release, reports=reports)


def _snapshot(digest: str = "a" * 64) -> dict[str, object]:
    return {
        "schema_version": "sqlmend-retrieval-v1-source-snapshot-v1",
        "file_count": 2,
        "tree_sha256": digest,
        "files": {
            "retrieval/retrieval-v1/src/module.py": digest,
            "retrieval/retrieval-v1/tests/test_module.py": digest,
        },
    }


def test_parser_supports_every_required_command_and_global_root() -> None:
    parser = cli._parser()
    required = {
        "audit-protected-paths",
        "verify-inputs",
        "build-runs",
        "check-pool",
        "evaluate",
        "benchmark",
        "test",
        "report",
        "finalize",
        "validate",
        "clean",
        "all",
    }
    observed = set(cli.COMMANDS) | {"audit-protected-paths"}
    assert observed == required
    audit = parser.parse_args(
        ["--root", "repo", "audit-protected-paths", "--phase", "after"]
    )
    assert audit.root == "repo"
    assert audit.phase == "after"
    all_args = parser.parse_args(["--root", "repo", "all", "--clean"])
    assert all_args.command == "all"
    assert all_args.clean is True


def test_formal_test_command_records_before_after_and_current_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    snapshot = _snapshot()
    snapshots = iter((snapshot, snapshot, snapshot))
    monkeypatch.setattr(cli, "_release_source_snapshot", lambda _paths: next(snapshots))
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="47 passed in 1.23s\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli.run_test_suite(paths)

    assert result["status"] == "PASS"
    assert result["source_stable_during_tests"] is True
    assert result["source_matches_current"] is True
    assert result["source_tree_sha256_before"] == snapshot["tree_sha256"]
    assert result["source_tree_sha256_after"] == snapshot["tree_sha256"]
    assert result["source_tree_sha256_current"] == snapshot["tree_sha256"]
    assert tuple(result["source_snapshots"]) == ("before", "after", "current")
    command = captured["command"]
    assert command[1:] == [
        "-m",
        "pytest",
        "retrieval/retrieval-v1/tests",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["check"] is False
    environment = captured["env"]
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert str(paths.release / "src") in environment["PYTHONPATH"]
    written = json.loads((paths.reports / "test_results.json").read_text(encoding="utf-8"))
    assert written == result


def test_formal_test_command_fails_if_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    before = _snapshot("a" * 64)
    after = _snapshot("b" * 64)
    snapshots = iter((before, after, after))
    monkeypatch.setattr(cli, "_release_source_snapshot", lambda _paths: next(snapshots))
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="47 passed in 1.23s\n", stderr=""
        ),
    )
    result = cli.run_test_suite(paths)
    assert result["status"] == "FAIL"
    assert result["source_stable_during_tests"] is False


def test_clean_removes_only_allowlisted_generated_artifacts(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    release = paths.release
    for name in ("config", "src", "tests"):
        directory = release / name
        directory.mkdir()
        (directory / "keep.txt").write_text("keep", encoding="utf-8")
    (release / "README.md").write_text("keep", encoding="utf-8")
    for name in cli.GENERATED_DIRECTORY_NAMES:
        directory = release / name
        directory.mkdir(exist_ok=True)
        (directory / "generated.txt").write_text("remove", encoding="utf-8")
    (release / cli.MANIFEST_NAME).write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    result = cli.clean_generated(paths)

    assert result["status"] == "PASS"
    assert set(result["removed"]) == set(cli.GENERATED_DIRECTORY_NAMES) | {
        cli.MANIFEST_NAME
    }
    assert all(not (release / name).exists() for name in cli.GENERATED_DIRECTORY_NAMES)
    assert not (release / cli.MANIFEST_NAME).exists()
    assert all((release / name / "keep.txt").is_file() for name in ("config", "src", "tests"))
    assert (release / "README.md").read_text(encoding="utf-8") == "keep"
    assert outside.read_text(encoding="utf-8") == "keep"


def test_clean_path_guard_rejects_escape_and_release_root(tmp_path: Path) -> None:
    release = tmp_path / "retrieval-v1"
    release.mkdir()
    with pytest.raises(cli.OrchestrationError, match="outside retrieval-v1"):
        cli._ensure_within_release(release, tmp_path / "outside")
    with pytest.raises(cli.OrchestrationError, match="retrieval-v1 root"):
        cli._ensure_within_release(release, release)


def test_all_runs_required_order_with_optional_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    calls: list[str] = []
    inputs = SimpleNamespace(
        candidates={"q1": [object()]},
        online_queries={"q1": object()},
        corpus_by_id={"d1": object()},
    )

    monkeypatch.setattr(
        cli,
        "clean_generated",
        lambda _paths: calls.append("clean") or {"status": "PASS"},
    )

    def audit(_paths, phase):
        calls.append(phase)
        return {
            "phase": phase,
            "status": "PASS",
            "errors": [],
            "protected_paths_unchanged": True if phase == "after" else None,
        }

    monkeypatch.setattr(cli, "audit_protected_paths", audit)
    monkeypatch.setattr(
        cli,
        "verify_and_load_online_inputs",
        lambda _paths: calls.append("verify") or inputs,
    )
    monkeypatch.setattr(
        cli,
        "build_all_runs",
        lambda _paths, _inputs: calls.append("build") or {"system": [1]},
    )
    monkeypatch.setattr(
        cli,
        "run_pool_audit",
        lambda _paths, _inputs: calls.append("pool")
        or {"summary": {"evaluation_integrity_status": "PASS"}},
    )
    monkeypatch.setattr(
        cli,
        "run_evaluation",
        lambda _paths, _inputs: calls.append("evaluate")
        or {
            "evaluation_integrity_status": "PASS",
            "retrieval_quality_status": "PASS",
        },
    )
    monkeypatch.setattr(
        cli,
        "run_latency_benchmark",
        lambda _paths, _inputs: calls.append("benchmark")
        or {"query_count": 1, "systems": {}},
    )
    monkeypatch.setattr(
        cli,
        "run_test_suite",
        lambda _paths: calls.append("test") or {"status": "PASS", "returncode": 0},
    )
    monkeypatch.setattr(
        cli,
        "finalize_command",
        lambda _paths: calls.append("finalize") or {"status": "PASS"},
    )
    monkeypatch.setattr(
        cli,
        "validate_command",
        lambda _paths: calls.append("validate")
        or {"status": "PASS", "overall_success": True},
    )

    result = cli.all_command(paths, clean=True)

    assert result["status"] == "PASS"
    assert calls == [
        "clean",
        "before",
        "verify",
        "build",
        "pool",
        "evaluate",
        "benchmark",
        "test",
        "after",
        "finalize",
        "validate",
    ]


def test_all_runs_after_audit_even_when_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    calls: list[str] = []
    inputs = SimpleNamespace(
        candidates={"q1": [object()]},
        online_queries={"q1": object()},
        corpus_by_id={"d1": object()},
    )

    def audit(_paths, phase):
        calls.append(phase)
        return {
            "phase": phase,
            "status": "PASS",
            "errors": [],
            "protected_paths_unchanged": True if phase == "after" else None,
        }

    monkeypatch.setattr(cli, "audit_protected_paths", audit)
    monkeypatch.setattr(
        cli,
        "verify_and_load_online_inputs",
        lambda _paths: calls.append("verify") or inputs,
    )

    def fail_build(_paths, _inputs):
        calls.append("build")
        raise RuntimeError("build failed")

    monkeypatch.setattr(cli, "build_all_runs", fail_build)
    with pytest.raises(RuntimeError, match="build failed"):
        cli.all_command(paths)
    assert calls == ["before", "verify", "build", "after"]


def test_main_returns_nonzero_for_blocked_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        cli.ProjectPaths,
        "discover",
        classmethod(lambda cls, explicit_root=None: paths),
    )
    monkeypatch.setattr(
        cli,
        "check_pool_command",
        lambda _paths: {
            "status": "BLOCKED",
            "evaluation_integrity_status": "BLOCKED",
        },
    )
    returncode = cli.main(["--root", str(tmp_path), "check-pool"])
    assert returncode == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["evaluation_integrity_status"] == "BLOCKED"
