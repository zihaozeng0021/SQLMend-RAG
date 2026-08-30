"""Provenance-preserving post-hoc metadata migration for Phase 10.

This module deliberately does not call Ollama.  It rebinds the historical
``g0``/``g1`` labels to the repository's canonical ``baseline`` and
``generation_v1`` names while keeping answers, failures, model responses,
attempt telemetry, and hashes byte-for-byte represented in the records.

The migration is intentionally separate from the online runner.  It accepts
the hashes of the new configuration and evaluation context from its caller;
it never discovers those values by opening reference or qrels files.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


MIGRATION_SCHEMA_VERSION = "sqlmend-generation-metadata-migration-v1"
LEGACY_BASELINE_SYSTEM_ID = "g0_closed_book"
LEGACY_GENERATION_V1_SYSTEM_ID = "g1_retrieval_v1_rag"
BASELINE_SYSTEM_ID = "baseline"
GENERATION_V1_SYSTEM_ID = "generation_v1"
LEGACY_EXPERIMENT_ID = "phase10_g0_g1_dev250"
CANONICAL_EXPERIMENT_ID = "phase10_baseline_generation_v1_dev250"

LEGACY_TO_CANONICAL = {
    LEGACY_BASELINE_SYSTEM_ID: BASELINE_SYSTEM_ID,
    LEGACY_GENERATION_V1_SYSTEM_ID: GENERATION_V1_SYSTEM_ID,
}
LEGACY_RUN_KEY_TO_CANONICAL = {
    "g0": BASELINE_SYSTEM_ID,
    "g1": GENERATION_V1_SYSTEM_ID,
}

RUN_CHANGED_FIELD_WHITELIST = (
    "experiment_id",
    "system_id",
    "generation_provenance.run_id",
)
JUDGMENT_CHANGED_FIELD_WHITELIST = (
    "assignment.A",
    "assignment.B",
    "counterbalance",
    "decision.<system_id-key>",
    "run_sha256.<system_id-key>",
    "policy_source_config_sha256",
    "evaluation_input_sha256",
    "evaluation_context_sha256",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MigrationError(ValueError):
    """Raised when a metadata migration would lose provenance or overwrite data."""


@dataclass(frozen=True, slots=True)
class MigrationPaths:
    """Legacy inputs and canonical post-migration outputs."""

    baseline_legacy: Path
    generation_v1_legacy: Path
    judgments_legacy: Path
    baseline: Path
    generation_v1: Path
    judgments: Path
    ledger: Path
    repository_root: Path | None = None

    @classmethod
    def from_repository_root(cls, root: str | Path) -> "MigrationPaths":
        root_path = Path(root).expanduser().resolve()
        release = root_path / "generation" / "generation-v1"
        legacy = release / "provenance" / "legacy"
        return cls(
            baseline_legacy=legacy / "g0_closed_book_dev250.jsonl",
            generation_v1_legacy=legacy / "g1_retrieval_v1_rag_dev250.jsonl",
            judgments_legacy=legacy / "judgments_g0_g1.jsonl",
            baseline=root_path / "generation" / "baseline" / "runs" / "baseline_closed_book_dev250.jsonl",
            generation_v1=release / "runs" / "generation_v1_rag_dev250.jsonl",
            judgments=release / "evaluation" / "judgments.jsonl",
            ledger=release / "provenance" / "system_naming_migration.json",
            repository_root=root_path,
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise MigrationError(f"migration input is missing: {path}")
    result: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if not raw.strip():
                raise MigrationError(f"blank JSONL line at {path}:{line_number}")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise MigrationError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise MigrationError(f"JSONL row is not an object at {path}:{line_number}")
            result.append(value)
    if not result:
        raise MigrationError(f"migration input is empty: {path}")
    return result


def _render_jsonl(records: list[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(dict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        + "\n"
        for record in records
    ).encode("utf-8")


def _render_judgment_jsonl(records: list[Mapping[str, Any]]) -> bytes:
    """Match the evaluator's canonical finalized-journal serialization."""

    return "".join(
        json.dumps(
            dict(record),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        + "\n"
        for record in records
    ).encode("utf-8")


def _render_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.migration-", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise MigrationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_evaluation_input_sha256(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise MigrationError("evaluation_input_sha256 must be a non-empty mapping")
    normalized: dict[str, str] = {}
    for key, digest in value.items():
        if not isinstance(key, str) or not key:
            raise MigrationError("evaluation_input_sha256 keys must be non-empty strings")
        normalized[key] = _validate_sha256(digest, f"evaluation_input_sha256[{key}]")
    return dict(sorted(normalized.items()))


def _path_text(path: Path, repository_root: Path | None) -> str:
    resolved = Path(path).resolve()
    if repository_root is not None:
        try:
            return resolved.relative_to(Path(repository_root).resolve()).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


def _copy(value: Any) -> Any:
    return deepcopy(value)


def immutable_projection(record: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    """Return the part of a record forbidden to change during migration.

    ``kind`` is ``run`` or ``judgment``.  Naming and caller-supplied context
    fields are removed from the projection; all answer, failure, attempts,
    response/request/prompt/raw hashes, latency, and model identity fields
    remain covered by the resulting projection hash.
    """

    projected = _copy(dict(record))
    if kind == "run":
        projected.pop("experiment_id", None)
        projected.pop("system_id", None)
        provenance = projected.get("generation_provenance")
        if isinstance(provenance, dict):
            provenance.pop("run_id", None)
        return projected
    if kind == "judgment":
        for field in (
            "assignment",
            "counterbalance",
            "decision",
            "run_sha256",
            "policy_source_config_sha256",
            "evaluation_input_sha256",
            "evaluation_context_sha256",
        ):
            projected.pop(field, None)
        return projected
    raise MigrationError(f"unknown immutable projection kind: {kind!r}")


def _validate_run_rows(
    rows: list[dict[str, Any]], expected_system_id: str, label: str
) -> None:
    query_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if row.get("experiment_id") != LEGACY_EXPERIMENT_ID:
            raise MigrationError(f"{label}:{index} has an unexpected experiment_id")
        if row.get("system_id") != expected_system_id:
            raise MigrationError(f"{label}:{index} has an unexpected system_id")
        query_id = row.get("query_id")
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            raise MigrationError(f"{label}:{index} has a duplicate or invalid query_id")
        query_ids.add(query_id)
        provenance = row.get("generation_provenance")
        if not isinstance(provenance, dict) or provenance.get("run_id") != LEGACY_EXPERIMENT_ID:
            raise MigrationError(f"{label}:{index} has an unexpected generation run_id")


def _migrate_run_rows(
    rows: list[dict[str, Any]], expected_system_id: str, canonical_system_id: str
) -> list[dict[str, Any]]:
    _validate_run_rows(rows, expected_system_id, "run")
    migrated = _copy(rows)
    for row in migrated:
        row["experiment_id"] = CANONICAL_EXPERIMENT_ID
        row["system_id"] = canonical_system_id
        provenance = row["generation_provenance"]
        provenance["run_id"] = CANONICAL_EXPERIMENT_ID
    return migrated


def _validate_judgment_rows(
    rows: list[dict[str, Any]], legacy_run_sha256: Mapping[str, str] | None = None
) -> None:
    for index, row in enumerate(rows, start=1):
        assignment = row.get("assignment")
        decision = row.get("decision")
        run_sha = row.get("run_sha256")
        if not isinstance(assignment, dict) or set(assignment) != {"A", "B"}:
            raise MigrationError(f"judgments:{index} has an invalid assignment")
        if set(assignment.values()) != set(LEGACY_TO_CANONICAL):
            raise MigrationError(f"judgments:{index} assignment is not the legacy pair")
        if not isinstance(decision, dict) or set(decision) != set(LEGACY_RUN_KEY_TO_CANONICAL):
            raise MigrationError(f"judgments:{index} has invalid decision keys")
        if not isinstance(run_sha, dict) or set(run_sha) != set(LEGACY_RUN_KEY_TO_CANONICAL):
            raise MigrationError(f"judgments:{index} has invalid run_sha256 keys")
        if legacy_run_sha256 is not None:
            for old_key, canonical in LEGACY_RUN_KEY_TO_CANONICAL.items():
                if run_sha[old_key] != legacy_run_sha256[canonical]:
                    raise MigrationError(
                        f"judgments:{index} {old_key} run SHA does not match its legacy run"
                    )
        if not isinstance(row.get("prompt_sha256"), str):
            raise MigrationError(f"judgments:{index} is missing prompt_sha256")


def _migrate_judgment_rows(
    rows: list[dict[str, Any]],
    config_sha256: str,
    evaluation_context_sha256: str,
    evaluation_input_sha256: Mapping[str, str],
    legacy_run_sha256: Mapping[str, str],
    canonical_run_sha256: Mapping[str, str],
) -> list[dict[str, Any]]:
    _validate_judgment_rows(rows, legacy_run_sha256)
    migrated = _copy(rows)
    for row in migrated:
        row["assignment"] = {
            slot: LEGACY_TO_CANONICAL[row["assignment"][slot]] for slot in ("A", "B")
        }
        row["decision"] = {
            LEGACY_RUN_KEY_TO_CANONICAL[system_id]: decision
            for system_id, decision in row["decision"].items()
        }
        row["run_sha256"] = {
            LEGACY_RUN_KEY_TO_CANONICAL[system_id]: canonical_run_sha256[
                LEGACY_RUN_KEY_TO_CANONICAL[system_id]
            ]
            for system_id in row["run_sha256"]
        }
        row["counterbalance"] = "odd:baseline=A;even:generation_v1=A"
        row["policy_source_config_sha256"] = config_sha256
        row["evaluation_context_sha256"] = evaluation_context_sha256
        row["evaluation_input_sha256"] = dict(evaluation_input_sha256)
    return migrated


def _assert_judgment_values_preserved(
    legacy: list[dict[str, Any]],
    migrated: list[dict[str, Any]],
    canonical_run_sha256: Mapping[str, str],
) -> None:
    if len(legacy) != len(migrated):
        raise MigrationError("judgment record count changed during migration")
    for index, (old, new) in enumerate(zip(legacy, migrated, strict=True), start=1):
        for old_id, new_id in LEGACY_RUN_KEY_TO_CANONICAL.items():
            if old["decision"][old_id] != new["decision"][new_id]:
                raise MigrationError(f"judgments:{index} decision payload changed")
        for old_id, new_id in LEGACY_RUN_KEY_TO_CANONICAL.items():
            if new["run_sha256"][new_id] != canonical_run_sha256[new_id]:
                raise MigrationError(f"judgments:{index} canonical run SHA binding changed")
        if old.get("prompt_sha256") != new.get("prompt_sha256"):
            raise MigrationError(f"judgments:{index} prompt_sha256 changed")
        if old.get("attempts") != new.get("attempts"):
            raise MigrationError(f"judgments:{index} judge attempts changed")


def _check_expected_source_hashes(
    observed: Mapping[str, str], expected: Mapping[str, str] | None
) -> None:
    if expected is None:
        return
    for label, digest in expected.items():
        if label not in observed:
            raise MigrationError(f"unknown expected source hash label: {label}")
        if observed[label] != _validate_sha256(digest, f"expected {label} source hash"):
            raise MigrationError(f"{label} source hash differs from the expected migration input")


def _existing_ledger(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read migration ledger: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != MIGRATION_SCHEMA_VERSION:
        raise MigrationError(f"invalid migration ledger: {path}")
    return value


def _verify_existing_migration(
    ledger: Mapping[str, Any],
    paths: MigrationPaths,
    source_hashes: Mapping[str, str],
    config_sha256: str,
    evaluation_context_sha256: str,
    evaluation_input_sha256: Mapping[str, str],
) -> dict[str, Any]:
    if ledger.get("config_source_sha256") != config_sha256:
        raise MigrationError("existing migration ledger has a different config source hash")
    if ledger.get("evaluation_context_sha256") != evaluation_context_sha256:
        raise MigrationError("existing migration ledger has a different evaluation context hash")
    if ledger.get("evaluation_input_sha256") != dict(evaluation_input_sha256):
        raise MigrationError("existing migration ledger has different evaluation input hashes")
    binding = ledger.get("run_sha256_binding")
    if not isinstance(binding, dict):
        raise MigrationError("existing migration ledger has no run SHA binding")
    artifacts = ledger.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MigrationError("existing migration ledger has no artifact ledger")
    for label, source_path, destination in (
        ("baseline", paths.baseline_legacy, paths.baseline),
        ("generation_v1", paths.generation_v1_legacy, paths.generation_v1),
        ("judgments", paths.judgments_legacy, paths.judgments),
    ):
        entry = artifacts.get(label)
        if not isinstance(entry, dict):
            raise MigrationError(f"existing migration ledger lacks {label}")
        if entry.get("legacy_sha256") != source_hashes[label]:
            raise MigrationError(f"{label} legacy source was modified after migration")
        if not destination.is_file() or _sha256_file(destination) != entry.get("canonical_sha256"):
            raise MigrationError(f"{label} canonical output was modified or removed")
        if entry.get("legacy_path") != _path_text(source_path, paths.repository_root):
            raise MigrationError(f"{label} legacy path differs from the migration ledger")
        if label in {"baseline", "generation_v1"}:
            bound = binding.get(label)
            if not isinstance(bound, dict):
                raise MigrationError(f"existing migration ledger lacks {label} run SHA binding")
            if bound.get("legacy") != source_hashes[label] or bound.get("canonical") != entry.get("canonical_sha256"):
                raise MigrationError(f"{label} run SHA binding differs from its artifact hashes")
    return dict(ledger)


def _artifact_entry(
    label: str,
    legacy_path: Path,
    canonical_path: Path,
    legacy_bytes: bytes,
    canonical_bytes: bytes,
    legacy_projection: list[dict[str, Any]],
    canonical_projection: list[dict[str, Any]],
    repository_root: Path | None,
) -> dict[str, Any]:
    old_projection_hash = _json_sha256(legacy_projection)
    new_projection_hash = _json_sha256(canonical_projection)
    if old_projection_hash != new_projection_hash:
        raise MigrationError(f"{label} immutable projection changed")
    return {
        "label": label,
        "legacy_path": _path_text(legacy_path, repository_root),
        "canonical_path": _path_text(canonical_path, repository_root),
        "legacy_sha256": _sha256_bytes(legacy_bytes),
        "canonical_sha256": _sha256_bytes(canonical_bytes),
        "legacy_record_count": len(legacy_projection),
        "canonical_record_count": len(canonical_projection),
        "immutable_projection_sha256_legacy": old_projection_hash,
        "immutable_projection_sha256_canonical": new_projection_hash,
        "immutable_projection_equal": True,
    }


def migrate_metadata(
    paths: MigrationPaths,
    *,
    config_source_sha256: str,
    evaluation_context_sha256: str,
    evaluation_input_sha256: Mapping[str, str],
    expected_query_count: int | None = 250,
    expected_legacy_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Migrate existing run/judge metadata without invoking a model.

    The operation is idempotent.  If a ledger already exists, every legacy
    input and canonical output is re-hashed and must match it.  This makes a
    later source tamper or output edit fail closed instead of silently
    producing a second provenance lineage.
    """

    config_hash = _validate_sha256(config_source_sha256, "config_source_sha256")
    context_hash = _validate_sha256(evaluation_context_sha256, "evaluation_context_sha256")
    input_hashes = _validate_evaluation_input_sha256(evaluation_input_sha256)
    if context_hash != _json_sha256(input_hashes):
        raise MigrationError(
            "evaluation_context_sha256 must equal canonical JSON hash of evaluation_input_sha256"
        )
    source_paths = {
        "baseline": Path(paths.baseline_legacy),
        "generation_v1": Path(paths.generation_v1_legacy),
        "judgments": Path(paths.judgments_legacy),
    }
    destination_paths = {
        "baseline": Path(paths.baseline),
        "generation_v1": Path(paths.generation_v1),
        "judgments": Path(paths.judgments),
    }
    for label, source in source_paths.items():
        if not source.is_file():
            raise MigrationError(f"missing {label} migration input: {source}")
    source_hashes = {label: _sha256_file(path) for label, path in source_paths.items()}
    _check_expected_source_hashes(source_hashes, expected_legacy_hashes)
    existing = _existing_ledger(Path(paths.ledger))
    if existing is not None:
        return _verify_existing_migration(
            existing, paths, source_hashes, config_hash, context_hash, input_hashes
        )

    baseline_rows = _read_jsonl(source_paths["baseline"])
    generation_rows = _read_jsonl(source_paths["generation_v1"])
    judgment_rows = _read_jsonl(source_paths["judgments"])
    if expected_query_count is not None:
        for label, records in (
            ("baseline", baseline_rows),
            ("generation_v1", generation_rows),
            ("judgments", judgment_rows),
        ):
            if len(records) != expected_query_count:
                raise MigrationError(
                    f"{label} has {len(records)} records; expected {expected_query_count}"
                )
    if len(baseline_rows) != len(generation_rows):
        raise MigrationError("baseline and generation_v1 run counts differ")
    baseline_ids = [row.get("query_id") for row in baseline_rows]
    generation_ids = [row.get("query_id") for row in generation_rows]
    if baseline_ids != generation_ids:
        raise MigrationError("paired run query order differs")

    migrated_baseline = _migrate_run_rows(
        baseline_rows, LEGACY_BASELINE_SYSTEM_ID, BASELINE_SYSTEM_ID
    )
    migrated_generation = _migrate_run_rows(
        generation_rows, LEGACY_GENERATION_V1_SYSTEM_ID, GENERATION_V1_SYSTEM_ID
    )
    rendered_runs = {
        "baseline": _render_jsonl(migrated_baseline),
        "generation_v1": _render_jsonl(migrated_generation),
    }
    canonical_run_sha256 = {
        label: _sha256_bytes(payload) for label, payload in rendered_runs.items()
    }
    migrated_judgments = _migrate_judgment_rows(
        judgment_rows,
        config_hash,
        context_hash,
        input_hashes,
        {"baseline": source_hashes["baseline"], "generation_v1": source_hashes["generation_v1"]},
        canonical_run_sha256,
    )
    _assert_judgment_values_preserved(
        judgment_rows, migrated_judgments, canonical_run_sha256
    )

    source_projection = {
        "baseline": [immutable_projection(row, kind="run") for row in baseline_rows],
        "generation_v1": [immutable_projection(row, kind="run") for row in generation_rows],
        "judgments": [immutable_projection(row, kind="judgment") for row in judgment_rows],
    }
    destination_projection = {
        "baseline": [immutable_projection(row, kind="run") for row in migrated_baseline],
        "generation_v1": [immutable_projection(row, kind="run") for row in migrated_generation],
        "judgments": [immutable_projection(row, kind="judgment") for row in migrated_judgments],
    }
    rendered = {
        **rendered_runs,
        "judgments": _render_judgment_jsonl(migrated_judgments),
    }
    original_bytes = {
        label: source.read_bytes() for label, source in source_paths.items()
    }
    entries = {
        label: _artifact_entry(
            label,
            source_paths[label],
            destination_paths[label],
            original_bytes[label],
            rendered[label],
            source_projection[label],
            destination_projection[label],
            paths.repository_root,
        )
        for label in ("baseline", "generation_v1", "judgments")
    }
    ledger: dict[str, Any] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration": "phase10_g0_g1_to_baseline_generation_v1",
        "ollama_called": False,
        "legacy_to_canonical_system_ids": dict(LEGACY_TO_CANONICAL),
        "experiment_id": {
            "legacy": LEGACY_EXPERIMENT_ID,
            "canonical": CANONICAL_EXPERIMENT_ID,
        },
        "config_source_sha256": config_hash,
        "evaluation_context_sha256": context_hash,
        "evaluation_input_sha256": input_hashes,
        "run_sha256_binding": {
            label: {
                "legacy": source_hashes[label],
                "canonical": canonical_run_sha256[label],
            }
            for label in ("baseline", "generation_v1")
        },
        "artifacts": entries,
        "changed_field_whitelist": {
            "run": list(RUN_CHANGED_FIELD_WHITELIST),
            "judgment": list(JUDGMENT_CHANGED_FIELD_WHITELIST),
        },
    }

    # Refuse to overwrite any pre-existing canonical artifact with different
    # bytes.  Equal bytes are harmless and make retries idempotent.
    for label, destination in destination_paths.items():
        if destination.is_file() and destination.read_bytes() != rendered[label]:
            raise MigrationError(f"refusing to overwrite differing output: {destination}")

    for label, destination in destination_paths.items():
        if not destination.is_file():
            _atomic_write(destination, rendered[label])
    _atomic_write(Path(paths.ledger), _render_json(ledger))
    return ledger


__all__ = [
    "BASELINE_SYSTEM_ID",
    "CANONICAL_EXPERIMENT_ID",
    "GENERATION_V1_SYSTEM_ID",
    "JUDGMENT_CHANGED_FIELD_WHITELIST",
    "LEGACY_BASELINE_SYSTEM_ID",
    "LEGACY_EXPERIMENT_ID",
    "LEGACY_GENERATION_V1_SYSTEM_ID",
    "LEGACY_TO_CANONICAL",
    "LEGACY_RUN_KEY_TO_CANONICAL",
    "MIGRATION_SCHEMA_VERSION",
    "MigrationError",
    "MigrationPaths",
    "RUN_CHANGED_FIELD_WHITELIST",
    "immutable_projection",
    "migrate_metadata",
]
