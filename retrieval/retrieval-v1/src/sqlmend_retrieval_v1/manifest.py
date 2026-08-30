"""Deterministic release manifest construction for retrieval v1.

The manifest is deliberately a pure description of bytes already on disk.  It
does not write an artifact, run retrieval, or consult relevance judgments to
make ranking decisions.  Validation is kept in :mod:`validation` so a manifest
cannot certify itself merely by recording a favourable status.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .io import load_json, load_yaml, sha256_file
from .pipeline import RUN_FILES, SYSTEM_CONFIG_FILES
from .pool import FORMAL_SYSTEM_IDS


MANIFEST_SCHEMA_VERSION = "sqlmend-retrieval-v1-release-manifest-v1"
EVALUATION_LABEL = "machine-proposed development evaluation"
MANIFEST_FILENAME = "manifest.json"
VALIDATION_RESULT_FILENAMES = frozenset(
    {"validation_results.json", "validation_result.json", "validation_report.json"}
)

_CACHE_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "model_cache",
        "cache",
    }
)
_TEMP_SUFFIXES = (".tmp", ".temp", ".pyc", ".pyo", ".swp", "~")

_REQUIRED_EVALUATION_FILES = (
    "acceptance.json",
    "comparison_results.json",
    "evaluation_status.json",
    "judged_coverage.json",
    "overall_metrics.json",
    "per_query_metrics.csv",
    "run_determinism.json",
    "slice_metrics.csv",
)
_REQUIRED_REPORT_FILES = (
    "candidate_union.json",
    "protected_paths_before.json",
    "protected_paths_after.json",
    "latency.json",
    "test_results.json",
    "retrieval_v1_report.md",
)
_REQUIRED_POOL_FILES = (
    "pool_expansion_required.jsonl",
    "pool_expansion_summary.json",
)


class ManifestError(ValueError):
    """Raised when a complete release manifest cannot be constructed."""


def _release_relative(paths: Any, path: Path) -> str:
    """Return a stable repository-relative POSIX path."""

    root = Path(paths.root).resolve()
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ManifestError(f"Artifact is outside the repository root: {resolved}") from exc


def _is_excluded_release_file(paths: Any, path: Path) -> bool:
    """Apply the release-wide cache, temporary, and self-reference exclusions."""

    path = Path(path)
    try:
        relative_release = path.resolve().relative_to(Path(paths.release).resolve())
    except ValueError:
        relative_release = None
    lowered_parts = {part.casefold() for part in path.parts}
    if lowered_parts.intersection(_CACHE_PARTS):
        return True
    lowered_name = path.name.casefold()
    if lowered_name.endswith(_TEMP_SUFFIXES):
        return True
    if relative_release is not None:
        release_posix = relative_release.as_posix().casefold()
        if release_posix == MANIFEST_FILENAME:
            return True
        if lowered_name in VALIDATION_RESULT_FILENAMES:
            return True
        # A validator may use a timestamped or suffixed result filename.  Such
        # output must never make a previously built manifest self-invalidating.
        if lowered_name.startswith("validation_results."):
            return True
    return False


def _iter_files(paths: Any, roots: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for raw_root in roots:
        root = Path(raw_root)
        if root.is_file():
            if not _is_excluded_release_file(paths, root):
                files.add(root.resolve())
            continue
        if root.is_dir():
            for child in root.rglob("*"):
                if child.is_file() and not _is_excluded_release_file(paths, child):
                    files.add(child.resolve())
    return sorted(files, key=lambda value: _release_relative(paths, value))


def _tree_digest(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, observed in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(observed.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _snapshot(paths: Any, roots: Iterable[Path]) -> dict[str, Any]:
    files = {
        _release_relative(paths, path): sha256_file(path)
        for path in _iter_files(paths, roots)
    }
    return {
        "file_count": len(files),
        "tree_sha256": _tree_digest(files),
        "files": dict(sorted(files.items())),
    }


def release_source_snapshot(paths: Any) -> dict[str, Any]:
    """Hash all release inputs that can change implementation or tests.

    This is the single source-snapshot definition shared by test evidence,
    manifest construction, and release validation.  It intentionally includes
    config, ``src``, ``tests``, ``pyproject.toml``, and ``requirements.txt`` and
    excludes generated caches and temporary files.
    """

    release = Path(paths.release)
    required = (
        release / "config",
        release / "src",
        release / "tests",
        release / "pyproject.toml",
        release / "requirements.txt",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ManifestError(f"Release source inputs are missing: {missing}")
    snapshot = _snapshot(paths, required)
    return {
        "schema_version": "sqlmend-retrieval-v1-source-snapshot-v1",
        **snapshot,
    }


def _require_files(paths: Any, files: Iterable[Path], label: str) -> list[Path]:
    values = [Path(path) for path in files]
    missing = [_release_relative(paths, path) for path in values if not path.is_file()]
    if missing:
        raise ManifestError(f"Required {label} files are missing: {missing}")
    return values


def _artifact_snapshot(paths: Any, files: Iterable[Path]) -> dict[str, Any]:
    required = _require_files(paths, files, "artifact")
    return _snapshot(paths, required)


def _record(paths: Any, path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"Required artifact is missing: {_release_relative(paths, path)}")
    return {
        "path": _release_relative(paths, path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _system_paths(paths: Any, system_id: str) -> tuple[Path, Path, Path]:
    config_path = Path(paths.system_configs) / SYSTEM_CONFIG_FILES[system_id]
    if system_id == "hybrid_rrf_frozen_control_v1":
        run_path = Path(paths.baseline_run)
        provenance_path = run_path.with_suffix(".provenance.jsonl")
    else:
        run_path = Path(paths.runs) / RUN_FILES[system_id]
        provenance_path = run_path.with_suffix(".provenance.jsonl")
    return config_path, run_path, provenance_path


def _determinism_records(paths: Any, systems: list[dict[str, Any]]) -> dict[str, Any]:
    v1_path = Path(paths.evaluation) / "run_determinism.json"
    baseline_path = Path(paths.baseline) / "evaluation" / "run_determinism.json"
    v1_payload = load_json(v1_path)
    baseline_payload = load_json(baseline_path)
    v1_systems = v1_payload.get("systems")
    if not isinstance(v1_systems, Mapping):
        raise ManifestError("run_determinism.json has no systems mapping")
    baseline_entry = baseline_payload.get("hybrid")
    if not isinstance(baseline_entry, Mapping):
        raise ManifestError("Frozen baseline determinism evidence has no hybrid entry")

    result: dict[str, Any] = {}
    for system in systems:
        system_id = system["system_id"]
        evidence = baseline_entry if system["frozen_baseline_reference"] else v1_systems.get(system_id)
        if not isinstance(evidence, Mapping):
            raise ManifestError(f"Missing determinism evidence for {system_id}")
        result[system_id] = {
            "actual_run_sha256": system["run"]["sha256"],
            "byte_identical": evidence.get("byte_identical"),
            "first_sha256": evidence.get("first_sha256"),
            "second_sha256": evidence.get("second_sha256"),
            "provenance_identical": (
                None
                if system["frozen_baseline_reference"]
                else evidence.get("provenance_identical")
            ),
            "evidence_path": _release_relative(
                paths, baseline_path if system["frozen_baseline_reference"] else v1_path
            ),
        }
    return result


def _recursive_group(paths: Any, directory: Path, required_names: Iterable[str]) -> dict[str, Any]:
    _require_files(paths, (directory / name for name in required_names), directory.name)
    return _snapshot(paths, (directory,))


def build_manifest(paths: Any) -> dict[str, Any]:
    """Build a deterministic, self-excluding manifest from artifacts on disk.

    The returned mapping is ready for the repository's canonical ``write_json``
    helper.  This function never writes the manifest itself.
    """

    system_ids = tuple(FORMAL_SYSTEM_IDS)
    if tuple(SYSTEM_CONFIG_FILES) != system_ids:
        raise ManifestError("System config order differs from the five-system contract")

    systems: list[dict[str, Any]] = []
    config_paths: list[Path] = []
    run_paths: list[Path] = []
    provenance_paths: list[Path] = []
    for system_id in system_ids:
        config_path, run_path, provenance_path = _system_paths(paths, system_id)
        _require_files(paths, (config_path, run_path, provenance_path), system_id)
        config = load_yaml(config_path)
        if config.get("system_id") != system_id:
            raise ManifestError(f"Config system_id differs for {system_id}")
        run_tag = config.get("run_tag")
        if not isinstance(run_tag, str) or not run_tag:
            raise ManifestError(f"Config run_tag is invalid for {system_id}")
        config_paths.append(config_path)
        run_paths.append(run_path)
        provenance_paths.append(provenance_path)
        systems.append(
            {
                "system_id": system_id,
                "run_tag": run_tag,
                "frozen_baseline_reference": system_id == system_ids[0],
                "config": _record(paths, config_path),
                "run": _record(paths, run_path),
                "provenance": _record(paths, provenance_path),
            }
        )
    if len({system["run_tag"] for system in systems}) != len(systems):
        raise ManifestError("Five systems do not have independent run tags")

    input_paths = _require_files(
        paths,
        (
            Path(paths.corpus),
            Path(paths.queries),
            Path(paths.qrels),
            Path(paths.baseline_bm25_run),
            Path(paths.baseline_dense_run),
            Path(paths.baseline_run),
            Path(paths.baseline) / "evaluation" / "latency.json",
            Path(paths.config) / "baseline_lock.json",
        ),
        "input",
    )
    serialized_paths = _require_files(
        paths,
        (Path(paths.baseline_serialized_queries), Path(paths.serialized_queries)),
        "serialized query",
    )
    evaluation_snapshot = _recursive_group(
        paths, Path(paths.evaluation), _REQUIRED_EVALUATION_FILES
    )
    reports_snapshot = _recursive_group(
        paths, Path(paths.reports), _REQUIRED_REPORT_FILES
    )
    pool_snapshot = _recursive_group(
        paths, Path(paths.pool_expansion), _REQUIRED_POOL_FILES
    )
    index_path = Path(paths.release) / "indices" / "reranker" / "metadata.json"
    _require_files(paths, (index_path,), "index metadata")
    source_snapshot = release_source_snapshot(paths)

    groups = {
        "inputs": _artifact_snapshot(paths, input_paths),
        "serialized_queries": _artifact_snapshot(paths, serialized_paths),
        "system_configs": _artifact_snapshot(paths, config_paths),
        "runs": _artifact_snapshot(paths, run_paths),
        "provenance": _artifact_snapshot(paths, provenance_paths),
        "evaluation": evaluation_snapshot,
        "reports": reports_snapshot,
        "pool_expansion": pool_snapshot,
        "indices": _artifact_snapshot(paths, (index_path,)),
        "source_and_tests": source_snapshot,
    }

    all_files: dict[str, str] = {}
    for group in groups.values():
        for relative, observed in group["files"].items():
            previous = all_files.setdefault(relative, observed)
            if previous != observed:
                raise ManifestError(f"Conflicting hashes collected for {relative}")

    test_results_path = Path(paths.reports) / "test_results.json"
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release": "retrieval-v1",
        "module": "sqlmend-retrieval-v1",
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "data_usage": (
            "machine-proposed development data for development experiments and "
            "regression validation; not human gold and not a final held-out test set"
        ),
        "system_order": list(system_ids),
        "systems": systems,
        "artifact_groups": groups,
        "artifact_file_count": len(all_files),
        "artifact_tree_sha256": _tree_digest(all_files),
        "deterministic_run_hashes": _determinism_records(paths, systems),
        "test_evidence": {
            **_record(paths, test_results_path),
            "source_snapshot": source_snapshot,
        },
        "excluded_from_manifest": {
            "self": _release_relative(paths, Path(paths.release) / MANIFEST_FILENAME),
            "validation_results": sorted(VALIDATION_RESULT_FILENAMES),
            "cache_directory_names": sorted(_CACHE_PARTS),
            "temporary_suffixes": list(_TEMP_SUFFIXES),
        },
        "rebuild": {
            "working_directory": ".",
            "commands": [
                "python -m pip install -r retrieval/retrieval-v1/requirements.txt",
                "python -m pip install -e retrieval/retrieval-v1 --no-deps",
                "python -m sqlmend_retrieval_v1.cli --root . all --clean",
            ],
        },
    }
    return manifest


__all__ = [
    "EVALUATION_LABEL",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestError",
    "VALIDATION_RESULT_FILENAMES",
    "build_manifest",
    "release_source_snapshot",
]
