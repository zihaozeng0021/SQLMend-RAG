from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SHARD_DIR = ROOT / "annotation" / "codex" / "work" / "shards"
CORRECTION_LOG = ROOT / "annotation" / "codex" / "provenance" / "case_corrections.json"
ORACLE_ALIASES = {
    "schema_and_rows": "database_state",
    "table_state": "database_state",
    "constraint_behavior": "database_state",
    "storage_type": "scalar",
    "scalar_and_type": "scalar",
}


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def update_field(row: dict[str, Any], field: str, value: Any, changes: list[str]) -> None:
    if row.get(field) != value:
        row[field] = value
        changes.append(field)


def correct_postgresql(row: dict[str, Any], changes: list[str]) -> None:
    if row.get("query_id") != "DEV0037":
        return
    replacements = {
        "sql": "CREATE AGGREGATE array_accum(anyelement) (SFUNC = array_append, STYPE = anyarray, INITCOND = '{}');",
        "user_problem": "After a PostgreSQL 14 upgrade, pg_upgrade reports a user aggregate whose array_append transition still uses the old polymorphic types. How should that aggregate be redeclared?",
        "schema_context": "The aggregate collects scalar inputs into an array through the built-in array_append transition function.",
        "expected_behavior": "Declare array_accum with the post-version-14 compatible polymorphic family.",
        "root_cause": "PostgreSQL 14 moved affected array transition functions from the anyarray family to anycompatible types; a dependent aggregate using the old exact signature blocks pg_upgrade.",
        "reference_fix_sql": "CREATE AGGREGATE array_accum(anycompatible) (SFUNC = array_append, STYPE = anycompatiblearray, INITCOND = '{}');",
        "reference_explanation": "The replacement uses the documented post-change aggregate declaration: anycompatible input and anycompatiblearray state for array_append.",
        "notes": "Documentation-only upgrade case. Release notes establish the version boundary, and the aggregate manual supplies the concrete array_append declaration.",
    }
    for field, value in replacements.items():
        update_field(row, field, value, changes)
    evidence = row["evidence"]
    supporting_id = "smr_postgresql_099d6bf8c81a420d92e03821"
    if not any(item.get("chunk_id") == supporting_id for item in evidence):
        evidence.append(
            {
                "chunk_id": supporting_id,
                "relevance": 2,
                "role": "supporting",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "The PostgreSQL aggregate manual gives array_accum with array_append, anycompatible input, and anycompatiblearray state.",
            }
        )
        changes.append("evidence:+concrete_array_append_declaration")
    row["verification"]["semantic_check"]["expected"] = (
        "array_accum uses anycompatible and anycompatiblearray with array_append."
    )


def correct_mysql(row: dict[str, Any], changes: list[str]) -> None:
    if row.get("query_id") != "DEV0074":
        return
    update_field(
        row,
        "reference_fix_sql",
        "SELECT article_id, GROUP_CONCAT(tag ORDER BY tag DESC SEPARATOR ',') AS tags FROM article_tags GROUP BY article_id;",
        changes,
    )
    update_field(
        row,
        "reference_explanation",
        "Use SEPARATOR for the delimiter and an in-aggregate ORDER BY so the requested sql,rag order is deterministic.",
        changes,
    )
    update_field(
        row,
        "notes",
        "Documentation-only. The reference order is explicit (tag DESC); no MariaDB compatibility claim is made.",
        changes,
    )
    semantic = row["verification"]["semantic_check"]
    if semantic.get("expected") != "article 1 yields sql,rag":
        semantic["expected"] = "article 1 yields sql,rag"
        changes.append("verification.semantic_check.expected")
    details = (
        "Documentation-only: MySQL GROUP_CONCAT grammar supports SEPARATOR and "
        "the in-aggregate tag DESC ordering used to produce sql,rag; no execution is claimed."
    )
    if row["verification"].get("details") != details:
        row["verification"]["details"] = details
        changes.append("verification.details")


def normalize_sqlite(row: dict[str, Any], changes: list[str]) -> None:
    semantic = row.get("verification", {}).get("semantic_check", {})
    if "expected_result" in semantic:
        semantic["expected"] = semantic.pop("expected_result")
        changes.append("verification.semantic_check.expected_result->expected")
    oracle = semantic.get("oracle_type")
    if oracle in ORACLE_ALIASES:
        semantic["oracle_type"] = ORACLE_ALIASES[oracle]
        changes.append(f"verification.semantic_check.oracle_type:{oracle}->{semantic['oracle_type']}")
    if row.get("version_status") == "range" and str(row.get("version") or "").lower().startswith("pre-"):
        row["version_status"] = "legacy"
        changes.append("version_status:range->legacy")
    if row.get("query_id") in {"DEV0107", "DEV0133", "DEV0148"}:
        if row["case_flags"].get("has_documented_error") is not False:
            row["case_flags"]["has_documented_error"] = False
            changes.append("case_flags.has_documented_error:true->false")
    if row.get("query_id") == "DEV0133":
        test_sql = "INSERT INTO contacts(id,email) VALUES(1,NULL);"
        if semantic.get("test_sql") != test_sql:
            semantic["test_sql"] = test_sql
            changes.append("verification.semantic_check.test_sql")
        row["verification"]["details"] = (
            "SQLite 3.45.3 accepted two NULLs under UNIQUE alone. In a fresh database, "
            "the repaired schema was created and semantic_check.test_sql was rejected "
            "with a NOT NULL constraint error."
        )
    if row.get("query_id") == "DEV0148":
        update_field(row, "sql", "INSERT INTO child VALUES(99);", changes)
    if row.get("query_id") == "DEV0116":
        replacements = {
            "sql": "SELECT strftime(created_at,'%F') FROM events;",
            "error_message": None,
            "user_problem": "A SQLite query intended to normalize an ISO timestamp returns NULL after passing created_at before the strftime format string.",
            "expected_behavior": "Return the ISO date 2024-06-15.",
            "root_cause": "SQLite defines strftime() with the format argument first and the time-value second; this query reverses those arguments.",
            "reference_fix_sql": "SELECT strftime('%F',created_at) FROM events;",
            "reference_explanation": "Pass the documented %F ISO-date format first and the stored timestamp second.",
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        evidence = [
            {
                "chunk_id": "smr_sqlite_494a6e8c7d5c7dbf971b9302",
                "relevance": 2,
                "role": "primary",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "SQLite documents strftime with its format argument before its time-value argument.",
            },
            {
                "chunk_id": "smr_sqlite_2446524dc179792251dd24b7",
                "relevance": 2,
                "role": "supporting",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "SQLite's substitution table defines %F as an ISO 8601 date in YYYY-MM-DD form.",
            },
        ]
        if row.get("evidence") != evidence:
            row["evidence"] = evidence
            row["primary_evidence_chunk_id"] = evidence[0]["chunk_id"]
            changes.append("evidence")
        row["case_flags"]["has_documented_error"] = False
        row["verification"]["original"] = {
            "executed": True,
            "outcome": "wrong_result",
            "observed_error": None,
            "observed_result": "NULL",
        }
        row["verification"]["fixed"] = {
            "executed": True,
            "outcome": "expected_result",
            "observed_error": None,
            "observed_result": "2024-06-15",
        }
        row["verification"]["semantic_check"] = {
            "oracle_type": "scalar",
            "expected": "2024-06-15",
            "passed": True,
        }
        row["verification"]["details"] = (
            "SQLite 3.45.3 returned NULL for the reversed arguments and 2024-06-15 "
            "after the documented format/time-value ordering was restored."
        )
        changes.append("verification")
    if row.get("query_id") == "DEV0128":
        replacements = {
            "sql": "SELECT o.order_id FROM orders o CROSS JOIN customers c ORDER BY o.order_id;",
            "seed_data": "INSERT INTO customers VALUES(1,'active'),(2,'inactive'); INSERT INTO orders VALUES(10,1,'paid');",
            "user_problem": "After a second customer was inserted, this report emits the same order twice because it cross joins orders and customers.",
            "expected_behavior": "Return order_id 10 exactly once by matching customer_id.",
            "root_cause": "The CROSS JOIN combines the order with every customer row; no customer_id constraint restricts the Cartesian product.",
            "reference_fix_sql": "SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id=o.customer_id ORDER BY o.order_id;",
            "reference_explanation": "Add the intended customer_id join constraint so each order is paired only with its customer.",
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        row["evidence"][0]["support_summary"] = (
            "SQLite documents that every join begins with a Cartesian product and that the join constraint determines which combinations remain."
        )
        row["case_flags"]["requires_dialect_reasoning"] = False
        row["verification"]["original"] = {
            "executed": True,
            "outcome": "wrong_result",
            "observed_error": None,
            "observed_result": "10\n10",
        }
        row["verification"]["fixed"] = {
            "executed": True,
            "outcome": "expected_result",
            "observed_error": None,
            "observed_result": "10",
        }
        row["verification"]["semantic_check"] = {
            "oracle_type": "ordered_rows",
            "expected": "Exactly one row containing 10",
            "passed": True,
        }
        row["verification"]["details"] = (
            "SQLite 3.45.3 returned order 10 twice from the two-row Cartesian product; "
            "the customer_id join returned it exactly once."
        )
        changes.extend(["evidence.support_summary", "case_flags.requires_dialect_reasoning", "verification"])
    if row.get("query_id") == "DEV0137":
        evidence = [
            {
                "chunk_id": "smr_sqlite_093d670eb31c5bdf7685fb24",
                "relevance": 2,
                "role": "primary",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "SQLite states that text with no interpretable integer prefix casts to integer 0.",
            },
            {
                "chunk_id": "smr_sqlite_dae3c1e9c2b92ebc85610238",
                "relevance": 2,
                "role": "supporting",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "SQLite defines TEXT-to-INTEGER conversion in terms of the longest integer prefix.",
            },
        ]
        if row.get("evidence") != evidence:
            row["evidence"] = evidence
            row["primary_evidence_chunk_id"] = evidence[0]["chunk_id"]
            changes.append("evidence")
    if row.get("query_id") == "DEV0138":
        replacements = {
            "expected_behavior": "Insert the widget with name 'bad' using an automatically assigned integer id.",
            "reference_fix_sql": "INSERT INTO widgets(id,name) VALUES(NULL,'bad');",
            "reference_explanation": "Replace the invalid textual key with NULL so SQLite allocates the INTEGER PRIMARY KEY, while preserving the original name value.",
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        auto_id = "smr_sqlite_06365020b70bb063752f8484"
        if not any(item.get("chunk_id") == auto_id for item in row["evidence"]):
            row["evidence"].append(
                {
                    "chunk_id": auto_id,
                    "relevance": 2,
                    "role": "supporting",
                    "dialect_compatibility": "compatible",
                    "version_compatibility": "compatible",
                    "support_summary": "SQLite documents automatic integer assignment when NULL is inserted into an INTEGER PRIMARY KEY.",
                }
            )
            changes.append("evidence:+automatic_integer_primary_key")
        row["verification"]["fixed"]["observed_result"] = "1|bad"
        row["verification"]["semantic_check"]["expected"] = (
            "One widget with generated integer id 1 and name 'bad'"
        )
        row["verification"]["details"] = (
            "SQLite 3.45.3 rejected the textual key with SQLITE_MISMATCH and inserted "
            "the unchanged name 'bad' with generated id 1 when NULL was supplied."
        )
        changes.append("verification")
    if row.get("query_id") == "DEV0139":
        replacements = {
            "expected_behavior": "Use a schema on SQLite 3.45.3 that rejects nonnumeric storage classes for amount.",
            "reference_fix_sql": (
                "CREATE TABLE prices(amount REAL NOT NULL) STRICT; "
                "INSERT INTO prices VALUES(12.5);"
            ),
            "reference_explanation": (
                "Use a permitted STRICT datatype and NOT NULL, then verify that a numeric value "
                "is stored while nonnumeric text is rejected."
            ),
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        row["verification"]["fixed"] = {
            "executed": True,
            "outcome": "expected_constraint_error",
            "observed_error": "Expected STRICT datatype constraint failure during semantic test",
            "observed_result": None,
        }
        row["verification"]["semantic_check"] = {
            "oracle_type": "database_state",
            "expected": "The valid 12.5 row remains stored as REAL and the abc insert is rejected.",
            "test_sql": "INSERT INTO prices VALUES('abc');",
            "passed": True,
        }
        row["verification"]["details"] = (
            "SQLite 3.45.3 stored abc in the ordinary DECIMAL column. The repaired STRICT table "
            "stored numeric 12.5, then rejected semantic_check.test_sql as a datatype violation."
        )
        changes.append("verification")
    if row.get("query_id") == "DEV0140":
        evidence = [
            {
                "chunk_id": "smr_sqlite_093d670eb31c5bdf7685fb24",
                "relevance": 2,
                "role": "primary",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "SQLite states that an integer prefix above +9223372036854775807 casts exactly to that upper bound.",
            }
        ]
        if row.get("evidence") != evidence:
            row["evidence"] = evidence
            row["primary_evidence_chunk_id"] = evidence[0]["chunk_id"]
            changes.append("evidence")
        row["verification"]["fixed"]["observed_result"] = "9223372036854775808"
        changes.append("verification.fixed.observed_result")
    if row.get("query_id") == "DEV0147":
        update_field(
            row,
            "seed_data",
            "INSERT INTO people VALUES('beta'),('Alpha'),('gamma');",
            changes,
        )
        update_field(
            row,
            "expected_behavior",
            "Order names using an available case-insensitive ASCII collation.",
            changes,
        )
        row["verification"]["fixed"]["observed_result"] = "Alpha\nbeta\ngamma"
        row["verification"]["semantic_check"] = {
            "oracle_type": "ordered_rows",
            "expected": "Alpha, beta, gamma",
            "passed": True,
        }
        row["verification"]["details"] = (
            "SQLite 3.45.3 reproduced the missing-collation error; the built-in NOCASE repair "
            "then returned the three ASCII fixtures in the asserted case-insensitive order."
        )
        changes.append("verification")
    if row.get("query_id") == "DEV0149":
        replacements = {
            "schema_context": "t(a INTEGER); u(b INTEGER); view v AS SELECT a,b FROM t JOIN u",
            "setup_sql": "CREATE TABLE t(a INTEGER); CREATE TABLE u(b INTEGER); CREATE VIEW v AS SELECT a,b FROM t JOIN u;",
            "error_message": "error in view v after rename: ambiguous column name: b",
            "user_problem": "Renaming t.a to b aborts because the dependent view already selects an unqualified b from table u, so the rewritten view would be ambiguous.",
            "expected_behavior": "Rename t.a to a_new and rewrite the dependent view without ambiguity.",
            "root_cause": "SQLite refuses RENAME COLUMN when rewriting a dependent view would make a column reference semantically ambiguous; after renaming t.a to b, the view's unqualified b could refer to either table.",
            "reference_fix_sql": "ALTER TABLE t RENAME COLUMN a TO a_new;",
            "reference_explanation": "Choose a name that does not collide inside the dependent view, allowing SQLite to rewrite its reference unambiguously.",
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        row["evidence"][0]["support_summary"] = (
            "SQLite documents that RENAME COLUMN aborts if rewriting a dependent view would create semantic ambiguity."
        )
        row["verification"]["original"] = {
            "executed": True,
            "outcome": "error",
            "observed_error": "error in view v after rename: ambiguous column name: b",
            "observed_result": None,
        }
        row["verification"]["fixed"] = {
            "executed": True,
            "outcome": "expected_result",
            "observed_error": None,
            "observed_result": "CREATE VIEW v AS SELECT a_new,b FROM t JOIN u",
        }
        row["verification"]["semantic_check"] = {
            "oracle_type": "schema_state",
            "expected": "Column t.a is renamed to a_new and view v references a_new without ambiguity.",
            "passed": True,
        }
        row["verification"]["details"] = (
            "SQLite 3.45.3 reproduced the dependent-view ambiguity and rewrote the view "
            "successfully when the non-conflicting name a_new was used."
        )
        changes.extend(["evidence.support_summary", "verification"])


def correct_mariadb(row: dict[str, Any], changes: list[str]) -> None:
    query_id = row.get("query_id")
    if query_id == "DEV0157":
        update_field(
            row,
            "root_cause",
            "MariaDB's documented SELECT grammar places the row cap in a LIMIT clause after ORDER BY; the SQL Server TOP form is not part of that grammar.",
            changes,
        )
        evidence = [
            {
                "chunk_id": "smr_mariadb_133f3ea8fb1426e6c0a4c77a",
                "relevance": 2,
                "role": "primary",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "MariaDB 11.4.10 gives the full SELECT grammar with ORDER BY followed by LIMIT and no TOP clause.",
            },
            {
                "chunk_id": "smr_mariadb_018f21abba8ed449876e0490",
                "relevance": 2,
                "role": "supporting",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "MariaDB explains that ORDER BY orders results and LIMIT restricts them to a chosen number of rows.",
            },
        ]
        if row.get("evidence") != evidence:
            row["evidence"] = evidence
            row["primary_evidence_chunk_id"] = evidence[0]["chunk_id"]
            changes.append("evidence")
    if query_id == "DEV0193":
        replacements = {
            "sql": "SET time_zone='UTC+8';",
            "user_problem": "A session should use a fixed UTC-plus-eight offset, but an informal UTC+8 value is rejected by MariaDB's time-zone setting.",
            "schema_context": "The application needs a fixed +08:00 session offset; named-zone daylight-saving rules are not required.",
            "setup_sql": None,
            "seed_data": None,
            "expected_behavior": "Set the session time zone to the fixed UTC offset +08:00.",
            "root_cause": "MariaDB documents SYSTEM or a signed UTC offset such as +5:00 for the time_zone variable; the informal UTC+8 spelling is not that offset format.",
            "reference_fix_sql": "SET time_zone='+08:00';",
            "reference_explanation": "Use the documented signed hours-and-minutes UTC-offset form for the session setting.",
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        evidence = [
            {
                "chunk_id": "smr_mariadb_033f948fa4e1bf840dd1fab8",
                "relevance": 2,
                "role": "primary",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "MariaDB documents time_zone values as SYSTEM or signed UTC offsets such as +5:00 and -9:00.",
            }
        ]
        row["evidence"] = evidence
        row["primary_evidence_chunk_id"] = evidence[0]["chunk_id"]
        row["verification"]["semantic_check"]["expected"] = replacements["expected_behavior"]
        row["verification"]["semantic_check"]["observed"] = (
            "The cited MariaDB time-zone documentation directly supports the +08:00 setting form; no runtime result was observed."
        )
        changes.extend(["evidence", "verification.semantic_check"])
    if query_id == "DEV0194":
        replacements = {
            "sql": "SELECT STR_TO_DATE('31.08.2026', '%Y-%m-%d');",
            "error_message": "Incorrect datetime value: '31.08.2026' for function str_to_date",
            "user_problem": "A European dotted date is parsed with a year-first dashed mask, producing an incorrect-value diagnostic instead of 2026-08-31.",
            "schema_context": "Incoming dates use MariaDB's documented European date representation DD.MM.YYYY.",
            "expected_behavior": "The scalar expression returns the date 2026-08-31.",
            "root_cause": "The supplied year-first dashed format does not match the day-first dotted input representation.",
            "reference_fix_sql": "SELECT STR_TO_DATE('31.08.2026', GET_FORMAT(DATE,'EUR'));",
            "reference_explanation": "Use MariaDB's documented European DATE format, which is %d.%m.%Y and is intended for STR_TO_DATE.",
        }
        for field, value in replacements.items():
            update_field(row, field, value, changes)
        evidence = [
            {
                "chunk_id": "smr_mariadb_037863d5c4265e36906f31ef",
                "relevance": 2,
                "role": "primary",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "MariaDB error 1411 identifies an incorrect value supplied to a conversion function.",
            },
            {
                "chunk_id": "smr_mariadb_319a435474c280dbd06220d1",
                "relevance": 2,
                "role": "supporting",
                "dialect_compatibility": "compatible",
                "version_compatibility": "compatible",
                "support_summary": "MariaDB documents GET_FORMAT(DATE,'EUR') as %d.%m.%Y and shows GET_FORMAT used with STR_TO_DATE.",
            },
        ]
        row["evidence"] = evidence
        row["primary_evidence_chunk_id"] = evidence[0]["chunk_id"]
        row["verification"]["semantic_check"]["expected"] = replacements["expected_behavior"]
        row["verification"]["semantic_check"]["observed"] = (
            "The error catalog and GET_FORMAT/STR_TO_DATE example support the diagnostic and the European-format repair; no runtime result was observed."
        )
        changes.extend(["evidence", "verification.semantic_check"])


def main() -> int:
    correction_rows: list[dict[str, Any]] = []
    for filename, corrector in (
        ("postgresql.jsonl", correct_postgresql),
        ("mysql.jsonl", correct_mysql),
        ("sqlite.jsonl", normalize_sqlite),
        ("mariadb.jsonl", correct_mariadb),
    ):
        path = SHARD_DIR / filename
        rows = load(path)
        file_changed = False
        for row in rows:
            changes: list[str] = []
            corrector(row, changes)
            if changes:
                file_changed = True
                correction_rows.append(
                    {"query_id": row["query_id"], "file": filename, "changes": changes}
                )
        if file_changed:
            save(path, rows)
    CORRECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    prior_rows: list[dict[str, Any]] = []
    if CORRECTION_LOG.is_file():
        prior_value = json.loads(CORRECTION_LOG.read_text(encoding="utf-8"))
        prior_rows = list(prior_value.get("records", []))
    merged_by_record: dict[tuple[str, str], dict[str, Any]] = {}
    for item in prior_rows + correction_rows:
        key = (str(item.get("file")), str(item.get("query_id")))
        existing = merged_by_record.setdefault(
            key,
            {"query_id": item.get("query_id"), "file": item.get("file"), "changes": []},
        )
        for change in item.get("changes", []):
            if change not in existing["changes"]:
                existing["changes"].append(change)
    cumulative_rows = sorted(
        merged_by_record.values(), key=lambda item: (str(item["file"]), str(item["query_id"]))
    )
    CORRECTION_LOG.write_text(
        json.dumps(
            {
                "correction_run": "codex-dev-20260828-01-premerge",
                "reason": "Independent schema, evidence, and execution replay findings",
                "records": cumulative_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "corrected_this_run": len(correction_rows),
                "cumulative_corrected_records": len(cumulative_rows),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
