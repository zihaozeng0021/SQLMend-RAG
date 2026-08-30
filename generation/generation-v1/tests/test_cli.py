from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sqlmend_generation_v1 import cli
from sqlmend_generation_v1.contracts import G0_SYSTEM_ID, G1_SYSTEM_ID


def _paths(tmp_path: Path) -> SimpleNamespace:
    release = tmp_path / "generation" / "generation-v1"
    release.mkdir(parents=True)
    return SimpleNamespace(root=tmp_path, release=release)


def test_system_aliases_resolve_to_formal_ids() -> None:
    assert cli._system_id("g0") == G0_SYSTEM_ID
    assert cli._system_id("g1") == G1_SYSTEM_ID
    assert cli._system_id(G0_SYSTEM_ID) == G0_SYSTEM_ID
    assert cli._system_id(G1_SYSTEM_ID) == G1_SYSTEM_ID


def test_audit_parser_accepts_all_three_release_phases() -> None:
    for phase in ("before", "after", "current"):
        args = cli._parser().parse_args(
            ["audit-protected-paths", "--phase", phase]
        )
        assert args.phase == phase


def test_clean_removes_only_allowlisted_generated_paths(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    for name in ("config", "schema", "src", "tests"):
        target = paths.release / name
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
    for name in cli.GENERATED_DIRECTORY_NAMES:
        target = paths.release / name
        target.mkdir()
        (target / "remove.txt").write_text("remove", encoding="utf-8")
    (paths.release / cli.MANIFEST_NAME).write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    result = cli.clean_generated(paths)

    assert result["status"] == "PASS"
    assert all(not (paths.release / name).exists() for name in cli.GENERATED_DIRECTORY_NAMES)
    assert not (paths.release / cli.MANIFEST_NAME).exists()
    assert all((paths.release / name / "keep.txt").is_file() for name in ("config", "schema", "src", "tests"))
    assert outside.read_text(encoding="utf-8") == "keep"


def test_clean_path_guard_rejects_escape_and_release_root(tmp_path: Path) -> None:
    release = tmp_path / "generation-v1"
    release.mkdir()
    with pytest.raises(cli.OrchestrationError, match="outside"):
        cli._ensure_within_release(release, tmp_path / "elsewhere")
    with pytest.raises(cli.OrchestrationError, match="root"):
        cli._ensure_within_release(release, release)


def test_all_runs_after_audit_when_generation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    calls: list[str] = []

    def audit(_paths, phase):
        calls.append(phase)
        return {
            "status": "PASS",
            "phase": phase,
            "protected_paths_unchanged": phase in {"after", "current"} or None,
            "snapshot": {},
        }

    monkeypatch.setattr(cli, "audit_protected_paths", audit)
    monkeypatch.setattr(cli, "verify_inputs_command", lambda _paths: calls.append("verify") or {"status": "PASS"})
    monkeypatch.setattr(cli, "inspect_model_command", lambda _paths: calls.append("model") or {"status": "PASS"})
    monkeypatch.setattr(cli, "prepare_command", lambda _paths: calls.append("prepare") or {"status": "PASS"})

    def fail(_paths, system_id):
        calls.append(system_id)
        raise RuntimeError("generation failed")

    monkeypatch.setattr(cli, "generate_command", fail)
    with pytest.raises(RuntimeError, match="generation failed"):
        cli.all_command(paths)
    assert calls == [
        "before",
        "verify",
        "model",
        "prepare",
        G0_SYSTEM_ID,
        "after",
        "current",
    ]


def test_all_orchestration_order_and_quality_exit_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(cli, "clean_generated", lambda _paths: calls.append("clean") or {"status": "PASS"})

    def audit(_paths, phase):
        calls.append(phase)
        return {
            "status": "PASS",
            "phase": phase,
            "protected_paths_unchanged": phase in {"after", "current"} or None,
            "snapshot": {},
        }

    monkeypatch.setattr(cli, "audit_protected_paths", audit)
    monkeypatch.setattr(cli, "verify_inputs_command", lambda _paths: calls.append("verify") or {"status": "PASS"})
    monkeypatch.setattr(cli, "inspect_model_command", lambda _paths: calls.append("model") or {"status": "PASS"})
    monkeypatch.setattr(cli, "prepare_command", lambda _paths: calls.append("prepare") or {"status": "PASS"})
    monkeypatch.setattr(cli, "generate_command", lambda _paths, system: calls.append(system) or {"status": "PASS"})
    monkeypatch.setattr(cli, "evaluate_command", lambda _paths: calls.append("evaluate") or {"status": "PASS"})
    monkeypatch.setattr(cli, "test_command", lambda _paths: calls.append("test") or {"status": "PASS", "returncode": 0})
    monkeypatch.setattr(cli, "finalize_command", lambda _paths: calls.append("finalize") or {"status": "PASS"})
    monkeypatch.setattr(
        cli,
        "validate_command",
        lambda _paths: calls.append("validate")
        or {"status": "PASS", "quality_status": "FAIL", "overall_success": False},
    )

    result = cli.all_command(paths, clean=True)

    assert result["status"] == "PASS"
    assert result["overall_success"] is False
    assert calls == [
        "clean",
        "before",
        "verify",
        "model",
        "prepare",
        G0_SYSTEM_ID,
        G1_SYSTEM_ID,
        "evaluate",
        "test",
        "after",
        "current",
        "finalize",
        "validate",
    ]
