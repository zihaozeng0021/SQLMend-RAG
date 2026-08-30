"""Deterministic, self-excluding release manifest for generation v1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .audit import (
    canonical_json_sha256,
    load_json,
    release_root,
    repository_root,
    sha256_file,
    tree_sha256,
    write_json,
)


MANIFEST_SCHEMA_VERSION = "sqlmend-generation-v1-release-manifest-v1"
BASELINE_MANIFEST_SCHEMA_VERSION = "sqlmend-generation-baseline-release-manifest-v1"
MANIFEST_FILENAME = "manifest.json"
EVALUATION_LABEL = "machine-proposed development evaluation"

VALIDATION_RESULT_FILENAMES = frozenset(
    {
        "validation_report.json",
        "validation_results.json",
        "validation_result.json",
    }
)
_EXCLUDED_DIRECTORY_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "cache",
        "model_cache",
        "tmp",
        "temp",
    }
)
_TEMPORARY_SUFFIXES = (".tmp", ".temp", ".pyc", ".pyo", ".swp", "~")
_SELF_REFERENCE_NAMES = frozenset(
    {MANIFEST_FILENAME, "manifest.sha256", "manifest_hash.json"}
)


class ManifestError(ValueError):
    """Raised when the release manifest is incomplete or differs from disk."""


def _attribute_path(paths: Any, names: Iterable[str], default: Path) -> Path:
    for name in names:
        value = getattr(paths, name, None)
        if value is not None:
            return Path(value).resolve()
    return Path(default).resolve()


def _release_relative(paths: Any, path: Path) -> str:
    root = repository_root(paths)
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ManifestError(f"Artifact is outside repository root: {resolved}") from exc


def _is_excluded(paths: Any, path: Path) -> bool:
    release = release_root(paths)
    path = Path(path)
    try:
        relative = path.resolve().relative_to(release)
    except ValueError:
        relative = None
    # Only release-relative directory names are meaningful exclusions.  Test
    # fixtures and clean builds commonly live below an OS ancestor named
    # ``Temp``; inspecting absolute ancestors would incorrectly erase every
    # release artifact from the manifest.
    lowered_parts = (
        {part.casefold() for part in relative.parts[:-1]}
        if relative is not None
        else set()
    )
    if lowered_parts.intersection(_EXCLUDED_DIRECTORY_PARTS):
        return True
    name = path.name.casefold()
    if name.endswith(_TEMPORARY_SUFFIXES):
        return True
    if name.startswith(".") and (".tmp-" in name or ".temp-" in name):
        return True
    if name in VALIDATION_RESULT_FILENAMES:
        return True
    if name.startswith("validation_results.") or name.startswith("validation_report."):
        return True
    if relative is not None and name in _SELF_REFERENCE_NAMES:
        return True
    return False


def _iter_files(paths: Any, roots: Iterable[Path]) -> list[Path]:
    result: set[Path] = set()
    for raw in roots:
        root = Path(raw)
        if root.is_file():
            if not _is_excluded(paths, root):
                result.add(root.resolve())
            continue
        if not root.is_dir():
            continue
        for candidate in root.rglob("*"):
            if candidate.is_file() and not _is_excluded(paths, candidate):
                result.add(candidate.resolve())
    return sorted(result, key=lambda path: _release_relative(paths, path))


def _snapshot(paths: Any, roots: Iterable[Path], *, label: str, nonempty: bool = True) -> dict[str, Any]:
    values = list(roots)
    missing = [_release_relative(paths, path) for path in values if not Path(path).exists()]
    if missing:
        raise ManifestError(f"Required {label} paths are missing: {missing}")
    files = {
        _release_relative(paths, path): sha256_file(path)
        for path in _iter_files(paths, values)
    }
    if nonempty and not files:
        raise ManifestError(f"Required {label} artifact group is empty")
    return {
        "file_count": len(files),
        "tree_sha256": tree_sha256(files),
        "files": dict(sorted(files.items())),
    }


def _record(paths: Any, path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"Required artifact is missing: {_release_relative(paths, path)}")
    return {
        "path": _release_relative(paths, path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def source_paths(paths: Any) -> tuple[Path, ...]:
    release = release_root(paths)
    root = repository_root(paths)
    return (
        _attribute_path(paths, ("config", "configs"), release / "config"),
        _attribute_path(paths, ("schema", "schemas"), release / "schema"),
        release / "src",
        release / "tests",
        release / "pyproject.toml",
        release / "requirements.txt",
        release / "README.md",
        root / "generation" / "README.md",
        root / "generation" / "baseline" / "README.md",
    )


def release_source_snapshot(paths: Any) -> dict[str, Any]:
    return {
        "schema_version": "sqlmend-generation-v1-source-snapshot-v1",
        **_snapshot(paths, source_paths(paths), label="source/config/schema/test"),
    }


def prepared_inputs_directory(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("prepared_inputs", "prepared_inputs_dir"),
        release_root(paths) / "prepared_inputs",
    )


def baseline_release_directory(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("baseline_release", "baseline_dir"),
        repository_root(paths) / "generation" / "baseline",
    )


def baseline_run_path(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("baseline_run", "baseline_run_path"),
        baseline_release_directory(paths) / "runs" / "baseline_closed_book_dev250.jsonl",
    )


def generation_v1_run_path(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("generation_v1_run", "generation_v1_run_path"),
        release_root(paths) / "runs" / "generation_v1_rag_dev250.jsonl",
    )


def formal_run_paths(paths: Any) -> tuple[Path, Path]:
    return baseline_run_path(paths), generation_v1_run_path(paths)


def evaluation_directory(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("evaluation", "evaluation_dir"),
        release_root(paths) / "evaluation",
    )


def reports_directory(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("reports", "reports_dir"),
        release_root(paths) / "reports",
    )


def provenance_directory(paths: Any) -> Path:
    return _attribute_path(
        paths,
        ("provenance", "provenance_dir"),
        release_root(paths) / "provenance",
    )


def frozen_input_paths(paths: Any) -> tuple[Path, Path, Path]:
    root = repository_root(paths)
    safe_queries = _attribute_path(
        paths,
        ("serialized_queries", "safe_queries", "queries"),
        root / "retrieval/retrieval-v1/serialized_queries/dev_250_queries.jsonl",
    )
    final_run = _attribute_path(
        paths,
        ("final_retrieval_run", "retrieval_run", "final_run"),
        root
        / "retrieval/retrieval-v1/runs/"
        "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec",
    )
    corpus = _attribute_path(
        paths,
        ("corpus", "production_corpus"),
        root / "construction/data/processed/corpus.jsonl",
    )
    return safe_queries, final_run, corpus


def manifest_payload_sha256(payload: Mapping[str, Any]) -> str:
    stripped = dict(payload)
    stripped.pop("manifest_payload_sha256", None)
    return canonical_json_sha256(stripped)


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ManifestError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def build_baseline_manifest(paths: Any) -> dict[str, Any]:
    """Build the standalone Closed-Book baseline ledger.

    The implementation/config are intentionally shared with Generation v1 so
    the comparison has one prompt, schema, model identity, and retry policy.
    """

    root = repository_root(paths)
    release = baseline_release_directory(paths)
    run = baseline_run_path(paths)
    prepared_queries = _attribute_path(
        paths,
        ("prepared_queries",),
        release_root(paths) / "prepared_inputs" / "online_queries.jsonl",
    )
    config = _attribute_path(paths, ("config_file",), release_root(paths) / "config/generation.yaml")
    answer_schema = _attribute_path(paths, ("answer_schema",), release_root(paths) / "schema/answer.schema.json")
    model_identity = release_root(paths) / "reports" / "model_identity.json"
    readme = release / "README.md"
    warmup = release / "reports" / "warmup_baseline.json"
    required = (
        run,
        prepared_queries,
        config,
        answer_schema,
        model_identity,
        readme,
        warmup,
    )
    missing = [path.relative_to(root).as_posix() for path in required if not path.is_file()]
    if missing:
        raise ManifestError(f"Required baseline artifacts are missing: {missing}")

    rows = _jsonl_rows(run)
    if len(rows) != 250:
        raise ManifestError(f"Baseline run must contain 250 wrappers; found {len(rows)}")
    query_ids = [row.get("query_id") for row in rows]
    if len(set(query_ids)) != 250 or query_ids != sorted(query_ids):
        raise ManifestError("Baseline query IDs are not unique and sorted")
    if any(row.get("system_id") != "baseline" for row in rows):
        raise ManifestError("Baseline run contains a non-baseline system_id")
    success_count = sum(row.get("status") == "success" for row in rows)
    retry_count = sum(
        int(row.get("generation_provenance", {}).get("retry_count", 0))
        for row in rows
    )

    artifact_paths = list(required)
    migration = provenance_directory(paths) / "system_naming_migration.json"
    if migration.is_file():
        artifact_paths.append(migration)
    files = {
        _release_relative(paths, path): sha256_file(path)
        for path in artifact_paths
    }
    payload: dict[str, Any] = {
        "schema_version": BASELINE_MANIFEST_SCHEMA_VERSION,
        "release": "generation-baseline",
        "system_id": "baseline",
        "display_name": "Generation Baseline",
        "method": "closed_book",
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "query_count": 250,
        "generation_contract_success_count": success_count,
        "generation_contract_failure_count": 250 - success_count,
        "generation_retry_count": retry_count,
        "formal_run": _record(paths, run),
        "warmup": _record(paths, warmup),
        "shared_generation_contract": {
            "owner": "generation/generation-v1",
            "config": _record(paths, config),
            "answer_schema": _record(paths, answer_schema),
            "model_identity": _record(paths, model_identity),
            "prepared_queries": _record(paths, prepared_queries),
        },
        "artifact_file_count": len(files),
        "artifact_tree_sha256": tree_sha256(files),
        "files": dict(sorted(files.items())),
    }
    if migration.is_file():
        payload["system_naming_migration"] = _record(paths, migration)
    payload["manifest_payload_sha256"] = manifest_payload_sha256(payload)
    return payload


def write_baseline_manifest(paths: Any) -> dict[str, Any]:
    payload = build_baseline_manifest(paths)
    write_json(baseline_release_directory(paths) / MANIFEST_FILENAME, payload)
    return payload


def verify_baseline_manifest(paths: Any) -> dict[str, Any]:
    path = baseline_release_directory(paths) / MANIFEST_FILENAME
    if not path.is_file():
        return {"status": "FAIL", "errors": ["baseline manifest is missing"]}
    payload = load_json(path)
    errors: list[str] = []
    if payload.get("schema_version") != BASELINE_MANIFEST_SCHEMA_VERSION:
        errors.append("baseline manifest schema_version differs")
    if payload.get("manifest_payload_sha256") != manifest_payload_sha256(payload):
        errors.append("baseline manifest payload hash differs")
    try:
        rebuilt = build_baseline_manifest(paths)
    except (ManifestError, OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"baseline manifest cannot be rebuilt from disk: {exc}")
        rebuilt = None
    identical = rebuilt == payload if rebuilt is not None else False
    if rebuilt is not None and not identical:
        errors.append("baseline manifest differs from an exact on-disk rebuild")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "rebuilt_fixed_point_identical": identical,
        "artifact_file_count": payload.get("artifact_file_count"),
    }


def build_manifest(paths: Any) -> dict[str, Any]:
    """Rebuild the complete release ledger from bytes already on disk."""

    source = release_source_snapshot(paths)
    prepared = prepared_inputs_directory(paths)
    runs = formal_run_paths(paths)
    evaluation = evaluation_directory(paths)
    reports = reports_directory(paths)
    test_results = reports / "test_results.json"
    frozen_inputs = frozen_input_paths(paths)

    groups = {
        "frozen_inputs": _snapshot(paths, frozen_inputs, label="frozen online input"),
        "source_and_tests": source,
        "prepared_inputs": _snapshot(paths, (prepared,), label="prepared input"),
        "runs": _snapshot(paths, runs, label="formal generation run"),
        "baseline_release": _snapshot(
            paths,
            (baseline_release_directory(paths),),
            label="generation baseline release",
        ),
        "evaluation": _snapshot(paths, (evaluation,), label="offline evaluation"),
        "reports": _snapshot(paths, (reports,), label="release report"),
    }
    provenance = provenance_directory(paths)
    if provenance.is_dir():
        groups["historical_naming_provenance"] = _snapshot(
            paths,
            (provenance,),
            label="historical naming provenance",
        )
    all_files: dict[str, str] = {}
    for group in groups.values():
        for relative, observed in group["files"].items():
            previous = all_files.setdefault(relative, observed)
            if previous != observed:
                raise ManifestError(f"Conflicting hashes collected for {relative}")

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release": "generation-v1",
        "module": "sqlmend-generation-v1",
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "human_verified": False,
        "systems": ["baseline", "generation_v1"],
        "query_count_per_system": 250,
        "formal_answer_count": 500,
        "formal_result_wrapper_count": 500,
        "formal_answer_count_semantics": (
            "formal_result_wrappers_including_explicit_failure_records"
        ),
        "retrieval_top_k": 5,
        "artifact_groups": groups,
        "artifact_file_count": len(all_files),
        "artifact_tree_sha256": tree_sha256(all_files),
        "test_evidence": {
            **_record(paths, test_results),
            "source_snapshot": source,
        },
        "excluded_from_manifest": {
            "self_reference_names": sorted(_SELF_REFERENCE_NAMES),
            "validation_result_names": sorted(VALIDATION_RESULT_FILENAMES),
            "cache_and_temporary_directory_names": sorted(_EXCLUDED_DIRECTORY_PARTS),
            "temporary_suffixes": list(_TEMPORARY_SUFFIXES),
            "atomic_temporary_name_fragments": [".temp-", ".tmp-"],
        },
    }
    manifest["manifest_payload_sha256"] = manifest_payload_sha256(manifest)
    return manifest


def write_manifest(paths: Any, output_path: Path | None = None) -> dict[str, Any]:
    write_baseline_manifest(paths)
    manifest = build_manifest(paths)
    write_json(output_path or release_root(paths) / MANIFEST_FILENAME, manifest)
    return manifest


def _verify_group_files(paths: Any, groups: Mapping[str, Any]) -> list[str]:
    root = repository_root(paths)
    errors: list[str] = []
    for group_name, group in groups.items():
        if not isinstance(group, Mapping):
            errors.append(f"manifest group {group_name} is not an object")
            continue
        files = group.get("files")
        if not isinstance(files, Mapping):
            errors.append(f"manifest group {group_name} has no per-file ledger")
            continue
        if group.get("file_count") != len(files):
            errors.append(f"manifest group {group_name} file_count differs")
        if group.get("tree_sha256") != tree_sha256(dict(files)):
            errors.append(f"manifest group {group_name} tree hash differs")
        for relative, expected in files.items():
            path = root / str(relative)
            if not path.is_file():
                errors.append(f"manifest artifact is missing: {relative}")
            elif sha256_file(path) != expected:
                errors.append(f"manifest artifact hash differs: {relative}")
    return errors


def verify_manifest(paths: Any, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Check payload hash, every file hash, and an exact fixed-point rebuild."""

    path = release_root(paths) / MANIFEST_FILENAME
    if payload is None:
        if not path.is_file():
            return {
                "status": "FAIL",
                "errors": ["release manifest is missing"],
                "rebuilt_fixed_point_identical": False,
            }
        payload = load_json(path)
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return {
            "status": "FAIL",
            "errors": ["release manifest is not a JSON object"],
            "rebuilt_fixed_point_identical": False,
        }
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("manifest schema_version differs")
    if payload.get("manifest_payload_sha256") != manifest_payload_sha256(payload):
        errors.append("manifest payload hash differs")
    groups = payload.get("artifact_groups")
    if not isinstance(groups, Mapping):
        errors.append("manifest has no artifact_groups mapping")
        groups = {}
    errors.extend(_verify_group_files(paths, groups))
    baseline_verification = verify_baseline_manifest(paths)
    if baseline_verification.get("status") != "PASS":
        errors.extend(
            f"baseline: {error}"
            for error in baseline_verification.get("errors", ["verification failed"])
        )

    rebuilt: Mapping[str, Any] | None = None
    try:
        rebuilt = build_manifest(paths)
    except (ManifestError, OSError, ValueError) as exc:
        errors.append(f"manifest cannot be rebuilt from disk: {exc}")
    identical = rebuilt == payload if rebuilt is not None else False
    if rebuilt is not None and not identical:
        errors.append("manifest differs from an exact on-disk rebuild")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "manifest_payload_sha256": payload.get("manifest_payload_sha256"),
        "rebuilt_fixed_point_identical": identical,
        "artifact_file_count": payload.get("artifact_file_count"),
    }


__all__ = [
    "EVALUATION_LABEL",
    "BASELINE_MANIFEST_SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestError",
    "VALIDATION_RESULT_FILENAMES",
    "build_manifest",
    "build_baseline_manifest",
    "baseline_release_directory",
    "baseline_run_path",
    "evaluation_directory",
    "frozen_input_paths",
    "manifest_payload_sha256",
    "prepared_inputs_directory",
    "release_source_snapshot",
    "reports_directory",
    "formal_run_paths",
    "generation_v1_run_path",
    "provenance_directory",
    "source_paths",
    "verify_manifest",
    "verify_baseline_manifest",
    "write_baseline_manifest",
    "write_manifest",
]
