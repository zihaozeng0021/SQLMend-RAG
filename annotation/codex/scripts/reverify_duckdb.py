from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable

import duckdb

from execution_common import execution_input_sha256, sha256_file


REPLAY_RANDOM_SEED = 0.5


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc


def snapshot(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    tables: dict[str, list[list[Any]]] = {}
    try:
        table_rows = connection.execute(
            "SELECT table_catalog, table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_catalog, table_schema, table_name"
        ).fetchall()
        column_rows = connection.execute(
            "SELECT table_catalog, table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_catalog, table_schema, table_name, ordinal_position"
        ).fetchall()
    except Exception as exc:  # DuckDB exposes several exception subclasses.
        return {"snapshot_error": str(exc), "tables": {}}
    for catalog, schema, name in table_rows:
        quoted_parts = [
            '"' + str(part).replace('"', '""') + '"' for part in (catalog, schema, name)
        ]
        qualified = ".".join(quoted_parts)
        key = ".".join(str(part) for part in (catalog, schema, name))
        try:
            rows = connection.execute(f"SELECT * FROM {qualified}").fetchall()
            tables[key] = [list(row) for row in rows]
        except Exception as exc:
            tables[key] = [[f"snapshot_error: {exc}"]]
    return {
        "schema_objects": [list(row) for row in table_rows],
        "columns": [list(row) for row in column_rows],
        "tables": tables,
    }


def run_side(case: dict[str, Any], sql: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sqlmendrag_duckdb_replay_") as temp_dir:
        attached_path = (Path(temp_dir) / "new_db.db").as_posix().replace("'", "''")
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(f"SELECT setseed({REPLAY_RANDOM_SEED})")
            for setup in (case.get("setup_sql"), case.get("seed_data")):
                if setup and str(setup).strip():
                    isolated_setup = str(setup).replace("'new_db.db'", f"'{attached_path}'")
                    connection.execute(isolated_setup)
            try:
                cursor = connection.execute(sql)
                rows = cursor.fetchall() if cursor.description else None
                return {
                    "attempted": True,
                    "succeeded": True,
                    "error": None,
                    "result_rows": [list(row) for row in rows] if rows is not None else None,
                    "database_snapshot": snapshot(connection),
                }
            except Exception as exc:
                return {
                    "attempted": True,
                    "succeeded": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "database_snapshot": snapshot(connection),
                }
        finally:
            connection.close()


def in_runtime_scope(case: dict[str, Any]) -> bool:
    return case.get("dialect") == "duckdb" and case.get("version") == "1.5" and case.get("version_status") == "current"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="annotation/codex/work/shards/duckdb.jsonl")
    parser.add_argument(
        "--output", default="annotation/codex/provenance/duckdb_1_5_5_reverification.jsonl"
    )
    args = parser.parse_args()
    cases = list(iter_jsonl(Path(args.input)))
    results: list[dict[str, Any]] = []
    runner_path = Path(__file__).resolve()
    common_path = runner_path.with_name("execution_common.py")
    for case in cases:
        if not in_runtime_scope(case):
            continue
        original = run_side(case, str(case["sql"]))
        fixed = run_side(case, str(case["reference_fix_sql"]))
        results.append(
            {
                "query_id": case["query_id"],
                "execution_input_sha256": execution_input_sha256(case),
                "runner_sha256": sha256_file(runner_path),
                "execution_common_sha256": sha256_file(common_path),
                "engine": "duckdb Python package",
                "engine_version": duckdb.__version__,
                "version_scope_match": True,
                "runtime_settings": {
                    "random_seed": REPLAY_RANDOM_SEED,
                    "database": ":memory:",
                    "attachment_isolation": "temporary_directory",
                },
                "original": original,
                "fixed": fixed,
                "declared_semantic_check": case["verification"]["semantic_check"],
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
            stream.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
    print(
        json.dumps(
            {
                "engine_version": duckdb.__version__,
                "scope_matched": len(results),
                "original_succeeded": sum(row["original"]["succeeded"] for row in results),
                "fixed_succeeded": sum(row["fixed"]["succeeded"] for row in results),
                "fixed_failed_query_ids": [
                    row["query_id"] for row in results if not row["fixed"]["succeeded"]
                ],
                "output": str(output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
