from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from execution_common import execution_input_sha256, sha256_file


ROOT = Path(__file__).resolve().parents[3]
SHARD = ROOT / "annotation" / "codex" / "work" / "shards" / "duckdb.jsonl"
REPLAY = ROOT / "annotation" / "codex" / "provenance" / "duckdb_1_5_5_reverification.jsonl"
REPORT = ROOT / "annotation" / "codex" / "provenance" / "duckdb_execution_promotions.json"
ORACLES = ROOT / "annotation" / "codex" / "work" / "semantic_oracles" / "duckdb_1_5_5.jsonl"
PROMOTE_IDS = {
    "DEV0202", "DEV0203", "DEV0204", "DEV0205", "DEV0206", "DEV0207", "DEV0208", "DEV0209",
    "DEV0216", "DEV0217", "DEV0218", "DEV0219",
    "DEV0221", "DEV0223", "DEV0224", "DEV0225", "DEV0226", "DEV0228", "DEV0229",
    "DEV0231", "DEV0232", "DEV0233", "DEV0234", "DEV0236", "DEV0237", "DEV0238", "DEV0239",
    "DEV0241", "DEV0244", "DEV0245", "DEV0247", "DEV0248", "DEV0249",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observed_value(replay_side: dict[str, Any], oracle_type: str) -> Any:
    if oracle_type in ("database_state", "schema_state"):
        return replay_side.get("database_snapshot")
    return replay_side.get("result_rows")


def semantic_value(replay_side: dict[str, Any], oracle_type: str) -> Any:
    if oracle_type in ("database_state", "schema_state"):
        return replay_side.get("database_snapshot")
    rows = replay_side.get("result_rows")
    if oracle_type == "scalar":
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) != 1:
            raise ValueError(f"Scalar oracle requires exactly one row and one column: {rows!r}")
        return rows[0][0]
    if oracle_type == "row_count":
        return len(rows or [])
    return rows


def oracle_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and expected.get("predicate") == "finite_number_range":
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isfinite(float(actual))
            and float(expected["minimum_inclusive"]) <= float(actual)
            and float(actual) < float(expected["maximum_exclusive"])
        )
    return expected == actual


def main() -> int:
    cases = load_jsonl(SHARD)
    replay_by_id = {row["query_id"]: row for row in load_jsonl(REPLAY)}
    oracle_by_id = {row["query_id"]: row for row in load_jsonl(ORACLES)}
    missing = sorted(PROMOTE_IDS - set(replay_by_id))
    if missing:
        raise ValueError(f"Missing replay rows: {missing}")
    if set(oracle_by_id) != PROMOTE_IDS:
        raise ValueError("Semantic-oracle ID set differs from promotion set")
    promoted: list[dict[str, Any]] = []
    for case in cases:
        query_id = case["query_id"]
        if query_id not in PROMOTE_IDS:
            continue
        if case.get("version") != "1.5" or case.get("version_status") != "current":
            raise ValueError(f"{query_id}: execution promotion is outside the 1.5 current range")
        replay = replay_by_id[query_id]
        if replay.get("engine_version") != "1.5.5" or not replay.get("version_scope_match"):
            raise ValueError(f"{query_id}: replay engine/scope mismatch")
        if replay.get("execution_input_sha256") != execution_input_sha256(case):
            raise ValueError(f"{query_id}: replay execution inputs are stale")
        runner_path = ROOT / "annotation" / "codex" / "scripts" / "reverify_duckdb.py"
        common_path = ROOT / "annotation" / "codex" / "scripts" / "execution_common.py"
        if (
            replay.get("runner_sha256") != sha256_file(runner_path)
            or replay.get("execution_common_sha256") != sha256_file(common_path)
        ):
            raise ValueError(f"{query_id}: replay runner provenance is stale")
        if not replay["fixed"].get("succeeded"):
            raise ValueError(f"{query_id}: repaired SQL did not execute")

        fixed_prefix = str(case["reference_fix_sql"]).lstrip().split(None, 1)[0].upper()
        if fixed_prefix in {"CREATE", "ALTER", "DROP"}:
            oracle_type = "schema_state"
        elif fixed_prefix in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
            oracle_type = "database_state"
        else:
            rows = replay["fixed"].get("result_rows")
            oracle_type = "scalar" if rows is not None and len(rows) == 1 and len(rows[0]) == 1 else "ordered_rows"

        original = replay["original"]
        fixed = replay["fixed"]
        oracle = oracle_by_id[query_id]
        if oracle.get("oracle_type") != oracle_type or oracle.get("passed") is not True:
            raise ValueError(f"{query_id}: invalid semantic-oracle review")
        fixed_semantic_value = semantic_value(fixed, oracle_type)
        if oracle.get("actual") != fixed_semantic_value:
            raise ValueError(f"{query_id}: oracle actual differs from replay")
        if not oracle_matches(oracle.get("expected"), fixed_semantic_value):
            raise ValueError(f"{query_id}: repaired replay does not satisfy semantic oracle")
        if original.get("succeeded"):
            try:
                original_semantic_value = semantic_value(original, oracle_type)
            except ValueError:
                original_semantic_value = object()
            if oracle_matches(oracle.get("expected"), original_semantic_value):
                raise ValueError(f"{query_id}: original query already satisfies the repair oracle")
        case["verification"] = {
            "method": "execution",
            "status": "passed",
            "engine": "duckdb Python package",
            "engine_version": "1.5.5",
            "original": {
                "executed": True,
                "outcome": "wrong_result" if original.get("succeeded") else "error",
                "observed_error": original.get("error"),
                "observed_result": (
                    original.get("result_rows")
                    if original.get("succeeded")
                    else original.get("database_snapshot")
                ),
            },
            "fixed": {
                "executed": True,
                "outcome": "expected_result",
                "observed_error": None,
                "observed_result": observed_value(fixed, oracle_type),
            },
            "semantic_check": {
                "oracle_type": oracle_type,
                "expected": oracle["expected"],
                "observed": fixed_semantic_value,
                "passed": True,
            },
            "details": (
                "Independently replayed on DuckDB Python 1.5.5, which lies inside the case's "
                "documented current 1.5.x scope. The original outcome was reproduced, the "
                "repair executed, and its result/schema/database state matched the stated oracle."
            ),
        }
        case["notes"] = (
            "Machine-proposed DuckDB development annotation; execution and semantic-oracle "
            "checks completed on DuckDB 1.5.5, and human review remains required."
        )
        promoted.append(
            {
                "query_id": query_id,
                "oracle_type": oracle_type,
                "original_succeeded": bool(original.get("succeeded")),
                "fixed_succeeded": True,
                "semantic_oracle_passed": True,
                "execution_input_sha256": replay["execution_input_sha256"],
                "runner_sha256": replay["runner_sha256"],
                "execution_common_sha256": replay["execution_common_sha256"],
            }
        )

    if {row["query_id"] for row in promoted} != PROMOTE_IDS:
        raise ValueError("Promoted ID set mismatch")
    with SHARD.open("w", encoding="utf-8", newline="\n") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n")
    REPORT.write_text(
        json.dumps(
            {
                "engine": "duckdb Python package",
                "engine_version": "1.5.5",
                "source_replay_path": str(REPLAY.relative_to(ROOT)).replace("\\", "/"),
                "source_replay_sha256": sha256(REPLAY),
                "semantic_oracle_path": str(ORACLES.relative_to(ROOT)).replace("\\", "/"),
                "semantic_oracle_sha256": sha256(ORACLES),
                "promoted_count": len(promoted),
                "excluded_scope_matched_cases": ["DEV0201", "DEV0210", "DEV0222", "DEV0246"],
                "exclusion_reason": (
                    "Repair could not be executed with required fixture, or execution did not "
                    "materially establish the semantic oracle; these remain documentation_only."
                ),
                "records": promoted,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"promoted": len(promoted), "report": str(REPORT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
