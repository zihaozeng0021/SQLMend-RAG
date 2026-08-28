from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from execution_common import execution_input_sha256, sha256_file


ROOT = Path(__file__).resolve().parents[3]
SHARD = ROOT / "annotation" / "codex" / "work" / "shards" / "sqlite.jsonl"
REPLAY = ROOT / "annotation" / "codex" / "provenance" / "sqlite_3_45_3_reverification.jsonl"
ORACLES = ROOT / "annotation" / "codex" / "work" / "semantic_oracles" / "sqlite_3_45_3.jsonl"
REPORT = ROOT / "annotation" / "codex" / "provenance" / "sqlite_execution_promotions.json"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalar_value(side: dict[str, Any]) -> Any:
    rows = side.get("last_result_rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) != 1:
        raise ValueError(f"Scalar oracle requires exactly one row and one column: {rows!r}")
    return rows[0][0]


def state_projection(side: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    snapshot = side.get("database_snapshot") or {}
    schema = snapshot.get("schema") or []
    tables = snapshot.get("tables") or {}
    projected: dict[str, Any] = {}
    for key in template:
        if key == "succeeded":
            projected[key] = bool(side.get("succeeded"))
        elif key == "error":
            projected[key] = {
                "message": side.get("error"),
                "sqlite_errorcode": side.get("sqlite_errorcode"),
                "sqlite_errorname": side.get("sqlite_errorname"),
            }
        elif key == "schema_sql":
            table_sql = [row[2] for row in schema if row[0] == "table"]
            if len(table_sql) != 1:
                raise ValueError(f"schema_sql projection requires exactly one table: {schema!r}")
            projected[key] = table_sql[0]
        elif key == "view_schema_sql":
            view_sql = [row[2] for row in schema if row[0] == "view"]
            if len(view_sql) != 1:
                raise ValueError(f"view_schema_sql projection requires exactly one view: {schema!r}")
            projected[key] = view_sql[0]
        elif key.endswith("_schema_sql"):
            object_name = key[: -len("_schema_sql")]
            matching = [row[2] for row in schema if row[1] == object_name]
            if len(matching) != 1:
                raise ValueError(f"{key} projection cannot resolve object {object_name}: {schema!r}")
            projected[key] = matching[0]
        elif key.endswith("_rows"):
            table_name = key[: -len("_rows")]
            if table_name not in tables:
                raise ValueError(f"{key} projection cannot resolve table {table_name}")
            projected[key] = tables[table_name]
        else:
            raise ValueError(f"Unsupported SQLite state-oracle field: {key}")
    return projected


def semantic_value(side: dict[str, Any], oracle_type: str, template: Any) -> Any:
    if oracle_type == "scalar":
        return scalar_value(side)
    if oracle_type == "ordered_rows":
        return side.get("last_result_rows")
    if oracle_type == "unordered_rows":
        rows = side.get("last_result_rows") or []
        return sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
    if oracle_type == "row_count":
        return len(side.get("last_result_rows") or [])
    if oracle_type in ("database_state", "schema_state"):
        if not isinstance(template, dict):
            raise ValueError(f"{oracle_type} requires an object-shaped oracle")
        return state_projection(side, template)
    raise ValueError(f"Unsupported SQLite oracle type: {oracle_type}")


def main() -> int:
    cases = load_jsonl(SHARD)
    execution_ids = {
        case["query_id"]
        for case in cases
        if case.get("verification", {}).get("method") == "execution"
    }
    replay_by_id = {row["query_id"]: row for row in load_jsonl(REPLAY)}
    oracle_by_id = {row["query_id"]: row for row in load_jsonl(ORACLES)}
    if set(replay_by_id) != execution_ids or set(oracle_by_id) != execution_ids:
        raise ValueError("SQLite replay/oracle/execution ID sets differ")

    promoted: list[dict[str, Any]] = []
    for case in cases:
        query_id = case["query_id"]
        if query_id not in execution_ids:
            continue
        replay = replay_by_id[query_id]
        oracle = oracle_by_id[query_id]
        if replay.get("engine_version") != "3.45.3" or not replay.get("engine_version_matches_claim"):
            raise ValueError(f"{query_id}: replay engine/version mismatch")
        if replay.get("execution_input_sha256") != execution_input_sha256(case):
            raise ValueError(f"{query_id}: replay execution inputs are stale")
        runner_path = ROOT / "annotation" / "codex" / "scripts" / "reverify_sqlite.py"
        common_path = ROOT / "annotation" / "codex" / "scripts" / "execution_common.py"
        if (
            replay.get("runner_sha256") != sha256_file(runner_path)
            or replay.get("execution_common_sha256") != sha256_file(common_path)
        ):
            raise ValueError(f"{query_id}: replay runner provenance is stale")
        if replay.get("status") != "PASS" or replay.get(
            "claimed_error_polarity_matches"
        ) != {"original": True, "fixed": True}:
            raise ValueError(f"{query_id}: replay outcome polarity mismatch")
        oracle_type = oracle.get("oracle_type")
        if oracle_type != case["verification"]["semantic_check"].get("oracle_type"):
            raise ValueError(f"{query_id}: oracle type differs from case")
        if oracle.get("passed") is not True:
            raise ValueError(f"{query_id}: independent semantic review failed")

        expected = oracle.get("expected")
        fixed_actual = semantic_value(replay["fixed"], oracle_type, expected)
        if oracle.get("actual") != fixed_actual:
            raise ValueError(f"{query_id}: oracle actual differs from replay projection")
        if expected != fixed_actual:
            raise ValueError(f"{query_id}: repaired replay does not satisfy exact semantic oracle")

        original = replay["original"]
        try:
            original_actual = semantic_value(original, oracle_type, expected)
        except ValueError:
            original_actual = None
        if original_actual == expected:
            raise ValueError(f"{query_id}: original query already satisfies the repair oracle")

        fixed = replay["fixed"]
        semantic_test_sql = case["verification"]["semantic_check"].get("test_sql")
        promoted_semantic_check = {
            "oracle_type": oracle_type,
            "expected": expected,
            "observed": fixed_actual,
            "passed": True,
        }
        if semantic_test_sql:
            promoted_semantic_check["test_sql"] = semantic_test_sql
        case["verification"] = {
            "method": "execution",
            "status": "passed",
            "engine": "sqlite",
            "engine_version": "3.45.3",
            "original": {
                "executed": True,
                "outcome": "wrong_result" if original.get("succeeded") else "error",
                "observed_error": original.get("error"),
                "observed_result": original_actual,
            },
            "fixed": {
                "executed": True,
                "outcome": "expected_result" if fixed.get("succeeded") else "expected_constraint_error",
                "observed_error": fixed.get("error"),
                "observed_result": fixed_actual,
            },
            "semantic_check": promoted_semantic_check,
            "details": (
                "Independently replayed on Python sqlite3/SQLite 3.45.3. The original outcome "
                "was reproduced, and the repaired result/error/state exactly matched the "
                "machine-readable semantic oracle reviewed for this case."
            ),
        }
        case["notes"] = (
            "Machine-proposed SQLite development annotation; execution and exact semantic-oracle "
            "checks completed on SQLite 3.45.3, and human review remains required."
        )
        if query_id in {"DEV0133", "DEV0148"}:
            case["case_flags"]["has_documented_error"] = False
        promoted.append(
            {
                "query_id": query_id,
                "oracle_type": oracle_type,
                "original_succeeded": bool(original.get("succeeded")),
                "fixed_succeeded": bool(fixed.get("succeeded")),
                "semantic_oracle_passed": True,
                "execution_input_sha256": replay["execution_input_sha256"],
                "runner_sha256": replay["runner_sha256"],
                "execution_common_sha256": replay["execution_common_sha256"],
            }
        )

    with SHARD.open("w", encoding="utf-8", newline="\n") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n")
    REPORT.write_text(
        json.dumps(
            {
                "engine": "Python sqlite3 module",
                "engine_version": "3.45.3",
                "source_replay_path": str(REPLAY.relative_to(ROOT)).replace("\\", "/"),
                "source_replay_sha256": sha256(REPLAY),
                "semantic_oracle_path": str(ORACLES.relative_to(ROOT)).replace("\\", "/"),
                "semantic_oracle_sha256": sha256(ORACLES),
                "promoted_count": len(promoted),
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
