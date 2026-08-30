"""Byte-for-byte audits for every frozen Phase 10 input tree.

The generation release is intentionally outside the four protected prefixes.
Snapshots enumerate *every* file below those prefixes, including untracked
files and caches: a generation command must not make even an incidental byte
change there.  Release caches are excluded by :mod:`manifest`, but protected
input snapshots never apply such exclusions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTECTED_PREFIXES = (
    "construction",
    "annotation/codex",
    "retrieval/baseline",
    "retrieval/retrieval-v1",
)
SNAPSHOT_SCHEMA_VERSION = "sqlmend-generation-v1-protected-snapshot-v1"
AUDIT_SCHEMA_VERSION = "sqlmend-generation-v1-protected-audit-v1"


class AuditError(ValueError):
    """Raised when a protected-path audit cannot be independently verified."""


def repository_root(paths_or_root: Any) -> Path:
    """Resolve a repository root from either a ``ProjectPaths``-like object or a path."""

    candidate = getattr(paths_or_root, "root", paths_or_root)
    return Path(candidate).resolve()


def release_root(paths_or_root: Any) -> Path:
    explicit = getattr(paths_or_root, "release", None)
    if explicit is not None:
        return Path(explicit).resolve()
    return repository_root(paths_or_root) / "generation" / "generation-v1"


def reports_directory(paths_or_root: Any) -> Path:
    explicit = getattr(paths_or_root, "reports", None)
    return Path(explicit).resolve() if explicit is not None else release_root(paths_or_root) / "reports"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def tree_sha256(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, observed in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(observed.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _protected_files(root: Path) -> Iterable[tuple[str, Path]]:
    for prefix in PROTECTED_PREFIXES:
        directory = root / prefix
        if not directory.is_dir():
            raise AuditError(f"Protected directory is missing: {prefix}")
        for path in sorted(
            (candidate for candidate in directory.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        ):
            yield path.relative_to(root).as_posix(), path


def protected_snapshot(paths_or_root: Any) -> dict[str, Any]:
    """Hash every file in all four protected trees."""

    root = repository_root(paths_or_root)
    files = {relative: sha256_file(path) for relative, path in _protected_files(root)}
    per_prefix: dict[str, int] = {}
    for prefix in PROTECTED_PREFIXES:
        marker = prefix + "/"
        per_prefix[prefix] = sum(relative.startswith(marker) for relative in files)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "protected_prefixes": list(PROTECTED_PREFIXES),
        "all_files_included": True,
        "file_count": len(files),
        "file_count_by_prefix": per_prefix,
        "tree_sha256": tree_sha256(files),
        "files": dict(sorted(files.items())),
    }


def compare_snapshots(expected: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    expected_files = dict(expected.get("files", {}))
    observed_files = dict(observed.get("files", {}))
    added = sorted(set(observed_files) - set(expected_files))
    removed = sorted(set(expected_files) - set(observed_files))
    changed = sorted(
        relative
        for relative in set(expected_files).intersection(observed_files)
        if expected_files[relative] != observed_files[relative]
    )
    identical = not added and not removed and not changed
    return {
        "identical": identical,
        "added": added,
        "removed": removed,
        "changed": changed,
        "expected_tree_sha256": expected.get("tree_sha256"),
        "observed_tree_sha256": observed.get("tree_sha256"),
    }


def _audit_path(paths_or_root: Any, phase: str) -> Path:
    return reports_directory(paths_or_root) / f"protected_paths_{phase}.json"


def audit_protected_paths(
    paths_or_root: Any,
    phase: str,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Create one of the before/after/current byte-audit artifacts."""

    if phase not in {"before", "after", "current"}:
        raise ValueError("phase must be 'before', 'after', or 'current'")
    snapshot = protected_snapshot(paths_or_root)
    comparisons: dict[str, Any] = {}
    errors: list[str] = []
    if phase in {"after", "current"}:
        phases = ("before",) if phase == "after" else ("before", "after")
        for previous_phase in phases:
            previous_path = _audit_path(paths_or_root, previous_phase)
            if not previous_path.is_file():
                errors.append(f"{previous_phase} protected-path audit is missing")
                continue
            previous = load_json(previous_path)
            comparison = compare_snapshots(previous.get("snapshot", {}), snapshot)
            comparisons[f"{previous_phase}_to_{phase}"] = comparison
            if not comparison["identical"]:
                errors.append(
                    f"protected bytes differ between {previous_phase} and {phase}"
                )
    result = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "phase": phase,
        "protected_prefixes": list(PROTECTED_PREFIXES),
        "snapshot": snapshot,
        "comparisons": comparisons,
        "protected_paths_unchanged": not errors if phase != "before" else None,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }
    write_json(output_path or _audit_path(paths_or_root, phase), result)
    return result


def _validate_saved_snapshot(payload: Mapping[str, Any], phase: str) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != AUDIT_SCHEMA_VERSION:
        errors.append(f"{phase} audit schema_version differs")
    if payload.get("phase") != phase:
        errors.append(f"{phase} audit phase differs")
    if payload.get("protected_prefixes") != list(PROTECTED_PREFIXES):
        errors.append(f"{phase} audit does not name all protected prefixes")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return errors + [f"{phase} audit has no snapshot mapping"]
    files = snapshot.get("files")
    if not isinstance(files, Mapping):
        return errors + [f"{phase} audit has no per-file hashes"]
    if snapshot.get("file_count") != len(files):
        errors.append(f"{phase} audit file_count differs from its file ledger")
    if snapshot.get("tree_sha256") != tree_sha256(dict(files)):
        errors.append(f"{phase} audit tree hash differs from its file ledger")
    for prefix in PROTECTED_PREFIXES:
        if not any(relative.startswith(prefix + "/") for relative in files):
            errors.append(f"{phase} audit has no files for protected prefix {prefix}")
    return errors


def verify_protected_audits(paths_or_root: Any) -> dict[str, Any]:
    """Independently compare saved before/after/current ledgers with live bytes."""

    errors: list[str] = []
    payloads: dict[str, Mapping[str, Any]] = {}
    for phase in ("before", "after", "current"):
        path = _audit_path(paths_or_root, phase)
        if not path.is_file():
            errors.append(f"protected_paths_{phase}.json is missing")
            continue
        payload = load_json(path)
        if not isinstance(payload, Mapping):
            errors.append(f"protected_paths_{phase}.json is not a JSON object")
            continue
        payloads[phase] = payload
        errors.extend(_validate_saved_snapshot(payload, phase))

    live = protected_snapshot(paths_or_root)
    comparisons: dict[str, Any] = {}
    if len(payloads) == 3:
        for left, right in (("before", "after"), ("before", "current"), ("after", "current")):
            comparison = compare_snapshots(
                payloads[left]["snapshot"], payloads[right]["snapshot"]
            )
            comparisons[f"{left}_to_{right}"] = comparison
            if not comparison["identical"]:
                errors.append(f"saved protected snapshots differ: {left} vs {right}")
    for phase, payload in payloads.items():
        comparison = compare_snapshots(payload["snapshot"], live)
        comparisons[f"{phase}_to_live"] = comparison
        if not comparison["identical"]:
            errors.append(f"live protected bytes differ from {phase} snapshot")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "protected_prefixes": list(PROTECTED_PREFIXES),
        "live_snapshot": live,
        "comparisons": comparisons,
        "before_after_current_live_identical": not errors,
    }


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditError",
    "PROTECTED_PREFIXES",
    "SNAPSHOT_SCHEMA_VERSION",
    "audit_protected_paths",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "compare_snapshots",
    "load_json",
    "protected_snapshot",
    "release_root",
    "reports_directory",
    "repository_root",
    "sha256_file",
    "tree_sha256",
    "verify_protected_audits",
    "write_json",
]
