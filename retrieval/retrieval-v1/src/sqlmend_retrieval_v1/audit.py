"""Static baseline locking and before/after protected-path byte audits."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Any, Iterable

from .io import load_json, sha256_file, write_json
from .paths import ProjectPaths


PROTECTED_PREFIXES = ("construction", "annotation/codex", "retrieval/baseline")


def _git_lines(root: Path, *arguments: str) -> list[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in completed.stdout.splitlines() if line]


def tracked_baseline_files(root: Path) -> list[str]:
    return sorted(_git_lines(root, "ls-files", "retrieval/baseline"))


def tracked_baseline_tree(root: Path) -> dict[str, Any]:
    files = tracked_baseline_files(root)
    digest = hashlib.sha256()
    file_hashes: dict[str, str] = {}
    for relative in files:
        observed = sha256_file(root / relative)
        file_hashes[relative] = observed
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(observed.encode("ascii"))
        digest.update(b"\n")
    return {
        "file_count": len(files),
        "tree_sha256": digest.hexdigest(),
        "files": file_hashes,
    }


def verify_static_lock(paths: ProjectPaths) -> dict[str, Any]:
    lock = load_json(paths.config / "baseline_lock.json")
    tracked = tracked_baseline_tree(paths.root)
    errors: list[str] = []
    if tracked["file_count"] != int(lock["tracked_file_count"]):
        errors.append("tracked baseline file count differs from the static lock")
    if tracked["tree_sha256"] != lock["tracked_tree_sha256"]:
        errors.append("tracked baseline tree hash differs from the static lock")
    checked: dict[str, str] = {}
    for section in ("critical_files", "input_files"):
        for relative, expected in dict(lock[section]).items():
            path = paths.root / relative
            if not path.is_file():
                errors.append(f"locked file is missing: {relative}")
                continue
            observed = sha256_file(path)
            checked[relative] = observed
            if observed != expected:
                errors.append(f"locked file hash differs: {relative}")
    protected_status = _git_lines(
        paths.root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *PROTECTED_PREFIXES,
    )
    if protected_status:
        errors.append("Git reports tracked or untracked changes under protected paths")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "tracked_baseline_file_count": tracked["file_count"],
        "tracked_baseline_tree_sha256": tracked["tree_sha256"],
        "checked_locked_files": checked,
        "protected_git_status": protected_status,
    }


def _iter_protected_files(paths: ProjectPaths) -> Iterable[tuple[str, Path]]:
    for prefix in PROTECTED_PREFIXES:
        directory = paths.root / prefix
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            yield path.relative_to(paths.root).as_posix(), path


def protected_snapshot(paths: ProjectPaths) -> dict[str, Any]:
    files = {relative: sha256_file(path) for relative, path in _iter_protected_files(paths)}
    digest = hashlib.sha256()
    for relative, observed in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(observed.encode("ascii"))
        digest.update(b"\n")
    return {
        "schema_version": "sqlmend-retrieval-v1-protected-snapshot-v1",
        "file_count": len(files),
        "tree_sha256": digest.hexdigest(),
        "files": files,
    }


def audit_protected_paths(paths: ProjectPaths, phase: str) -> dict[str, Any]:
    if phase not in {"before", "after"}:
        raise ValueError("phase must be 'before' or 'after'")
    static = verify_static_lock(paths)
    snapshot = protected_snapshot(paths)
    result: dict[str, Any] = {
        "schema_version": "sqlmend-retrieval-v1-protected-audit-v1",
        "phase": phase,
        "static_lock": static,
        "snapshot": snapshot,
        "protected_paths_unchanged": None,
        "status": static["status"],
        "errors": list(static["errors"]),
    }
    before_path = paths.reports / "protected_paths_before.json"
    if phase == "after":
        if not before_path.is_file():
            result["errors"].append("before protected-path snapshot is missing")
        else:
            before = load_json(before_path)
            unchanged = before.get("snapshot", {}).get("files") == snapshot["files"]
            result["protected_paths_unchanged"] = unchanged
            if not unchanged:
                before_files = dict(before.get("snapshot", {}).get("files", {}))
                current_files = snapshot["files"]
                result["added"] = sorted(set(current_files) - set(before_files))
                result["removed"] = sorted(set(before_files) - set(current_files))
                result["changed"] = sorted(
                    relative
                    for relative in set(before_files).intersection(current_files)
                    if before_files[relative] != current_files[relative]
                )
                result["errors"].append("protected path bytes changed after the before audit")
        if result["errors"]:
            result["status"] = "FAIL"
    output = paths.reports / f"protected_paths_{phase}.json"
    write_json(output, result)
    return result
