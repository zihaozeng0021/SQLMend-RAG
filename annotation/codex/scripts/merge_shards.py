from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ANNOTATION = ROOT / "annotation" / "codex"
SHARD_DIR = ANNOTATION / "work" / "shards"
SHARDS = (
    ("postgresql", "postgresql.jsonl", 1, 50),
    ("mysql", "mysql.jsonl", 51, 100),
    ("sqlite", "sqlite.jsonl", 101, 150),
    ("mariadb", "mariadb.jsonl", 151, 200),
    ("duckdb", "duckdb.jsonl", 201, 250),
)
ORACLE_ALIASES = {
    "schema_and_rows": "database_state",
    "table_state": "database_state",
    "constraint_behavior": "database_state",
    "storage_type": "scalar",
    "scalar_and_type": "scalar",
}


def load(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def normalize_case(row: dict) -> list[str]:
    """Normalize known shard aliases into the frozen canonical schema."""
    changes: list[str] = []
    semantic = row.get("verification", {}).get("semantic_check", {})
    if "expected_result" in semantic:
        if "expected" in semantic and semantic["expected"] != semantic["expected_result"]:
            raise ValueError(f"{row.get('query_id')}: conflicting semantic expectations")
        semantic["expected"] = semantic.pop("expected_result")
        changes.append("semantic_check.expected_result -> expected")
    oracle = semantic.get("oracle_type")
    if oracle in ORACLE_ALIASES:
        semantic["oracle_type"] = ORACLE_ALIASES[oracle]
        changes.append(f"semantic oracle {oracle} -> {semantic['oracle_type']}")
    if row.get("version_status") == "range" and (
        row.get("version_min") is None or row.get("version_max") is None
    ):
        if str(row.get("version") or "").lower().startswith("pre-"):
            row["version_status"] = "legacy"
            changes.append("open-ended pre-version range -> legacy")
        else:
            raise ValueError(
                f"{row.get('query_id')}: range status requires both version boundaries"
            )
    return changes


def main() -> int:
    merged: list[dict] = []
    normalization_rows: list[dict] = []
    for dialect, filename, first, last in SHARDS:
        path = SHARD_DIR / filename
        rows = load(path)
        expected_ids = [f"DEV{index:04d}" for index in range(first, last + 1)]
        actual_ids = [row.get("query_id") for row in rows]
        if len(rows) != 50:
            raise ValueError(f"{filename}: expected 50 records, observed {len(rows)}")
        if actual_ids != expected_ids:
            raise ValueError(f"{filename}: IDs must be ordered exactly {expected_ids[0]}..{expected_ids[-1]}")
        if any(row.get("dialect") != dialect for row in rows):
            raise ValueError(f"{filename}: contains a non-{dialect} record")
        for row in rows:
            changes = normalize_case(row)
            if changes:
                normalization_rows.append({"query_id": row["query_id"], "changes": changes})
        merged.extend(rows)
    output = ANNOTATION / "dev_250.jsonl"
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for row in merged:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    normalization_path = ANNOTATION / "provenance" / "normalization_report.json"
    normalization_path.write_text(
        json.dumps(
            {
                "normalization_count": len(normalization_rows),
                "records": normalization_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} records to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
