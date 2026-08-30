"""Command-line orchestration for the isolated Generation-v1 release."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from .audit import audit_protected_paths
from .contracts import BASELINE_SYSTEM_ID, GENERATION_V1_SYSTEM_ID
from .inputs import (
    FROZEN_CORPUS_SHA256,
    FROZEN_FINAL_RUN_SHA256,
    FROZEN_SERIALIZED_QUERIES_SHA256,
    load_generation_config,
    prepare_inputs,
)
from .io import load_json, load_jsonl, read_trec_run, sha256_file, write_json
from .ollama import OllamaClient
from .paths import ProjectPaths
from .runner import generate_system


PASS = "PASS"
FAIL = "FAIL"
GENERATED_DIRECTORY_NAMES = ("prepared_inputs", "runs", "evaluation", "reports")
MANIFEST_NAME = "manifest.json"


class OrchestrationError(RuntimeError):
    """Raised when the release workflow cannot safely continue."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sqlmend-generation-v1")
    parser.add_argument("--root", help="SQLMend-RAG repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit-protected-paths")
    audit.add_argument("--phase", choices=("before", "after", "current"), required=True)

    subparsers.add_parser("verify-inputs")
    subparsers.add_parser("inspect-model")
    subparsers.add_parser("prepare")

    generate = subparsers.add_parser("generate")
    generate.add_argument(
        "--system",
        required=True,
        choices=(BASELINE_SYSTEM_ID, "generation-v1"),
    )
    generate.add_argument("--no-resume", action="store_true")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--no-resume", action="store_true")

    for command in ("report", "test", "finalize", "validate", "clean"):
        subparsers.add_parser(command)

    all_parser = subparsers.add_parser("all")
    all_parser.add_argument("--clean", action="store_true")
    return parser


def _system_id(value: str) -> str:
    if value == BASELINE_SYSTEM_ID:
        return BASELINE_SYSTEM_ID
    if value == "generation-v1":
        return GENERATION_V1_SYSTEM_ID
    raise KeyError(value)


def verify_inputs_command(paths: ProjectPaths) -> dict[str, Any]:
    """Verify only the three permitted online files; do not materialize output."""

    config = load_generation_config(paths.config_file)
    expected = {
        paths.frozen_serialized_queries: FROZEN_SERIALIZED_QUERIES_SHA256,
        paths.final_retrieval_run: FROZEN_FINAL_RUN_SHA256,
        paths.corpus: FROZEN_CORPUS_SHA256,
    }
    observed = {path: sha256_file(path) for path in expected}
    mismatches = {
        str(path): {"expected": expected[path], "observed": digest}
        for path, digest in observed.items()
        if digest != expected[path]
    }
    if mismatches:
        raise OrchestrationError(f"frozen online input hash mismatch: {mismatches}")

    query_rows = load_jsonl(paths.frozen_serialized_queries)
    query_ids = [row.get("query_id") for row in query_rows]
    run_rows = read_trec_run(paths.final_retrieval_run)
    if len(query_rows) != config.expected_query_count:
        raise OrchestrationError("safe query count differs from the frozen experiment")
    if len(query_ids) != len(set(query_ids)) or query_ids != sorted(query_ids):
        raise OrchestrationError("safe query IDs are not unique and sorted")
    if len(run_rows) != config.expected_query_count * 30:
        raise OrchestrationError("Retrieval-v1 Final run is not 250 x 30")
    return {
        "status": PASS,
        "query_count": len(query_rows),
        "final_run_row_count": len(run_rows),
        "top_k": config.top_k,
        "online_paths_only": True,
        "hashes": {
            path.relative_to(paths.root).as_posix(): digest
            for path, digest in observed.items()
        },
    }


def inspect_model_command(paths: ProjectPaths) -> dict[str, Any]:
    config = load_generation_config(paths.config_file)
    client = OllamaClient(config.base_url, timeout_seconds=config.timeout_seconds)
    identity = client.preflight(config.model_tag, config.model_digest)
    result = {
        "schema_version": "sqlmend-generation-model-identity-v1",
        "status": PASS,
        "model_tag": identity.model_tag,
        "model_digest": identity.model_digest,
        "ollama_version": identity.ollama_version,
        "thinking": config.think,
        "thinking_disabled": config.think is False,
        "options": config.options.to_dict(),
        "config_sha256": sha256_file(paths.config_file),
    }
    output = paths.release / "reports" / "model_identity.json"
    write_json(output, result)
    return result


def prepare_command(paths: ProjectPaths) -> dict[str, Any]:
    return {"status": PASS, **prepare_inputs(paths)}


def _warmup_model(paths: ProjectPaths, client: OllamaClient, system_id: str) -> dict[str, Any]:
    """Load the exact model before formal latency measurement."""

    config = load_generation_config(paths.config_file)
    identity = client.preflight(config.model_tag, config.model_digest)
    response = client.chat(
        messages=(
            {
                "role": "system",
                "content": (
                    "Return only a JSON object with exactly one boolean key named ready. "
                    "Do not use Markdown or code fences."
                ),
            },
            {"role": "user", "content": "Set ready to true."},
        ),
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["ready"],
            "properties": {"ready": {"type": "boolean"}},
        },
        model_tag=config.model_tag,
        think=config.think,
        options=config.options.to_dict(),
    )
    result = {
        "schema_version": "sqlmend-generation-warmup-v1",
        "status": PASS,
        "system_id": system_id,
        "excluded_from_formal_latency": True,
        "model_tag": identity.model_tag,
        "model_digest": identity.model_digest,
        "ollama_version": identity.ollama_version,
        "wall_ms": response.wall_ms,
        "ollama_total_ms": response.ollama_total_ms,
        "load_ms": response.load_ms,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "raw_response_sha256": response.raw_response_sha256,
    }
    report_dir = (
        paths.baseline_reports
        if system_id == BASELINE_SYSTEM_ID
        else paths.release / "reports"
    )
    write_json(report_dir / f"warmup_{system_id}.json", result)
    return result


def generate_command(
    paths: ProjectPaths,
    system_id: str,
    *,
    resume: bool = True,
    client: Any = None,
) -> dict[str, Any]:
    config = load_generation_config(paths.config_file)
    active = client or OllamaClient(config.base_url, timeout_seconds=config.timeout_seconds)
    warmup = _warmup_model(paths, active, system_id)
    summary = generate_system(paths, system_id, client=active, resume=resume)
    return {
        "status": PASS,
        "status_scope": "generation_command_completed",
        "warmup": warmup,
        "generation": summary,
    }


def evaluate_command(paths: ProjectPaths, *, resume: bool = True) -> dict[str, Any]:
    from .evaluation import run_offline_evaluation

    overall = run_offline_evaluation(paths, resume=resume)
    return {
        "status": PASS,
        "status_scope": "offline_evaluation_completed",
        "query_count": overall.get("query_count"),
        "formal_answer_count": overall.get("formal_answer_count"),
        "formal_result_wrapper_count": overall.get("formal_result_wrapper_count"),
        "systems": overall.get("systems"),
        "paired": overall.get("paired"),
        "acceptance": overall.get("acceptance"),
    }


def report_command(paths: ProjectPaths) -> dict[str, Any]:
    from .evaluation import OfflineEvaluationPaths
    from .reporting import write_generation_report

    resolved = OfflineEvaluationPaths.resolve(paths)
    overall = load_json(resolved.overall)
    rows = load_jsonl(resolved.per_query)
    output = write_generation_report(resolved, overall, rows)
    return {
        "status": PASS,
        "path": output.relative_to(paths.root).as_posix(),
        "sha256": sha256_file(output),
    }


def _same_snapshot(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return (
        first.get("file_count") == second.get("file_count")
        and first.get("tree_sha256") == second.get("tree_sha256")
        and first.get("files") == second.get("files")
    )


def run_test_suite(paths: ProjectPaths) -> dict[str, Any]:
    from .manifest import release_source_snapshot

    before = release_source_snapshot(paths)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "generation/generation-v1/tests",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    source_path = str((paths.release / "src").resolve())
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else source_path + os.pathsep + existing_pythonpath
    )
    completed = subprocess.run(
        command,
        cwd=paths.root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    after = release_source_snapshot(paths)
    stable = _same_snapshot(before, after)
    result: dict[str, Any] = {
        "schema_version": "sqlmend-generation-test-results-v1",
        "status": PASS if completed.returncode == 0 and stable else FAIL,
        "returncode": completed.returncode,
        "command": command,
        "python_executable": sys.executable,
        "python_version": sys.version,
        "source_tree_sha256": before["tree_sha256"],
        "source_tree_sha256_before": before["tree_sha256"],
        "source_tree_sha256_after": after["tree_sha256"],
        "source_file_count": before["file_count"],
        "source_stable_during_tests": stable,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    output = paths.release / "reports" / "test_results.json"
    write_json(output, result)
    current = release_source_snapshot(paths)
    stable = stable and _same_snapshot(after, current)
    result.update(
        {
            "status": PASS if completed.returncode == 0 and stable else FAIL,
            "source_tree_sha256_current": current["tree_sha256"],
            "source_file_count_current": current["file_count"],
            "source_stable_during_tests": stable,
        }
    )
    write_json(output, result)
    return result


def test_command(paths: ProjectPaths) -> dict[str, Any]:
    return run_test_suite(paths)


def finalize_command(paths: ProjectPaths) -> dict[str, Any]:
    from .manifest import write_manifest

    report = report_command(paths)
    manifest = write_manifest(paths)
    output = paths.release / MANIFEST_NAME
    return {
        "status": PASS,
        "report": report,
        "manifest": {
            "path": output.relative_to(paths.root).as_posix(),
            "sha256": sha256_file(output),
            "artifact_file_count": manifest.get("artifact_file_count"),
            "artifact_tree_sha256": manifest.get("artifact_tree_sha256"),
        },
    }


def validate_command(paths: ProjectPaths) -> dict[str, Any]:
    from .validation import write_validation_report

    result = write_validation_report(paths)
    return {"status_scope": "engineering_validation", **result}


def _ensure_within_release(release: Path, target: Path) -> Path:
    release_resolved = release.resolve()
    target_resolved = target.resolve(strict=False)
    try:
        relative = target_resolved.relative_to(release_resolved)
    except ValueError as exc:
        raise OrchestrationError(f"refusing clean outside generated release: {target}") from exc
    if not relative.parts:
        raise OrchestrationError("refusing to clean a release root")
    return target_resolved


def clean_generated(paths: ProjectPaths) -> dict[str, Any]:
    release = paths.release.resolve()
    baseline = paths.baseline.resolve()
    targets = [(release, release / name) for name in GENERATED_DIRECTORY_NAMES]
    targets.append((release, release / MANIFEST_NAME))
    # Baseline contains generated outputs but its README is protected.
    targets.extend(
        (
            (baseline, baseline / "runs"),
            (baseline, baseline / "reports"),
            (baseline, baseline / MANIFEST_NAME),
        )
    )
    removed: list[str] = []
    absent: list[str] = []
    for owner, target in targets:
        resolved = _ensure_within_release(owner, target)
        literal = Path(os.path.abspath(target))
        relative = target.relative_to(paths.root.resolve()).as_posix()
        if resolved != literal or target.is_symlink():
            raise OrchestrationError(f"refusing redirected clean target: {target}")
        if not target.exists():
            absent.append(relative)
            continue
        if target.name == MANIFEST_NAME:
            if not target.is_file():
                raise OrchestrationError("manifest clean target is not a file")
            target.unlink()
        else:
            if not target.is_dir():
                raise OrchestrationError(f"generated clean target is not a directory: {target}")
            shutil.rmtree(target)
        removed.append(relative)
    return {
        "status": PASS,
        "release_root": release.as_posix(),
        "removed": removed,
        "already_absent": absent,
        "protected_release_entries": [
            "config",
            "schema",
            "src",
            "tests",
            "README.md",
            "pyproject.toml",
            "requirements.txt",
        ],
    }


def clean_command(paths: ProjectPaths) -> dict[str, Any]:
    return clean_generated(paths)


def _audit_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = result.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    return {
        "status": result.get("status"),
        "phase": result.get("phase"),
        "protected_paths_unchanged": result.get("protected_paths_unchanged"),
        "file_count": snapshot.get("file_count"),
        "tree_sha256": snapshot.get("tree_sha256"),
        "errors": result.get("errors", []),
    }


def _require_audit(result: Mapping[str, Any], phase: str) -> None:
    if result.get("status") != PASS:
        raise OrchestrationError(f"protected-path {phase} audit failed")
    if phase == "after" and result.get("protected_paths_unchanged") is not True:
        raise OrchestrationError("protected paths differ from the before audit")


def all_command(paths: ProjectPaths, *, clean: bool = False) -> dict[str, Any]:
    results: dict[str, Any] = {"status": PASS}
    if clean:
        results["clean"] = clean_generated(paths)
    before = audit_protected_paths(paths, "before")
    results["audit_before"] = _audit_summary(before)
    _require_audit(before, "before")

    pending: BaseException | None = None
    try:
        results["verify_inputs"] = verify_inputs_command(paths)
        results["inspect_model"] = inspect_model_command(paths)
        results["prepare"] = prepare_command(paths)
        results["generate_baseline"] = generate_command(paths, BASELINE_SYSTEM_ID)
        results["generate_generation_v1"] = generate_command(
            paths, GENERATION_V1_SYSTEM_ID
        )
        results["evaluate"] = evaluate_command(paths)
        tests = test_command(paths)
        results["test"] = tests
        if tests.get("status") != PASS:
            raise OrchestrationError("formal test evidence failed")
    except BaseException as exc:
        pending = exc
    finally:
        try:
            after = audit_protected_paths(paths, "after")
            results["audit_after"] = _audit_summary(after)
            _require_audit(after, "after")
            current = audit_protected_paths(paths, "current")
            results["audit_current"] = _audit_summary(current)
            if current.get("status") != PASS or current.get("protected_paths_unchanged") is not True:
                raise OrchestrationError(
                    "protected paths differ across before, after, and current audits"
                )
        except BaseException as audit_error:
            pending = audit_error if pending is None else OrchestrationError(
                f"{pending}; after audit also failed: {audit_error}"
            )
    if pending is not None:
        raise pending

    results["finalize"] = finalize_command(paths)
    validation = validate_command(paths)
    results["validate"] = validation
    results["status"] = validation.get("status", FAIL)
    results["status_scope"] = "phase10_engineering_validation"
    results["engineering_status"] = validation.get("engineering_status", FAIL)
    results["quality_status"] = validation.get("quality_status", "NOT_EVALUATED")
    results["overall_success"] = validation.get("overall_success") is True
    results["phase_success"] = results["overall_success"]
    return results


def _dispatch(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "audit-protected-paths":
        return _audit_summary(audit_protected_paths(paths, args.phase))
    if args.command == "verify-inputs":
        return verify_inputs_command(paths)
    if args.command == "inspect-model":
        return inspect_model_command(paths)
    if args.command == "prepare":
        return prepare_command(paths)
    if args.command == "generate":
        return generate_command(
            paths,
            _system_id(args.system),
            resume=not args.no_resume,
        )
    if args.command == "evaluate":
        return evaluate_command(paths, resume=not args.no_resume)
    handlers: dict[str, Callable[[ProjectPaths], dict[str, Any]]] = {
        "report": report_command,
        "test": test_command,
        "finalize": finalize_command,
        "validate": validate_command,
        "clean": clean_command,
    }
    if args.command == "all":
        return all_command(paths, clean=bool(args.clean))
    return handlers[args.command](paths)


def _failed_result(command: str, result: Mapping[str, Any]) -> bool:
    if result.get("status") == FAIL:
        return True
    if command == "audit-protected-paths":
        return result.get("phase") == "after" and result.get("protected_paths_unchanged") is not True
    if command == "test":
        return result.get("returncode") != 0
    if command == "validate":
        return result.get("status") != PASS
    if command == "all":
        return result.get("overall_success") is not True
    return False


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = ProjectPaths.discover(args.root)
        result = _dispatch(paths, args)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            error = "interrupted"
        else:
            error = str(exc)
        print(
            json.dumps(
                {"status": FAIL, "command": args.command, "error": error},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if _failed_result(args.command, result) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
