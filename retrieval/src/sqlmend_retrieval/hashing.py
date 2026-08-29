"""Byte-level hashing and protected-input snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .paths import ProjectPaths

PROTECTED_ROOTS = ("construction", "annotation/codex")
RELEASE_SOURCE_FILES = ("README.md", "pyproject.toml", "requirements.txt")
RELEASE_SOURCE_DIRECTORIES = ("src", "tests", "config")


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)


def sha256_tree(path: Path) -> str:
    """Hash a directory from sorted relative paths, file hashes, and sizes."""
    if not path.is_dir():
        raise FileNotFoundError(path)
    aggregate = hashlib.sha256()
    for candidate in sorted(
        (item for item in path.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(path).as_posix(),
    ):
        relative = candidate.relative_to(path).as_posix()
        digest = sha256_file(candidate)
        size = candidate.stat().st_size
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(str(size).encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest()


def snapshot_protected_paths(paths: ProjectPaths) -> dict[str, Any]:
    """Return a fresh byte-level snapshot of every protected file.

    This is public so release validation can compare the recorded after-audit
    with the bytes that exist at validation time, rather than trusting a stale
    or hand-edited report.
    """
    records: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for root_name in PROTECTED_ROOTS:
        protected_root = paths.root / Path(root_name)
        if not protected_root.is_dir():
            raise FileNotFoundError(f"Protected directory is missing: {protected_root}")
        for path in sorted(
            (candidate for candidate in protected_root.rglob("*") if candidate.is_file()),
            key=lambda item: item.relative_to(paths.root).as_posix(),
        ):
            relative = path.relative_to(paths.root).as_posix()
            digest = sha256_file(path)
            size = path.stat().st_size
            records.append({"path": relative, "sha256": digest, "size_bytes": size})
            aggregate.update(relative.encode("utf-8"))
            aggregate.update(b"\0")
            aggregate.update(digest.encode("ascii"))
            aggregate.update(b"\0")
            aggregate.update(str(size).encode("ascii"))
            aggregate.update(b"\n")
    return {
        "algorithm": "sha256",
        "file_count": len(records),
        "tree_sha256": aggregate.hexdigest(),
        "files": records,
    }


def snapshot_release_source(paths: ProjectPaths) -> dict[str, Any]:
    """Hash the code/config/test inputs whose test result is meant to attest."""

    candidates: set[Path] = set()
    for name in RELEASE_SOURCE_FILES:
        path = paths.retrieval / name
        if path.is_file():
            candidates.add(path)
    for name in RELEASE_SOURCE_DIRECTORIES:
        root = paths.retrieval / name
        if root.is_dir():
            allowed_suffixes = {".py"} if name in {"src", "tests"} else {".yaml", ".yml"}
            candidates.update(
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.suffix.casefold() in allowed_suffixes
                and "__pycache__" not in path.parts
                and ".pytest_cache" not in path.parts
            )
    records: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.relative_to(paths.retrieval).as_posix()):
        relative = path.relative_to(paths.retrieval).as_posix()
        digest = sha256_file(path)
        size = path.stat().st_size
        records.append({"path": relative, "sha256": digest, "size_bytes": size})
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(str(size).encode("ascii"))
        aggregate.update(b"\n")
    if not records:
        raise FileNotFoundError("No retrieval source/config/test files found")
    return {
        "algorithm": "sha256",
        "file_count": len(records),
        "tree_sha256": aggregate.hexdigest(),
        "files": records,
    }


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    before_map = {item["path"]: (item["sha256"], item["size_bytes"]) for item in before["files"]}
    after_map = {item["path"]: (item["sha256"], item["size_bytes"]) for item in after["files"]}
    return {
        "added": sorted(after_map.keys() - before_map.keys()),
        "removed": sorted(before_map.keys() - after_map.keys()),
        "changed": sorted(
            path for path in before_map.keys() & after_map.keys() if before_map[path] != after_map[path]
        ),
    }


def audit_protected_paths(paths: ProjectPaths, phase: str) -> dict[str, Any]:
    if phase not in {"before", "after"}:
        raise ValueError("phase must be 'before' or 'after'")
    report_path = paths.protected_report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    current = snapshot_protected_paths(paths)
    if phase == "before":
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            recorded = existing.get("before")
            if not isinstance(recorded, dict) or not isinstance(recorded.get("files"), list):
                raise ValueError("Existing protected-path report has no complete before snapshot.")
            differences = _diff(recorded, current)
            if (
                any(differences.values())
                or recorded.get("tree_sha256") != current.get("tree_sha256")
                or recorded.get("file_count") != current.get("file_count")
            ):
                raise RuntimeError(
                    "Refusing to overwrite the anchored before snapshot after protected bytes changed."
                )
            # Idempotent retries must preserve the original anchor and any
            # completed after-audit instead of silently re-baselining it.
            return existing
        report = {
            "schema_version": "1.0.0",
            "protected_roots": list(PROTECTED_ROOTS),
            "before": current,
            "after": None,
            "differences": None,
            "protected_paths_unchanged": None,
        }
    else:
        if not report_path.exists():
            raise FileNotFoundError("Run audit-protected-paths --phase before first.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        before = report.get("before")
        if not before:
            raise ValueError("Protected-path report has no before snapshot.")
        differences = _diff(before, current)
        unchanged = not any(differences.values())
        report.update(
            {
                "after": current,
                "differences": differences,
                "protected_paths_unchanged": unchanged,
            }
        )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
