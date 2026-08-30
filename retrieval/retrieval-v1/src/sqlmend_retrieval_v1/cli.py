"""Command-line orchestration for the isolated retrieval-v1 release."""

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
from .experiment import run_evaluation, run_pool_audit
from .io import sha256_file, write_json
from .latency import run_latency_benchmark
from .paths import ProjectPaths
from .pipeline import RUN_FILES, build_all_runs, verify_and_load_online_inputs


COMMANDS = (
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
)
GENERATED_DIRECTORY_NAMES = (
    "runs",
    "evaluation",
    "reports",
    "pool_expansion",
    "serialized_queries",
    "indices",
)
MANIFEST_NAME = "manifest.json"
PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"


class OrchestrationError(RuntimeError):
    """Raised when a command cannot safely complete."""


class ReleaseBlockedError(OrchestrationError):
    """Raised when incomplete judgments block release evaluation."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sqlmend-retrieval-v1")
    parser.add_argument("--root", help="SQLMend-RAG repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit-protected-paths")
    audit.add_argument("--phase", choices=("before", "after"), required=True)
    for command in COMMANDS:
        command_parser = subparsers.add_parser(command)
        if command == "all":
            command_parser.add_argument(
                "--clean",
                action="store_true",
                help="remove only allowlisted retrieval-v1 generated artifacts first",
            )
    return parser


def _release_source_snapshot(paths: Any) -> dict[str, Any]:
    # Delayed so CLI development can proceed independently of manifest work.
    from .manifest import release_source_snapshot

    return release_source_snapshot(paths)


def _same_snapshot(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return (
        first.get("file_count") == second.get("file_count")
        and first.get("tree_sha256") == second.get("tree_sha256")
        and first.get("files") == second.get("files")
    )


def _input_summary(inputs: Any) -> dict[str, Any]:
    candidate_sizes = [len(rows) for rows in inputs.candidates.values()]
    if not candidate_sizes:
        raise OrchestrationError("validated online inputs contain no candidates")
    return {
        "status": PASS,
        "query_count": len(inputs.online_queries),
        "corpus_chunk_count": len(inputs.corpus_by_id),
        "candidate_pair_count": sum(candidate_sizes),
        "minimum_candidates_per_query": min(candidate_sizes),
        "maximum_candidates_per_query": max(candidate_sizes),
        "online_input_fields_only": True,
    }


def verify_inputs_command(paths: ProjectPaths) -> dict[str, Any]:
    inputs = verify_and_load_online_inputs(paths)
    return _input_summary(inputs)


def build_runs_command(paths: ProjectPaths) -> dict[str, Any]:
    inputs = verify_and_load_online_inputs(paths)
    runs = build_all_runs(paths, inputs)
    return {
        "status": PASS,
        "inputs": _input_summary(inputs),
        "systems": {
            system_id: {"row_count": len(rows)}
            for system_id, rows in runs.items()
        },
    }


def check_pool_command(paths: ProjectPaths) -> dict[str, Any]:
    inputs = verify_and_load_online_inputs(paths, write_serialized=False)
    result = run_pool_audit(paths, inputs)
    return dict(result["summary"])


def evaluate_command(paths: ProjectPaths) -> dict[str, Any]:
    inputs = verify_and_load_online_inputs(paths, write_serialized=False)
    result = run_evaluation(paths, inputs)
    return _evaluation_summary(result)


def benchmark_command(paths: ProjectPaths) -> dict[str, Any]:
    inputs = verify_and_load_online_inputs(paths, write_serialized=False)
    return run_latency_benchmark(paths, inputs)


def _evaluation_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "evaluation_integrity_status": result.get("evaluation_integrity_status"),
        "retrieval_quality_status": result.get("retrieval_quality_status"),
    }
    acceptance = result.get("acceptance")
    if isinstance(acceptance, Mapping):
        summary["phase_status"] = acceptance.get("status")
    pool = result.get("pool")
    if isinstance(pool, Mapping):
        summary["Judged@30"] = (
            pool.get("overall", {}).get("Judged@30")
            if isinstance(pool.get("overall"), Mapping)
            else None
        )
    return summary


def run_test_suite(paths: ProjectPaths) -> dict[str, Any]:
    """Run formal tests and bind the evidence to before/after/current source bytes."""

    command = [
        sys.executable,
        "-m",
        "pytest",
        "retrieval/retrieval-v1/tests",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    source_path = str((Path(paths.release) / "src").resolve())
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else source_path + os.pathsep + existing_pythonpath
    )

    before = _release_source_snapshot(paths)
    completed = subprocess.run(
        command,
        cwd=Path(paths.root),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    after = _release_source_snapshot(paths)

    # Write once, then take the requested current snapshot.  Generated reports
    # are outside the source-snapshot roots, so a healthy run remains stable.
    provisional_stable = _same_snapshot(before, after)
    result: dict[str, Any] = {
        "schema_version": "sqlmend-retrieval-v1-test-results-v1",
        "status": PASS if completed.returncode == 0 and provisional_stable else FAIL,
        "returncode": completed.returncode,
        "command": command,
        "command_display": (
            "python -m pytest retrieval/retrieval-v1/tests -q -p no:cacheprovider"
        ),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "source_tree_sha256": before["tree_sha256"],
        "source_tree_sha256_before": before["tree_sha256"],
        "source_tree_sha256_after": after["tree_sha256"],
        "source_file_count": before["file_count"],
        "source_file_count_before": before["file_count"],
        "source_file_count_after": after["file_count"],
        "source_stable_during_tests": provisional_stable,
        "source_snapshots": {"before": before, "after": after, "current": after},
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    output = Path(paths.reports) / "test_results.json"
    write_json(output, result)

    current = _release_source_snapshot(paths)
    stable = _same_snapshot(before, after) and _same_snapshot(after, current)
    result.update(
        {
            "status": PASS if completed.returncode == 0 and stable else FAIL,
            "source_tree_sha256_current": current["tree_sha256"],
            "source_file_count_current": current["file_count"],
            "source_stable_during_tests": stable,
            "source_matches_current": _same_snapshot(after, current),
            "source_snapshots": {
                "before": before,
                "after": after,
                "current": current,
            },
        }
    )
    write_json(output, result)
    return result


def test_command(paths: ProjectPaths) -> dict[str, Any]:
    return run_test_suite(paths)


def _report_sources(paths: ProjectPaths) -> Any:
    from .reporting import ReportSources, SYSTEM_ORDER

    runs = {
        system_id: (
            Path(paths.baseline_run)
            if system_id == SYSTEM_ORDER[0]
            else Path(paths.runs) / RUN_FILES[system_id]
        )
        for system_id in SYSTEM_ORDER
    }
    return ReportSources(
        overall_metrics=Path(paths.evaluation) / "overall_metrics.json",
        slice_metrics=Path(paths.evaluation) / "slice_metrics.csv",
        per_query_metrics=Path(paths.evaluation) / "per_query_metrics.csv",
        runs=runs,
        serialized_queries=Path(paths.serialized_queries),
        corpus=Path(paths.corpus),
        qrels=Path(paths.qrels),
        latency=Path(paths.reports) / "latency.json",
        acceptance=Path(paths.evaluation) / "acceptance.json",
        evaluation_status=Path(paths.evaluation) / "evaluation_status.json",
    )


def report_command(paths: ProjectPaths) -> dict[str, Any]:
    from .reporting import generate_retrieval_v1_report

    output = Path(paths.reports) / "retrieval_v1_report.md"
    report = generate_retrieval_v1_report(
        _report_sources(paths),
        output_path=output,
    )
    return {
        "status": PASS,
        "path": output.relative_to(Path(paths.root)).as_posix(),
        "sha256": sha256_file(output),
        "character_count": len(report),
    }


def finalize_command(paths: ProjectPaths) -> dict[str, Any]:
    """Generate the report and bind all release bytes in manifest.json."""

    from .manifest import MANIFEST_FILENAME, build_manifest

    report = report_command(paths)
    manifest = build_manifest(paths)
    output = Path(paths.release) / MANIFEST_FILENAME
    write_json(output, manifest)
    return {
        "status": PASS,
        "report": report,
        "manifest": {
            "path": output.relative_to(Path(paths.root)).as_posix(),
            "sha256": sha256_file(output),
            "artifact_file_count": manifest.get("artifact_file_count"),
            "artifact_tree_sha256": manifest.get("artifact_tree_sha256"),
        },
    }


def validate_command(paths: ProjectPaths) -> dict[str, Any]:
    from .validation import validate_release

    result = validate_release(paths)
    write_json(Path(paths.reports) / "validation_report.json", result)
    return result


def _ensure_within_release(release: Path, target: Path) -> Path:
    release_resolved = release.resolve()
    target_resolved = target.resolve(strict=False)
    try:
        relative = target_resolved.relative_to(release_resolved)
    except ValueError as exc:
        raise OrchestrationError(
            f"refusing generated-path operation outside retrieval-v1: {target_resolved}"
        ) from exc
    if not relative.parts:
        raise OrchestrationError("refusing generated-path operation on retrieval-v1 root")
    return target_resolved


def clean_generated(paths: ProjectPaths) -> dict[str, Any]:
    """Remove only the fixed retrieval-v1 generated-output allowlist."""

    release = Path(paths.release).resolve()
    targets = [release / name for name in GENERATED_DIRECTORY_NAMES]
    manifest = release / MANIFEST_NAME
    removed: list[str] = []
    absent: list[str] = []
    for target in (*targets, manifest):
        resolved = _ensure_within_release(release, target)
        literal_absolute = Path(os.path.abspath(target))
        relative = target.relative_to(release).as_posix()
        if resolved != literal_absolute:
            raise OrchestrationError(
                f"refusing redirected generated path (symlink or junction): {target}"
            )
        if target.is_symlink():
            raise OrchestrationError(f"refusing to clean symlinked generated path: {target}")
        if not target.exists():
            absent.append(relative)
            continue
        if target == manifest:
            if not target.is_file():
                raise OrchestrationError(f"manifest target is not a file: {target}")
            target.unlink()
        else:
            if not target.is_dir():
                raise OrchestrationError(f"generated directory target is not a directory: {target}")
            # ``resolved`` was checked above; use the literal allowlisted path
            # so no wildcard or environment expansion can broaden deletion.
            shutil.rmtree(target)
        removed.append(relative)
    return {
        "status": PASS,
        "release_root": release.as_posix(),
        "removed": removed,
        "already_absent": absent,
        "protected_release_entries": ["config", "src", "tests", "README.md"],
    }


def clean_command(paths: ProjectPaths) -> dict[str, Any]:
    return clean_generated(paths)


def _assert_audit_passed(result: Mapping[str, Any], phase: str) -> None:
    if result.get("status") != PASS:
        raise OrchestrationError(f"protected-path {phase} audit failed: {result.get('errors')}")
    if phase == "after" and result.get("protected_paths_unchanged") is not True:
        raise OrchestrationError("protected paths changed between before and after audits")


def _audit_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = result.get("snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    return {
        "phase": result.get("phase"),
        "status": result.get("status"),
        "protected_paths_unchanged": result.get("protected_paths_unchanged"),
        "file_count": snapshot.get("file_count"),
        "tree_sha256": snapshot.get("tree_sha256"),
        "errors": result.get("errors", []),
    }


def all_command(paths: ProjectPaths, *, clean: bool = False) -> dict[str, Any]:
    """Run the complete ordered build, evaluation, evidence, and release flow."""

    results: dict[str, Any] = {"status": PASS}
    if clean:
        results["clean"] = clean_generated(paths)
    before = audit_protected_paths(paths, "before")
    results["audit-protected-paths-before"] = _audit_summary(before)
    _assert_audit_passed(before, "before")

    pending_error: BaseException | None = None
    try:
        inputs = verify_and_load_online_inputs(paths)
        results["verify-inputs"] = _input_summary(inputs)

        runs = build_all_runs(paths, inputs)
        results["build-runs"] = {
            "status": PASS,
            "systems": {
                system_id: {"row_count": len(rows)}
                for system_id, rows in runs.items()
            },
        }

        pool = run_pool_audit(paths, inputs)
        pool_summary = dict(pool["summary"])
        results["check-pool"] = pool_summary

        evaluation = run_evaluation(paths, inputs)
        evaluation_summary = _evaluation_summary(evaluation)
        results["evaluate"] = evaluation_summary
        if (
            pool_summary.get("evaluation_integrity_status") != PASS
            or evaluation_summary.get("evaluation_integrity_status") != PASS
        ):
            raise ReleaseBlockedError(
                "formal evaluation is blocked by an incomplete Top-30 judgment pool"
            )

        latency = run_latency_benchmark(paths, inputs)
        results["benchmark"] = {
            "status": PASS,
            "query_count": latency.get("query_count"),
            "systems": latency.get("systems"),
        }

        tests = run_test_suite(paths)
        results["test"] = tests
        if tests.get("status") != PASS:
            raise OrchestrationError(
                "formal tests failed or source bytes changed during testing"
            )
    except BaseException as exc:  # after-audit must still run on interruption/failure.
        pending_error = exc
    finally:
        try:
            after = audit_protected_paths(paths, "after")
            results["audit-protected-paths-after"] = _audit_summary(after)
            _assert_audit_passed(after, "after")
        except BaseException as audit_error:
            if pending_error is None:
                pending_error = audit_error
            else:
                pending_error = OrchestrationError(
                    f"{pending_error}; protected-path after audit also failed: {audit_error}"
                )

    if pending_error is not None:
        raise pending_error

    results["finalize"] = finalize_command(paths)
    validation = validate_command(paths)
    results["validate"] = validation
    evaluation_quality = results["evaluate"].get("retrieval_quality_status")
    successful = validation.get("overall_success") is True and evaluation_quality == PASS
    results["status"] = PASS if successful else FAIL
    return results


def _dispatch(paths: ProjectPaths, args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    handlers: dict[str, Callable[[ProjectPaths], dict[str, Any]]] = {
        "verify-inputs": verify_inputs_command,
        "build-runs": build_runs_command,
        "check-pool": check_pool_command,
        "evaluate": evaluate_command,
        "benchmark": benchmark_command,
        "test": test_command,
        "report": report_command,
        "finalize": finalize_command,
        "validate": validate_command,
        "clean": clean_command,
    }
    if command == "audit-protected-paths":
        return _audit_summary(audit_protected_paths(paths, args.phase))
    if command == "all":
        return all_command(paths, clean=bool(args.clean))
    return handlers[command](paths)


def _failed_result(command: str, result: Mapping[str, Any]) -> bool:
    if result.get("status") in {FAIL, BLOCKED}:
        return True
    if command == "audit-protected-paths":
        phase = result.get("phase")
        return phase == "after" and result.get("protected_paths_unchanged") is not True
    if command == "check-pool":
        return result.get("evaluation_integrity_status") != PASS
    if command == "evaluate":
        return (
            result.get("evaluation_integrity_status") != PASS
            or result.get("retrieval_quality_status") != PASS
        )
    if command == "test":
        return result.get("returncode") != 0 or result.get("status") != PASS
    if command == "validate":
        return result.get("overall_success") is not True
    if command == "all":
        return result.get("status") != PASS
    return False


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = ProjectPaths.discover(args.root)
        result = _dispatch(paths, args)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
        return 1 if _failed_result(args.command, result) else 0
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMMANDS",
    "GENERATED_DIRECTORY_NAMES",
    "OrchestrationError",
    "ReleaseBlockedError",
    "all_command",
    "benchmark_command",
    "build_runs_command",
    "check_pool_command",
    "clean_command",
    "clean_generated",
    "evaluate_command",
    "finalize_command",
    "main",
    "report_command",
    "run_test_suite",
    "test_command",
    "validate_command",
    "verify_inputs_command",
]
