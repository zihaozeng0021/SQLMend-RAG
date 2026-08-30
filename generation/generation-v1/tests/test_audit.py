from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlmend_generation_v1.audit import (
    PROTECTED_PREFIXES,
    audit_protected_paths,
    protected_snapshot,
    verify_protected_audits,
)


def _paths(tmp_path: Path) -> SimpleNamespace:
    release = tmp_path / "generation" / "generation-v1"
    reports = release / "reports"
    reports.mkdir(parents=True)
    for index, prefix in enumerate(PROTECTED_PREFIXES, start=1):
        directory = tmp_path / prefix
        directory.mkdir(parents=True)
        (directory / f"frozen-{index}.txt").write_text(
            f"protected {index}\n", encoding="utf-8"
        )
    return SimpleNamespace(root=tmp_path, release=release, reports=reports)


def test_snapshot_includes_every_file_even_protected_cache_and_tmp(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    cache = tmp_path / "retrieval" / "retrieval-v1" / "__pycache__" / "state.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache bytes")
    temporary = tmp_path / "annotation" / "codex" / "scratch.tmp"
    temporary.write_bytes(b"temporary bytes")

    snapshot = protected_snapshot(paths)

    assert snapshot["protected_prefixes"] == list(PROTECTED_PREFIXES)
    assert snapshot["all_files_included"] is True
    assert "retrieval/retrieval-v1/__pycache__/state.pyc" in snapshot["files"]
    assert "annotation/codex/scratch.tmp" in snapshot["files"]
    assert snapshot["file_count"] == 6


def test_before_after_current_and_live_are_byte_identical(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert audit_protected_paths(paths, "before")["status"] == "PASS"
    assert audit_protected_paths(paths, "after")["status"] == "PASS"
    assert audit_protected_paths(paths, "current")["status"] == "PASS"

    verified = verify_protected_audits(paths)

    assert verified["status"] == "PASS"
    assert verified["before_after_current_live_identical"] is True
    assert all(value["identical"] for value in verified["comparisons"].values())


def test_after_audit_and_live_verification_expose_exact_tamper(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    audit_protected_paths(paths, "before")
    target = tmp_path / "retrieval" / "baseline" / "frozen-3.txt"
    target.write_text("tampered\n", encoding="utf-8")

    after = audit_protected_paths(paths, "after")

    assert after["status"] == "FAIL"
    comparison = after["comparisons"]["before_to_after"]
    assert comparison["changed"] == ["retrieval/baseline/frozen-3.txt"]
    # A forged saved PASS cannot override the per-file comparison.
    audit_protected_paths(paths, "current")
    verified = verify_protected_audits(paths)
    assert verified["status"] == "FAIL"
    assert "saved protected snapshots differ: before vs after" in verified["errors"]
