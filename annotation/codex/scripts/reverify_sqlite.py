from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from execution_common import execution_input_sha256, sha256_file


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc


def split_sql(sql: str | None) -> list[str]:
    if not sql or not sql.strip():
        return []
    statements: list[str] = []
    buffer = ""
    for character in sql:
        buffer += character
        if character == ";" and sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip())
    return statements


def execute_statements(connection: sqlite3.Connection, sql: str | None) -> dict[str, Any]:
    last_rows: list[list[Any]] | None = None
    statements = split_sql(sql)
    for statement in statements:
        cursor = connection.execute(statement)
        if cursor.description:
            last_rows = [list(row) for row in cursor.fetchall()]
    return {"statement_count": len(statements), "last_result_rows": last_rows}


def database_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    schema_rows = connection.execute(
        "SELECT type,name,sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    tables: dict[str, list[list[Any]]] = {}
    for object_type, name, _ in schema_rows:
        if object_type == "table":
            quoted = '"' + str(name).replace('"', '""') + '"'
            try:
                tables[str(name)] = [list(row) for row in connection.execute(f"SELECT * FROM {quoted}")]
            except sqlite3.Error as exc:
                tables[str(name)] = [[f"snapshot_error: {exc}"]]
    return {
        "schema": [list(row) for row in schema_rows],
        "tables": tables,
        "total_changes": connection.total_changes,
    }


def run_side(case: dict[str, Any], sql: str) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    try:
        execute_statements(connection, case.get("setup_sql"))
        execute_statements(connection, case.get("seed_data"))
        connection.commit()
        try:
            statement_result = execute_statements(connection, sql)
            connection.commit()
            return {
                "attempted": True,
                "succeeded": True,
                "error": None,
                **statement_result,
                "database_snapshot": database_snapshot(connection),
            }
        except sqlite3.Error as exc:
            return {
                "attempted": True,
                "succeeded": False,
                "error": str(exc),
                "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None),
                "sqlite_errorname": getattr(exc, "sqlite_errorname", None),
                "database_snapshot": database_snapshot(connection),
            }
    finally:
        connection.close()


def claimed_error(side: dict[str, Any]) -> bool:
    return side.get("observed_error") is not None or "error" in str(side.get("outcome") or "").lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="annotation/codex/work/shards/sqlite.jsonl",
        help="JSONL cases to independently re-execute",
    )
    parser.add_argument(
        "--output",
        default="annotation/codex/provenance/sqlite_3_45_3_reverification.jsonl",
    )
    args = parser.parse_args()
    rows = list(iter_jsonl(Path(args.input)))
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    runner_path = Path(__file__).resolve()
    common_path = runner_path.with_name("execution_common.py")
    for case in rows:
        if case.get("dialect") != "sqlite" or case.get("verification", {}).get("method") != "execution":
            continue
        original = run_side(case, str(case["sql"]))
        fixed_sql = str(case["reference_fix_sql"])
        semantic_test_sql = case.get("verification", {}).get("semantic_check", {}).get("test_sql")
        if semantic_test_sql:
            fixed_sql = fixed_sql.rstrip().rstrip(";") + "; " + str(semantic_test_sql)
        fixed = run_side(case, fixed_sql)
        claimed_original_error = claimed_error(case["verification"]["original"])
        claimed_fixed_error = claimed_error(case["verification"]["fixed"])
        polarity_matches = {
            "original": claimed_original_error == (not original["succeeded"]),
            "fixed": claimed_fixed_error == (not fixed["succeeded"]),
        }
        engine_matches = case["verification"].get("engine_version") == sqlite3.sqlite_version
        status = (
            "PASS"
            if engine_matches and polarity_matches == {"original": True, "fixed": True}
            else "FAIL"
        )
        if status == "FAIL":
            failures.append(str(case["query_id"]))
        results.append(
            {
                "query_id": case["query_id"],
                "execution_input_sha256": execution_input_sha256(case),
                "runner_sha256": sha256_file(runner_path),
                "execution_common_sha256": sha256_file(common_path),
                "engine": "python sqlite3",
                "engine_version": sqlite3.sqlite_version,
                "status": status,
                "engine_version_matches_claim": engine_matches,
                "claimed_error_polarity_matches": polarity_matches,
                "original": original,
                "fixed": fixed,
                "declared_semantic_check": case["verification"].get("semantic_check"),
                "note": (
                    "Raw independent replay. The declared semantic check is copied only for "
                    "traceability; execution promotion depends on the separate reviewed oracle ledger."
                ),
            }
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for row in results:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(
        json.dumps(
            {
                "engine_version": sqlite3.sqlite_version,
                "replayed": len(results),
                "pass": sum(row["status"] == "PASS" for row in results),
                "fail": len(failures),
                "failed_query_ids": failures,
                "output": str(output),
            },
            indent=2,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
