from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


ALLOWED_DIALECTS = ("postgresql", "mysql", "sqlite", "mariadb", "duckdb")
VERSION_STATUSES = ("exact", "range", "current", "legacy", "unknown")
ERROR_CATEGORIES = (
    "syntax_error",
    "dialect_incompatibility",
    "version_incompatibility",
    "function_or_operator_incompatibility",
    "aggregation_or_grouping",
    "join_or_query_logic",
    "null_semantics",
    "type_or_casting",
    "date_time_semantics",
    "schema_or_identifier_issue",
)
COMPATIBILITY_VALUES = ("compatible", "incompatible", "unknown", "not_applicable")
REQUIRED_CASE_FIELDS = (
    "schema_version",
    "query_id",
    "split",
    "dialect",
    "version",
    "version_min",
    "version_max",
    "version_status",
    "sql",
    "error_message",
    "error_code",
    "sqlstate",
    "error_symbol",
    "user_problem",
    "schema_context",
    "setup_sql",
    "seed_data",
    "expected_behavior",
    "error_category",
    "root_cause",
    "reference_fix_sql",
    "reference_explanation",
    "evidence",
    "primary_evidence_chunk_id",
    "case_flags",
    "verification",
    "annotation_source",
    "annotation_status",
    "generation_run_id",
    "notes",
)
FLAG_FIELDS = (
    "requires_dialect_reasoning",
    "requires_version_reasoning",
    "has_documented_error",
    "plausible_but_wrong",
    "compares_mysql_mariadb",
    "is_duckdb_specific",
)
TOKEN_RE = re.compile(r"[a-z0-9_]+")
VERSION_LITERAL_RE = re.compile(r"\b\d+(?:\.\d+){1,2}(?:\.[xX])?\b")
VERSION_PLUS_RE = re.compile(r"\b\d+(?:\.\d+){1,2}\+")
VERSION_CHANGE_RE = re.compile(
    r"\b(beginning|before|after|since|starting|introduced|added|adds|new|brings|"
    r"release|released|became|first|removed|deprecated|"
    r"version|higher|later|earlier|prior|legacy|changed|change|supported|"
    r"available|availability|default)\b",
    re.IGNORECASE,
)
AUDIT_CHECK_FIELDS = (
    "root_cause_matches_problem",
    "reference_fix_addresses_root_cause",
    "evidence_supports_diagnosis",
    "target_dialect_correct",
    "mysql_mariadb_not_conflated",
    "duckdb_claim_supported",
    "version_claim_supported",
    "evidence_ids_resolve",
    "no_evidence_contradiction",
    "reference_fix_target_appropriate",
    "verification_truthful",
)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_pooling_helpers(script_path: Path) -> Any:
    """Load the frozen pooling implementation so hashes/leakage are recomputed, not trusted."""
    spec = importlib.util.spec_from_file_location("sqlmendrag_pooling_validation", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pooling helpers from {script_path}")
    module = importlib.util.module_from_spec(spec)
    script_directory = str(script_path.parent)
    inserted = script_directory not in sys.path
    if inserted:
        sys.path.insert(0, script_directory)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            sys.path.remove(script_directory)
    return module


def normalized_tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def semantic_oracle_matches(expected: Any, observed: Any) -> bool:
    if isinstance(expected, dict) and expected.get("predicate") == "finite_number_range":
        return (
            isinstance(observed, (int, float))
            and not isinstance(observed, bool)
            and math.isfinite(float(observed))
            and float(expected["minimum_inclusive"]) <= float(observed)
            and float(observed) < float(expected["maximum_exclusive"])
        )
    return expected == observed


def evidence_has_version_change_signal(chunk: dict[str, Any]) -> bool:
    if chunk.get("contains_version_or_compatibility"):
        return True
    text = str(chunk.get("text") or "")
    return bool(
        VERSION_PLUS_RE.search(text)
        or (VERSION_LITERAL_RE.search(text) and VERSION_CHANGE_RE.search(text))
    )


def add_check(
    checks: list[dict[str, Any]], name: str, passed: bool, observed: Any, required: Any, failures: list[str] | None = None
) -> None:
    checks.append(
        {
            "check": name,
            "status": "PASS" if passed else "FAIL",
            "observed": observed,
            "required": required,
            "failures": failures or [],
        }
    )


def validate_case_shape(case: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    query_id = str(case.get("query_id") or "<missing>")
    missing = [field for field in REQUIRED_CASE_FIELDS if field not in case]
    if missing:
        failures.append(f"{query_id}: missing fields {missing}")
    if case.get("schema_version") != "1.0.0":
        failures.append(f"{query_id}: schema_version must be 1.0.0")
    if case.get("split") != "dev":
        failures.append(f"{query_id}: split must be dev")
    if case.get("dialect") not in ALLOWED_DIALECTS:
        failures.append(f"{query_id}: invalid dialect {case.get('dialect')}")
    if case.get("version_status") not in VERSION_STATUSES:
        failures.append(f"{query_id}: invalid version_status {case.get('version_status')}")
    if case.get("version_status") == "unknown" and any(
        case.get(field) is not None for field in ("version", "version_min", "version_max")
    ):
        failures.append(f"{query_id}: unknown version requires null version/version_min/version_max")
    if case.get("version_status") == "range" and (
        case.get("version_min") is None or case.get("version_max") is None
    ):
        failures.append(f"{query_id}: range version requires version_min and version_max")
    if case.get("version_status") in ("exact", "current", "legacy") and case.get("version") is None:
        failures.append(f"{query_id}: {case.get('version_status')} version requires version")
    if case.get("error_category") not in ERROR_CATEGORIES:
        failures.append(f"{query_id}: invalid error_category {case.get('error_category')}")
    if not isinstance(case.get("sql"), str) or not case.get("sql", "").strip():
        failures.append(f"{query_id}: sql is empty")
    for field in ("user_problem", "expected_behavior", "root_cause", "reference_fix_sql", "reference_explanation"):
        if not isinstance(case.get(field), str) or not case.get(field, "").strip():
            failures.append(f"{query_id}: {field} is empty")
    if case.get("annotation_source") != "codex_machine_proposed":
        failures.append(f"{query_id}: invalid annotation_source")
    if case.get("annotation_status") != "unverified":
        failures.append(f"{query_id}: annotation_status must be unverified")
    if case.get("generation_run_id") != "codex-dev-20260828-01":
        failures.append(f"{query_id}: unexpected generation_run_id")
    flags = case.get("case_flags")
    if not isinstance(flags, dict):
        failures.append(f"{query_id}: case_flags must be an object")
    else:
        for field in FLAG_FIELDS:
            if not isinstance(flags.get(field), bool):
                failures.append(f"{query_id}: case_flags.{field} must be boolean")
    evidence = case.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        failures.append(f"{query_id}: evidence must be a non-empty list")
    else:
        seen: set[str] = set()
        for item in evidence:
            chunk_id = item.get("chunk_id")
            if not isinstance(chunk_id, str):
                failures.append(f"{query_id}: evidence chunk_id must be a string")
            elif chunk_id in seen:
                failures.append(f"{query_id}: duplicate evidence chunk {chunk_id}")
            seen.add(chunk_id)
            if item.get("relevance") not in (1, 2):
                failures.append(f"{query_id}: final evidence relevance must be 1 or 2")
            if item.get("role") not in ("primary", "supporting", "comparison"):
                failures.append(f"{query_id}: invalid evidence role")
            for compatibility in ("dialect_compatibility", "version_compatibility"):
                if item.get(compatibility) not in COMPATIBILITY_VALUES:
                    failures.append(f"{query_id}: invalid {compatibility}")
        primary = case.get("primary_evidence_chunk_id")
        matching = [item for item in evidence if item.get("chunk_id") == primary]
        if not matching:
            failures.append(f"{query_id}: primary evidence is not in evidence array")
        elif matching[0].get("relevance") != 2 or matching[0].get("role") != "primary":
            failures.append(f"{query_id}: primary evidence must have relevance=2 and role=primary")
    verification = case.get("verification")
    if not isinstance(verification, dict):
        failures.append(f"{query_id}: verification must be an object")
    else:
        method = verification.get("method")
        status = verification.get("status")
        if method not in ("execution", "parser", "documentation_only"):
            failures.append(f"{query_id}: invalid verification method")
        if status not in ("passed", "failed", "not_available"):
            failures.append(f"{query_id}: invalid verification status")
        for side in ("original", "fixed"):
            if not isinstance(verification.get(side), dict):
                failures.append(f"{query_id}: verification.{side} must be an object")
            elif not isinstance(verification[side].get("executed"), bool) or not verification[side].get("outcome"):
                failures.append(f"{query_id}: verification.{side} lacks executed/outcome")
        semantic = verification.get("semantic_check")
        if not isinstance(semantic, dict) or not semantic.get("oracle_type") or "passed" not in semantic:
            failures.append(f"{query_id}: verification.semantic_check is incomplete")
        elif not isinstance(semantic.get("passed"), bool):
            failures.append(f"{query_id}: semantic_check.passed must be boolean")
        if method == "execution":
            if not verification.get("original", {}).get("executed") or not verification.get("fixed", {}).get("executed"):
                failures.append(f"{query_id}: execution requires both original and fixed execution")
            if not verification.get("engine_version"):
                failures.append(f"{query_id}: execution requires an exact engine_version")
            if semantic and semantic.get("oracle_type") in ("parser_only", "documented_behavior", "not_available"):
                failures.append(f"{query_id}: execution needs a semantic execution oracle")
            if semantic and ("expected" not in semantic or "observed" not in semantic):
                failures.append(f"{query_id}: execution semantic oracle needs expected and observed values")
            elif semantic and not semantic_oracle_matches(
                semantic.get("expected"), semantic.get("observed")
            ):
                failures.append(f"{query_id}: observed execution does not satisfy semantic oracle")
            if "observed_result" not in verification.get("fixed", {}):
                failures.append(f"{query_id}: execution repair lacks an observed result/state")
            if status == "passed" and not semantic.get("passed", False):
                failures.append(f"{query_id}: passed execution requires passed semantic check")
            if "documentation-only" in str(case.get("notes") or "").lower():
                failures.append(f"{query_id}: execution case notes contradict verification method")
        if method == "documentation_only":
            if verification.get("original", {}).get("executed") or verification.get("fixed", {}).get("executed"):
                failures.append(f"{query_id}: documentation_only cannot claim execution")
            if semantic and semantic.get("oracle_type") != "documented_behavior":
                failures.append(f"{query_id}: documentation_only needs documented_behavior oracle")
        if status == "passed" and semantic and semantic.get("oracle_type") in ("parser_only", "not_available"):
            failures.append(f"{query_id}: passed verification needs a semantic or documented oracle")
        engine = str(verification.get("engine") or "").lower()
        if case.get("dialect") and str(case.get("dialect")) not in engine:
            failures.append(f"{query_id}: verification engine does not identify target dialect")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    annotation = root / "annotation" / "codex"
    paths = {
        "corpus": root / "construction" / "data" / "processed" / "corpus.jsonl",
        "kb_validation": root / "construction" / "reports" / "validation_report.json",
        "cases": annotation / "dev_250.jsonl",
        "pools": annotation / "candidate_pools.jsonl",
        "qrels": annotation / "qrels_machine_proposed.jsonl",
        "leakage": annotation / "query_source_leakage.jsonl",
        "runs": annotation / "provenance" / "retrieval_runs.jsonl",
        "retrieval_config": annotation / "provenance" / "retrieval_config.json",
        "retrieval_metrics": annotation / "reports" / "retrieval_metrics.json",
        "embedding_model": annotation / "provenance" / "embedding_model.json",
        "top30_refresh": annotation / "provenance" / "top30_blind_refresh.json",
        "annotation_sensitivity": annotation / "reports" / "top30_annotation_sensitivity.json",
    }
    missing_files = [str(path) for path in paths.values() if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(f"Missing required inputs: {missing_files}")

    corpus = list(iter_jsonl(paths["corpus"]))
    corpus_sha256 = sha256_file(paths["corpus"])
    corpus_by_id = {row["chunk_id"]: row for row in corpus}
    cases = list(iter_jsonl(paths["cases"]))
    pools = list(iter_jsonl(paths["pools"]))
    qrels = list(iter_jsonl(paths["qrels"]))
    leakage = list(iter_jsonl(paths["leakage"]))
    retrieval_runs = list(iter_jsonl(paths["runs"]))
    retrieval_config = json.loads(paths["retrieval_config"].read_text(encoding="utf-8"))
    retrieval_metrics = json.loads(paths["retrieval_metrics"].read_text(encoding="utf-8"))
    embedding_model = json.loads(paths["embedding_model"].read_text(encoding="utf-8"))
    top30_refresh = json.loads(paths["top30_refresh"].read_text(encoding="utf-8"))
    annotation_sensitivity = json.loads(
        paths["annotation_sensitivity"].read_text(encoding="utf-8")
    )
    checks: list[dict[str, Any]] = []

    case_schema = json.loads(
        (annotation / "schema" / "dev_case.schema.json").read_text(encoding="utf-8")
    )
    schema_validator = Draft202012Validator(case_schema)
    schema_failures: list[str] = []
    for case in cases:
        query_id = str(case.get("query_id") or "<missing>")
        for error in sorted(schema_validator.iter_errors(case), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.absolute_path) or "<record>"
            schema_failures.append(f"{query_id}: {location}: {error.message}")
    add_check(
        checks,
        "json_schema_validation",
        not schema_failures,
        len(schema_failures),
        0,
        schema_failures[:200],
    )
    artifact_schema_failures: list[str] = []
    for artifact_name, artifact_rows, schema_name in (
        ("candidate_pools", pools, "candidate_pool.schema.json"),
        ("qrels", qrels, "qrel.schema.json"),
        ("query_source_leakage", leakage, "query_source_leakage.schema.json"),
    ):
        artifact_schema = json.loads(
            (annotation / "schema" / schema_name).read_text(encoding="utf-8")
        )
        artifact_validator = Draft202012Validator(artifact_schema)
        for row_number, row in enumerate(artifact_rows, 1):
            for error in sorted(
                artifact_validator.iter_errors(row), key=lambda item: list(item.path)
            ):
                location = ".".join(str(part) for part in error.absolute_path) or "<record>"
                artifact_schema_failures.append(
                    f"{artifact_name}:{row_number}: {location}: {error.message}"
                )
    add_check(
        checks,
        "generated_artifact_schema_validation",
        not artifact_schema_failures,
        len(artifact_schema_failures),
        0,
        artifact_schema_failures[:200],
    )

    kb_report = json.loads(paths["kb_validation"].read_text(encoding="utf-8"))
    add_check(checks, "kb_validation_pass", kb_report.get("status") == "PASS", kb_report.get("status"), "PASS")
    add_check(checks, "exact_record_count", len(cases) == 250, len(cases), 250)
    expected_ids = [f"DEV{index:04d}" for index in range(1, 251)]
    actual_ids = [str(case.get("query_id")) for case in cases]
    add_check(
        checks,
        "exact_query_id_set",
        sorted(actual_ids) == expected_ids and len(set(actual_ids)) == 250,
        {"count": len(actual_ids), "unique": len(set(actual_ids))},
        "DEV0001 through DEV0250 exactly once",
        sorted(set(expected_ids) ^ set(actual_ids)),
    )

    merge_failures: list[str] = []
    merge_script = annotation / "scripts" / "merge_shards.py"
    normalization_report_path = annotation / "provenance" / "normalization_report.json"
    if not merge_script.is_file():
        merge_failures.append("merge_shards.py is missing")
    if not normalization_report_path.is_file():
        merge_failures.append("normalization_report.json is missing")
    if not merge_failures:
        try:
            merge_helpers = load_pooling_helpers(merge_script)
            normalized_shard_rows: list[dict[str, Any]] = []
            expected_normalization_records: list[dict[str, Any]] = []
            for dialect, filename, first, last in merge_helpers.SHARDS:
                shard_path = annotation / "work" / "shards" / filename
                if not shard_path.is_file():
                    merge_failures.append(f"missing shard {filename}")
                    continue
                shard_rows = list(iter_jsonl(shard_path))
                expected_shard_ids = [f"DEV{index:04d}" for index in range(first, last + 1)]
                actual_shard_ids = [row.get("query_id") for row in shard_rows]
                if len(shard_rows) != 50:
                    merge_failures.append(
                        f"{filename}: expected 50 records, observed {len(shard_rows)}"
                    )
                if actual_shard_ids != expected_shard_ids:
                    merge_failures.append(
                        f"{filename}: IDs are not ordered exactly "
                        f"{expected_shard_ids[0]}..{expected_shard_ids[-1]}"
                    )
                if any(row.get("dialect") != dialect for row in shard_rows):
                    merge_failures.append(f"{filename}: contains a non-{dialect} record")
                for source_row in shard_rows:
                    normalized_row = copy.deepcopy(source_row)
                    changes = merge_helpers.normalize_case(normalized_row)
                    if changes:
                        expected_normalization_records.append(
                            {"query_id": normalized_row.get("query_id"), "changes": changes}
                        )
                    normalized_shard_rows.append(normalized_row)
            if normalized_shard_rows != cases:
                first_difference = next(
                    (
                        index
                        for index, (merged_row, case) in enumerate(
                            zip(normalized_shard_rows, cases, strict=False)
                        )
                        if merged_row != case
                    ),
                    min(len(normalized_shard_rows), len(cases)),
                )
                merge_failures.append(
                    "dev_250.jsonl is not the exact ordered normalized shard merge; "
                    f"first differing zero-based row is {first_difference}"
                )
            expected_normalization_report = {
                "normalization_count": len(expected_normalization_records),
                "records": expected_normalization_records,
            }
            saved_normalization_report = json.loads(
                normalization_report_path.read_text(encoding="utf-8")
            )
            if saved_normalization_report != expected_normalization_report:
                merge_failures.append(
                    "normalization_report.json differs from normalize_case results over shards"
                )
        except Exception as exc:
            merge_failures.append(f"cannot recompute normalized shard merge: {exc}")
    add_check(
        checks,
        "exact_ordered_normalized_shard_merge",
        not merge_failures,
        len(merge_failures),
        0,
        merge_failures,
    )

    dialect_counts = Counter(case.get("dialect") for case in cases)
    add_check(
        checks,
        "exactly_50_per_dialect",
        all(dialect_counts[dialect] == 50 for dialect in ALLOWED_DIALECTS) and set(dialect_counts) == set(ALLOWED_DIALECTS),
        dict(dialect_counts),
        {dialect: 50 for dialect in ALLOWED_DIALECTS},
    )
    category_counts = Counter(case.get("error_category") for case in cases)
    add_check(
        checks,
        "error_category_coverage_and_cap",
        set(category_counts) == set(ERROR_CATEGORIES) and max(category_counts.values(), default=0) <= 50,
        dict(category_counts),
        "all ten categories represented; each <=50",
    )

    shape_failures = [failure for case in cases for failure in validate_case_shape(case)]
    add_check(checks, "case_schema_and_semantics", not shape_failures, len(shape_failures), 0, shape_failures[:200])

    evidence_failures: list[str] = []
    for case in cases:
        for evidence in case.get("evidence", []):
            chunk = corpus_by_id.get(evidence.get("chunk_id"))
            if not chunk:
                evidence_failures.append(f"{case.get('query_id')}: unresolved {evidence.get('chunk_id')}")
            elif evidence.get("role") == "primary" and evidence.get("dialect_compatibility") == "compatible" and chunk.get("dialect") != case.get("dialect"):
                evidence_failures.append(
                    f"{case.get('query_id')}: primary compatible evidence dialect {chunk.get('dialect')} != {case.get('dialect')}"
                )
    add_check(checks, "evidence_ids_and_dialects", not evidence_failures, len(evidence_failures), 0, evidence_failures[:200])

    claim_support_failures: list[str] = []
    for case in cases:
        query_id = str(case.get("query_id"))
        flags = case.get("case_flags", {})
        resolved_evidence = [
            (item, corpus_by_id[item["chunk_id"]])
            for item in case.get("evidence", [])
            if item.get("chunk_id") in corpus_by_id
        ]
        direct_target_evidence = [
            (item, chunk)
            for item, chunk in resolved_evidence
            if item.get("relevance") == 2
            and item.get("dialect_compatibility") == "compatible"
            and chunk.get("dialect") == case.get("dialect")
        ]
        if not direct_target_evidence:
            claim_support_failures.append(f"{query_id}: no direct compatible target-dialect evidence")
        if flags.get("requires_dialect_reasoning") and not direct_target_evidence:
            claim_support_failures.append(f"{query_id}: dialect-sensitive flag lacks direct target evidence")
        if flags.get("requires_version_reasoning"):
            if case.get("version_status") == "unknown":
                claim_support_failures.append(f"{query_id}: version-sensitive case has unknown version")
            version_support = any(
                item.get("version_compatibility") in ("compatible", "incompatible")
                and evidence_has_version_change_signal(chunk)
                for item, chunk in direct_target_evidence
            )
            if not version_support:
                claim_support_failures.append(
                    f"{query_id}: version-sensitive flag lacks direct version-change evidence"
                )
        if flags.get("has_documented_error") and not any(
            case.get(field) is not None
            for field in ("error_message", "error_code", "sqlstate", "error_symbol")
        ):
            claim_support_failures.append(
                f"{query_id}: documented-error flag has no error message/code/state/symbol"
            )
        if flags.get("compares_mysql_mariadb"):
            evidence_dialects = {chunk.get("dialect") for _, chunk in resolved_evidence}
            explicit_comparison_source = any(
                item.get("role") == "comparison"
                and "mysql" in str(chunk.get("text") or "").lower()
                and "mariadb" in str(chunk.get("text") or "").lower()
                for item, chunk in resolved_evidence
            )
            if case.get("dialect") not in ("mysql", "mariadb") or not (
                {"mysql", "mariadb"}.issubset(evidence_dialects) or explicit_comparison_source
            ):
                claim_support_failures.append(
                    f"{query_id}: MySQL/MariaDB comparison lacks two-system or explicit comparison evidence"
                )
        if flags.get("is_duckdb_specific"):
            if case.get("dialect") != "duckdb" or not any(
                chunk.get("dialect") == "duckdb" for _, chunk in direct_target_evidence
            ):
                claim_support_failures.append(f"{query_id}: DuckDB-specific flag lacks DuckDB evidence")
    add_check(
        checks,
        "dialect_version_and_error_claim_support",
        not claim_support_failures,
        len(claim_support_failures),
        0,
        claim_support_failures[:200],
    )

    per_dialect_flags: dict[str, dict[str, int]] = {}
    for dialect in ALLOWED_DIALECTS:
        dialect_cases = [case for case in cases if case.get("dialect") == dialect]
        per_dialect_flags[dialect] = {
            field: sum(bool(case.get("case_flags", {}).get(field)) for case in dialect_cases)
            for field in FLAG_FIELDS
        }
    sensitive_ok = all(
        per_dialect_flags[dialect]["requires_dialect_reasoning"] >= 15
        and per_dialect_flags[dialect]["requires_version_reasoning"] >= 10
        for dialect in ALLOWED_DIALECTS
    )
    add_check(
        checks,
        "sensitive_case_targets_per_dialect",
        sensitive_ok,
        per_dialect_flags,
        "each dialect: >=15 dialect-sensitive and >=10 version-sensitive",
    )
    documented_errors = sum(bool(case.get("case_flags", {}).get("has_documented_error")) for case in cases)
    plausible = sum(bool(case.get("case_flags", {}).get("plausible_but_wrong")) for case in cases)
    add_check(checks, "documented_error_minimum", documented_errors >= 30, documented_errors, ">=30")
    add_check(checks, "plausible_but_wrong_minimum", plausible >= 50, plausible, ">=50")
    verifiable_cases = [
        case
        for case in cases
        if case.get("verification", {}).get("status") in ("passed", "failed")
    ]
    verification_pass_rate = sum(
        case.get("verification", {}).get("status") == "passed" for case in verifiable_cases
    ) / max(1, len(verifiable_cases))
    add_check(
        checks,
        "selected_verification_method_pass_rate",
        verification_pass_rate >= 0.90,
        round(verification_pass_rate, 6),
        ">=0.90 among passed/failed selected-method checks",
    )

    exact_pairs = Counter(
        (str(case.get("sql") or "").strip().lower(), str(case.get("user_problem") or "").strip().lower())
        for case in cases
    )
    duplicate_pairs = [pair for pair, count in exact_pairs.items() if count > 1]
    add_check(checks, "no_exact_sql_problem_duplicates", not duplicate_pairs, len(duplicate_pairs), 0)

    near_pairs: list[dict[str, Any]] = []
    combined = [
        normalized_tokens(str(case.get("sql") or "") + " " + str(case.get("user_problem") or ""))
        for case in cases
    ]
    for left in range(len(cases)):
        for right in range(left + 1, len(cases)):
            union = combined[left] | combined[right]
            similarity = len(combined[left] & combined[right]) / max(1, len(union))
            if similarity >= 0.82:
                near_pairs.append(
                    {
                        "left": cases[left]["query_id"],
                        "right": cases[right]["query_id"],
                        "jaccard": round(similarity, 6),
                    }
                )
    near_record_ids = {pair[side] for pair in near_pairs for side in ("left", "right")}
    near_rate = len(near_record_ids) / max(1, len(cases))
    add_check(
        checks,
        "estimated_near_duplicate_record_rate",
        near_rate < 0.05,
        {"rate": round(near_rate, 6), "pairs": near_pairs[:100]},
        "<0.05 using global token-set Jaccard >=0.82",
    )

    pooling_helpers = load_pooling_helpers(annotation / "scripts" / "build_candidate_pools.py")

    # Independently bind the completed retrieval artifacts to their exact corpus,
    # case/query inputs, cached embedding snapshot, and producing scripts.  The
    # binding report deliberately does not hash itself.
    retrieval_binding_failures: list[str] = []
    binding_path = annotation / "provenance" / "retrieval_provenance_binding.json"
    corpus_snapshot_path = annotation / "provenance" / "corpus_snapshot.json"
    builder_path = annotation / "scripts" / "build_candidate_pools.py"
    finalizer_path = annotation / "scripts" / "finalize_retrieval_provenance.py"
    binding: dict[str, Any] = {}
    corpus_snapshot: dict[str, Any] = {}
    for label, path, destination in (
        ("retrieval provenance binding", binding_path, "binding"),
        ("corpus snapshot", corpus_snapshot_path, "corpus_snapshot"),
    ):
        if not path.is_file():
            retrieval_binding_failures.append(f"missing {label}: {path.relative_to(root)}")
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                retrieval_binding_failures.append(f"{label} must be a JSON object")
            elif destination == "binding":
                binding = loaded
            else:
                corpus_snapshot = loaded
        except Exception as exc:
            retrieval_binding_failures.append(f"cannot read {label}: {exc}")

    if binding:
        if binding.get("binding_method") != "post_build_exact_cached_snapshot_capture":
            retrieval_binding_failures.append(
                "binding_method is not post_build_exact_cached_snapshot_capture"
            )
        if binding.get("capture_stage") != "final_after_derived_refresh":
            retrieval_binding_failures.append(
                "retrieval binding is not the final_after_derived_refresh capture"
            )

    query_hash_rows = [
        {
            "query_id": case["query_id"],
            "query_text_hash": hashlib.sha256(
                pooling_helpers.query_text(case).encode("utf-8")
            ).hexdigest(),
        }
        for case in sorted(cases, key=lambda row: row["query_id"])
    ]
    expected_retrieval_inputs = {
        "corpus_path": "construction/data/processed/corpus.jsonl",
        "corpus_sha256": corpus_sha256,
        "corpus_chunk_count": len(corpus),
        "cases_path": "annotation/codex/dev_250.jsonl",
        "cases_sha256": sha256_file(paths["cases"]),
        "case_count": len(cases),
        "query_hash_set_sha256": sha256_json(query_hash_rows),
    }
    binding_inputs = binding.get("inputs") if binding else None
    config_inputs = retrieval_config.get("inputs")
    if binding_inputs != expected_retrieval_inputs:
        retrieval_binding_failures.append(
            "retrieval binding inputs differ from the current corpus/dev/query hashes"
        )
    if config_inputs != expected_retrieval_inputs:
        retrieval_binding_failures.append(
            "retrieval_config.inputs differ from the current corpus/dev/query hashes"
        )
    if binding_inputs != config_inputs:
        retrieval_binding_failures.append(
            "retrieval binding inputs and retrieval_config.inputs are not exactly equal"
        )

    expected_corpus_snapshot_fields = {
        "path": expected_retrieval_inputs["corpus_path"],
        "sha256": expected_retrieval_inputs["corpus_sha256"],
        "chunk_count": expected_retrieval_inputs["corpus_chunk_count"],
        "validation_status": kb_report.get("status"),
        "validation_report_path": "construction/reports/validation_report.json",
        "validation_report_sha256": sha256_file(paths["kb_validation"]),
    }
    if not corpus_snapshot or any(
        corpus_snapshot.get(field) != expected
        for field, expected in expected_corpus_snapshot_fields.items()
    ):
        retrieval_binding_failures.append(
            "corpus_snapshot.json differs from the current corpus or KB validation report"
        )

    model_identity_fields = (
        "requested_model",
        "resolved_repository",
        "resolved_revision",
        "snapshot_manifest_sha256",
        "snapshot_file_count",
    )
    if binding:
        for field in model_identity_fields:
            value = embedding_model.get(field)
            if field == "snapshot_file_count":
                valid_value = isinstance(value, int) and not isinstance(value, bool) and value > 0
            else:
                valid_value = isinstance(value, str) and bool(value.strip())
            if not valid_value:
                retrieval_binding_failures.append(
                    f"embedding_model.json has invalid or missing {field}"
                )
            if binding.get(field) != value:
                retrieval_binding_failures.append(
                    f"retrieval binding model field {field} differs from embedding_model.json"
                )
        if binding.get("embedding_model_json_sha256") != sha256_file(paths["embedding_model"]):
            retrieval_binding_failures.append(
                "retrieval binding embedding_model_json_sha256 is stale"
            )

    for field, script_path in (
        ("builder_file_sha256_at_binding", builder_path),
        ("finalizer_file_sha256_at_binding", finalizer_path),
    ):
        if not script_path.is_file():
            retrieval_binding_failures.append(
                f"missing binding script: {script_path.relative_to(root)}"
            )
        elif binding and binding.get(field) != sha256_file(script_path):
            retrieval_binding_failures.append(f"retrieval binding {field} is stale")

    retrieval_artifact_paths = {
        "cases": paths["cases"],
        "pools": paths["pools"],
        "qrels": paths["qrels"],
        "leakage": paths["leakage"],
        "runs": paths["runs"],
        "metrics": paths["retrieval_metrics"],
        "config": paths["retrieval_config"],
    }
    if binding.get("latest_snapshot_mtime_not_after_earliest_output") is not True:
        retrieval_binding_failures.append(
            "retrieval binding does not affirm model-snapshot/output timestamp ordering"
        )
    try:
        model_cache_root = (annotation / "work" / "model_cache").resolve()
        snapshot_paths = [
            (model_cache_root / item["path"]).resolve()
            for item in embedding_model.get("files", [])
        ]
        if not snapshot_paths or any(
            not path.is_file() or model_cache_root not in path.parents
            for path in snapshot_paths
        ):
            raise ValueError("embedding snapshot file list is empty, missing, or escapes cache")
        latest_snapshot_mtime = max(path.stat().st_mtime for path in snapshot_paths)
        earliest_ranking_output_mtime = min(
            retrieval_artifact_paths[name].stat().st_mtime
            for name in ("pools", "runs", "metrics", "config")
        )
        if latest_snapshot_mtime > earliest_ranking_output_mtime:
            retrieval_binding_failures.append(
                "cached embedding snapshot is newer than a bound ranking output"
            )
    except Exception as exc:
        retrieval_binding_failures.append(
            f"cannot verify embedding snapshot/output timestamp ordering: {exc}"
        )
    expected_artifact_hashes = {
        name: sha256_file(path) for name, path in retrieval_artifact_paths.items()
    }
    artifact_hashes_after = binding.get("artifact_sha256_after_binding") if binding else None
    artifact_hashes_before = binding.get("artifact_sha256_before_binding") if binding else None
    if artifact_hashes_after != expected_artifact_hashes:
        retrieval_binding_failures.append(
            "artifact_sha256_after_binding must have exactly the fixed artifact keys and current hashes"
        )
    if not isinstance(artifact_hashes_before, dict) or not set(
        expected_artifact_hashes
    ).issubset(artifact_hashes_before):
        retrieval_binding_failures.append(
            "artifact_sha256_before_binding lacks one or more fixed artifact keys"
        )
    if artifact_hashes_before != artifact_hashes_after:
        retrieval_binding_failures.append(
            "final retrieval capture changed artifacts during binding (before/after hashes differ)"
        )
    add_check(
        checks,
        "retrieval_input_snapshot_binding_hard_gate",
        not retrieval_binding_failures,
        len(retrieval_binding_failures),
        0,
        retrieval_binding_failures,
    )

    pool_query_ids = [str(row.get("query_id")) for row in pools]
    pool_by_query = {str(row.get("query_id")): row for row in pools}
    qrels_by_query: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    qrel_failures: list[str] = []
    duplicate_pool_query_ids = sorted(
        query_id for query_id, count in Counter(pool_query_ids).items() if count > 1
    )
    if duplicate_pool_query_ids:
        qrel_failures.append(f"duplicate candidate pool query IDs {duplicate_pool_query_ids}")
    seen_pairs: set[tuple[str, str]] = set()
    for row in qrels:
        pair = (str(row.get("query_id")), str(row.get("chunk_id")))
        if pair in seen_pairs:
            qrel_failures.append(f"duplicate qrel pair {pair}")
        seen_pairs.add(pair)
        if row.get("relevance") not in (0, 1, 2):
            qrel_failures.append(f"invalid qrel relevance {pair}: {row.get('relevance')}")
        qrels_by_query[pair[0]][pair[1]] = row
    if set(pool_query_ids) != set(actual_ids) or len(pool_query_ids) != len(actual_ids):
        qrel_failures.append("candidate pool query IDs do not match dev set")
    if set(qrels_by_query) != set(actual_ids):
        qrel_failures.append("qrel query IDs do not match dev set")
    for query_id, pool in pool_by_query.items():
        candidates = pool.get("candidates", [])
        candidate_ids = [str(item.get("chunk_id")) for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            qrel_failures.append(f"{query_id}: duplicate candidate chunk IDs")
        if not set(candidate_ids).issubset(set(qrels_by_query.get(query_id, {}))):
            qrel_failures.append(f"{query_id}: candidate pool is not a qrel subset")
        if not all(chunk_id in corpus_by_id for chunk_id in candidate_ids):
            qrel_failures.append(f"{query_id}: candidate contains unresolved chunk")
        labels = {item.get("relevance") for item in candidates}
        if not labels.issubset({0, 1, 2}) or 2 not in labels:
            qrel_failures.append(f"{query_id}: invalid/missing direct relevance labels {labels}")
        sources = {source for item in candidates for source in item.get("retrieved_by", [])}
        if "bm25" not in sources or "dense" not in sources or "source_link" not in sources:
            qrel_failures.append(f"{query_id}: incomplete retrieval-source union {sources}")
        case = next((value for value in cases if value["query_id"] == query_id), None)
        if case:
            expected_query_hash = hashlib.sha256(
                pooling_helpers.query_text(case).encode("utf-8")
            ).hexdigest()
            if pool.get("query_text_hash") != expected_query_hash:
                qrel_failures.append(f"{query_id}: stale/incorrect query_text_hash")
            config = pool.get("pool_config", {})
            if config.get("bm25_top_k") != 30 or config.get("dense_top_k") != 30:
                qrel_failures.append(f"{query_id}: pool top-k configuration is not 30/30")
            if (
                config.get("bm25_top_k") != retrieval_config.get("bm25", {}).get("top_k")
                or config.get("dense_top_k") != retrieval_config.get("dense", {}).get("top_k")
                or config.get("dense_method") != retrieval_config.get("dense", {}).get("method")
            ):
                qrel_failures.append(f"{query_id}: pool configuration differs from retrieval config")
            evidence_ids_for_case = {item["chunk_id"] for item in case["evidence"]}
            for method, top_k_name in (("bm25", "bm25_top_k"), ("dense", "dense_top_k")):
                method_items = [item for item in candidates if method in item.get("retrieved_by", [])]
                ranks = [item.get("ranks", {}).get(method) for item in method_items]
                expected_ranks = list(range(1, int(config.get(top_k_name) or 0) + 1))
                if any(not isinstance(rank, int) for rank in ranks) or sorted(ranks) != expected_ranks:
                    qrel_failures.append(f"{query_id}: {method} ranks are incomplete or duplicated")
            for item in candidates:
                chunk_id = str(item.get("chunk_id"))
                retrieved_by = item.get("retrieved_by", [])
                if len(retrieved_by) != len(set(retrieved_by)):
                    qrel_failures.append(f"{query_id}: duplicate retrieved_by value for {chunk_id}")
                rank_methods = set(item.get("ranks", {}))
                score_methods = set(item.get("scores", {}))
                expected_methods = set(retrieved_by) & {"bm25", "dense"}
                if rank_methods != expected_methods or score_methods != expected_methods:
                    qrel_failures.append(f"{query_id}: rank/score provenance mismatch for {chunk_id}")
                if "source_link" in retrieved_by and chunk_id not in evidence_ids_for_case:
                    qrel_failures.append(f"{query_id}: non-evidence source_link {chunk_id}")
                if chunk_id in evidence_ids_for_case and "source_link" not in retrieved_by:
                    qrel_failures.append(f"{query_id}: evidence candidate lacks source_link {chunk_id}")
                if any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    for value in item.get("scores", {}).values()
                ):
                    qrel_failures.append(f"{query_id}: non-finite/non-numeric score for {chunk_id}")
                qrel = qrels_by_query.get(query_id, {}).get(chunk_id, {})
                for field in ("relevance", "judgment_origin", "judgment_method"):
                    if item.get(field) != qrel.get(field):
                        qrel_failures.append(
                            f"{query_id}: candidate/qrel {field} mismatch for {chunk_id}"
                        )
            evidence_judgments = {
                item["chunk_id"]: int(item["relevance"]) for item in case["evidence"]
            }
            for item in candidates:
                chunk_id = item["chunk_id"]
                expected_relevance = pooling_helpers.machine_relevance(
                    case,
                    corpus_by_id[chunk_id],
                    set(item.get("retrieved_by", [])),
                    evidence_judgments,
                )
                expected_method = (
                    "explicit_case_evidence"
                    if chunk_id in evidence_judgments
                    else "deterministic_contextual_heuristic"
                )
                method = item.get("judgment_method")
                if method in {"explicit_case_evidence", "deterministic_contextual_heuristic"} and (
                    item.get("relevance") != expected_relevance or method != expected_method
                ):
                    qrel_failures.append(
                        f"{query_id}: candidate judgment differs from frozen policy for {chunk_id}"
                    )
                if method not in {
                    "explicit_case_evidence",
                    "deterministic_contextual_heuristic",
                    "blind_double_pass_consensus",
                    "blind_double_pass_adjudicated",
                }:
                    qrel_failures.append(
                        f"{query_id}: unsupported judgment method for {chunk_id}: {method}"
                    )
            evidence_ids = set(evidence_judgments)
            if not evidence_ids.issubset(set(candidate_ids)):
                qrel_failures.append(f"{query_id}: evidence is not a candidate-pool subset")
            for chunk_id, expected_relevance in evidence_judgments.items():
                if qrels_by_query[query_id].get(chunk_id, {}).get("relevance") != expected_relevance:
                    qrel_failures.append(
                        f"{query_id}: evidence qrel {chunk_id} does not match "
                        f"case relevance {expected_relevance}"
                    )
    global_qrel_labels = {row.get("relevance") for row in qrels}
    if global_qrel_labels != {0, 1, 2}:
        qrel_failures.append(
            f"dataset-level qrels must use 0, 1, and 2; observed {global_qrel_labels}"
        )
    add_check(checks, "candidate_pool_and_complete_qrels", not qrel_failures, len(qrel_failures), 0, qrel_failures[:200])

    refresh_failures: list[str] = []
    sealed_refresh = dict(top30_refresh)
    stored_refresh_sha = sealed_refresh.pop("record_sha256", None)
    if stored_refresh_sha != sha256_json(sealed_refresh):
        refresh_failures.append("top30 refresh record_sha256 is missing or stale")
    if top30_refresh.get("annotation_main_version") != "v1":
        refresh_failures.append("top30 refresh does not identify v1 as the main annotation")
    if top30_refresh.get("status") != "COMPLETE_MACHINE_PROPOSED_DEVELOPMENT_ONLY":
        refresh_failures.append("top30 refresh is not complete")
    if top30_refresh.get("human_verified") is not False:
        refresh_failures.append("top30 refresh must remain explicitly non-human-verified")
    formal_pairs: set[tuple[str, str]] = set()
    formal_run_hashes = top30_refresh.get("formal_run_sha256", {})
    for relative, expected_hash in formal_run_hashes.items():
        run_path = root / relative
        if not run_path.is_file() or sha256_file(run_path) != expected_hash:
            refresh_failures.append(f"formal run hash mismatch: {relative}")
            continue
        for line_number, line in enumerate(run_path.read_text(encoding="utf-8").splitlines(), 1):
            fields = line.split()
            if len(fields) != 6:
                refresh_failures.append(f"invalid formal run row: {relative}:{line_number}")
                continue
            formal_pairs.add((fields[0], fields[2]))
    pair_digest_rows = [
        {"query_id": query_id, "chunk_id": chunk_id}
        for query_id, chunk_id in sorted(formal_pairs)
    ]
    if len(formal_pairs) != top30_refresh.get("formal_pair_count"):
        refresh_failures.append("formal Top-30 pair count differs from refresh provenance")
    if sha256_json(pair_digest_rows) != top30_refresh.get("formal_pair_set_sha256"):
        refresh_failures.append("formal Top-30 pair set hash differs from refresh provenance")
    qrel_pairs = set(seen_pairs)
    if not formal_pairs.issubset(qrel_pairs):
        refresh_failures.append(
            f"main v1 qrels omit {len(formal_pairs - qrel_pairs)} formal Top-30 pairs"
        )
    blind_methods = {"blind_double_pass_consensus", "blind_double_pass_adjudicated"}
    for pair in formal_pairs:
        row = qrels_by_query.get(pair[0], {}).get(pair[1], {})
        method = row.get("judgment_method")
        if method not in blind_methods | {"explicit_case_evidence"}:
            refresh_failures.append(f"formal pair lacks refreshed judgment provenance: {pair}")
        if method in blind_methods and (
            row.get("confidence") not in {"low", "medium", "high"}
            or not isinstance(row.get("resolution"), str)
        ):
            refresh_failures.append(f"blind formal qrel lacks confidence/resolution: {pair}")
    for row in qrels:
        pair = (row.get("query_id"), row.get("chunk_id"))
        if row.get("judgment_method") in blind_methods and pair not in formal_pairs:
            refresh_failures.append(f"blind judgment occurs outside frozen formal scope: {pair}")
    refresh_hashes = top30_refresh.get("artifact_sha256", {})
    for field, path in (
        ("updated_v1_qrels", paths["qrels"]),
        ("updated_candidate_pools", paths["pools"]),
        ("updated_legacy_retrieval_metrics", paths["retrieval_metrics"]),
    ):
        if refresh_hashes.get(field) != sha256_file(path):
            refresh_failures.append(f"top30 refresh artifact hash is stale: {field}")
    sensitivity_inputs = annotation_sensitivity.get("input_sha256", {})
    if sensitivity_inputs.get("main_v1_qrels") != sha256_file(paths["qrels"]):
        refresh_failures.append("annotation sensitivity report is stale relative to main v1 qrels")
    if annotation_sensitivity.get("human_verified") is not False:
        refresh_failures.append("annotation sensitivity report must remain non-human-verified")
    add_check(
        checks,
        "v1_top30_blind_refresh_integrity",
        not refresh_failures,
        len(refresh_failures),
        0,
        refresh_failures[:200],
    )

    retrieval_failures: list[str] = []
    run_ids = [str(row.get("query_id")) for row in retrieval_runs]
    if len(run_ids) != len(set(run_ids)):
        retrieval_failures.append("duplicate retrieval-run query IDs")
    if set(run_ids) != set(actual_ids) or len(run_ids) != len(actual_ids):
        retrieval_failures.append("retrieval-run query IDs do not match dev set")
    corpus_index_by_id = {row["chunk_id"]: index for index, row in enumerate(corpus)}
    recomputed_metric_rows: list[dict[str, Any]] = []
    for run in retrieval_runs:
        query_id = str(run.get("query_id"))
        pool = pool_by_query.get(query_id, {})
        candidates = pool.get("candidates", [])
        rankings = run.get("rankings", {})
        if set(rankings) != {"bm25", "dense", "hybrid_rrf"}:
            retrieval_failures.append(f"{query_id}: retrieval systems differ from expected set")
            continue
        expected_rankings: dict[str, list[str]] = {}
        for method in ("bm25", "dense"):
            expected_rankings[method] = [
                item["chunk_id"]
                for item in sorted(
                    (item for item in candidates if method in item.get("retrieved_by", [])),
                    key=lambda item: item["ranks"][method],
                )
            ]
            if rankings.get(method) != expected_rankings[method]:
                retrieval_failures.append(f"{query_id}: {method} run differs from candidate ranks")
        try:
            hybrid = pooling_helpers.reciprocal_rank_fusion(
                [(corpus_index_by_id[chunk_id], 0.0) for chunk_id in expected_rankings["bm25"]],
                [(corpus_index_by_id[chunk_id], 0.0) for chunk_id in expected_rankings["dense"]],
                30,
                constant=60,
            )
            expected_hybrid = [corpus[index]["chunk_id"] for index, _ in hybrid]
        except Exception as exc:
            retrieval_failures.append(f"{query_id}: cannot recompute hybrid RRF: {exc}")
            continue
        if run.get("rrf_constant") != 60 or rankings.get("hybrid_rrf") != expected_hybrid:
            retrieval_failures.append(f"{query_id}: hybrid RRF run is stale or incorrect")
        integer_qrels = {
            chunk_id: int(row["relevance"])
            for chunk_id, row in qrels_by_query.get(query_id, {}).items()
        }
        for system_name, ranking in rankings.items():
            try:
                recomputed_metric_rows.append(
                    {
                        "query_id": query_id,
                        "system": system_name,
                        **pooling_helpers.ranking_metrics(ranking, integer_qrels),
                    }
                )
            except Exception as exc:
                retrieval_failures.append(f"{query_id}: cannot score {system_name}: {exc}")
    expected_system_metrics: dict[str, dict[str, float]] = {}
    for system_name in sorted({row["system"] for row in recomputed_metric_rows}):
        system_rows = [row for row in recomputed_metric_rows if row["system"] == system_name]
        metric_names = [name for name in system_rows[0] if name not in {"query_id", "system"}]
        expected_system_metrics[system_name] = {
            name: round(sum(float(row[name]) for row in system_rows) / len(system_rows), 6)
            for name in metric_names
        }
    if retrieval_metrics.get("systems") != expected_system_metrics:
        retrieval_failures.append("aggregate retrieval metrics differ from saved judged runs")
    if "circular" not in str(retrieval_metrics.get("warning") or "").lower():
        retrieval_failures.append("retrieval metric report omits circular-machine-heuristic warning")
    if "circular" not in str(
        retrieval_config.get("judgment_policy", {}).get("metric_caveat") or ""
    ).lower():
        retrieval_failures.append("retrieval config omits heuristic-qrel circularity caveat")
    dense_config = retrieval_config.get("dense", {})
    if retrieval_config.get("bm25") != {"k1": 1.2, "b": 0.75, "top_k": 30}:
        retrieval_failures.append("BM25 retrieval config differs from frozen settings")
    if (
        dense_config.get("method") != "fastembed_neural_text_embedding_cosine"
        or dense_config.get("top_k") != 30
    ):
        retrieval_failures.append("final dense retrieval is not the frozen FastEmbed top-30 backend")
    if retrieval_metrics.get("dense_metadata") != dense_config:
        retrieval_failures.append("retrieval metric dense metadata differs from retrieval config")
    for field in (
        "resolved_repository",
        "resolved_revision",
        "snapshot_manifest_sha256",
        "cache_dir",
    ):
        if dense_config.get(field) != embedding_model.get(field):
            retrieval_failures.append(f"embedding model/config {field} mismatch")
    if dense_config.get("model_name") != embedding_model.get("requested_model"):
        retrieval_failures.append("embedding requested model differs from retrieval config")
    model_cache = (annotation / "work" / "model_cache").resolve()
    try:
        recomputed_model = pooling_helpers.embedding_model_provenance(
            model_cache, str(dense_config.get("model_name") or "")
        )
        if embedding_model != recomputed_model:
            retrieval_failures.append("embedding model manifest differs from resolved snapshot")
    except Exception as exc:
        retrieval_failures.append(f"cannot recompute embedding model manifest: {exc}")
    add_check(
        checks,
        "retrieval_runs_metrics_and_embedding_provenance",
        not retrieval_failures,
        len(retrieval_failures),
        0,
        retrieval_failures[:200],
    )

    leakage_query_ids = [str(row.get("query_id")) for row in leakage]
    leakage_by_id = {str(row.get("query_id")): row for row in leakage}
    leakage_failures: list[str] = []
    duplicate_leakage_ids = sorted(
        query_id for query_id, count in Counter(leakage_query_ids).items() if count > 1
    )
    if duplicate_leakage_ids:
        leakage_failures.append(f"duplicate leakage query IDs {duplicate_leakage_ids}")
    if set(leakage_query_ids) != set(actual_ids) or len(leakage_query_ids) != len(actual_ids):
        leakage_failures.append("leakage query IDs do not match dev set")
    for case in cases:
        query_id = case["query_id"]
        evidence_rows = [corpus_by_id[item["chunk_id"]] for item in case.get("evidence", [])]
        recomputed = pooling_helpers.leakage_check(case, evidence_rows)
        saved = leakage_by_id.get(query_id)
        if saved != recomputed:
            leakage_failures.append(f"{query_id}: saved leakage result differs from recomputation")
        if recomputed.get("status") == "FAIL":
            leakage_failures.append(f"{query_id}: query-source leakage hard failure")
    add_check(checks, "query_source_leakage_hard_gate", not leakage_failures, leakage_failures, [])

    execution_provenance_failures: list[str] = []
    execution_common = load_pooling_helpers(annotation / "scripts" / "execution_common.py")
    execution_configs = {
        "sqlite": {
            "oracle": annotation / "work" / "semantic_oracles" / "sqlite_3_45_3.jsonl",
            "replay": annotation / "provenance" / "sqlite_3_45_3_reverification.jsonl",
            "promotion": annotation / "provenance" / "sqlite_execution_promotions.json",
            "script": annotation / "scripts" / "promote_sqlite_execution.py",
            "runner": annotation / "scripts" / "reverify_sqlite.py",
            "engine_version": "3.45.3",
        },
        "duckdb": {
            "oracle": annotation / "work" / "semantic_oracles" / "duckdb_1_5_5.jsonl",
            "replay": annotation / "provenance" / "duckdb_1_5_5_reverification.jsonl",
            "promotion": annotation / "provenance" / "duckdb_execution_promotions.json",
            "script": annotation / "scripts" / "promote_duckdb_execution.py",
            "runner": annotation / "scripts" / "reverify_duckdb.py",
            "engine_version": "1.5.5",
        },
    }
    for dialect, config in execution_configs.items():
        required_paths = [
            config["oracle"],
            config["replay"],
            config["promotion"],
            config["script"],
            config["runner"],
            annotation / "scripts" / "execution_common.py",
        ]
        missing = [str(path.relative_to(root)) for path in required_paths if not path.is_file()]
        if missing:
            execution_provenance_failures.append(f"{dialect}: missing execution provenance {missing}")
            continue
        oracle_rows = list(iter_jsonl(config["oracle"]))
        replay_rows = list(iter_jsonl(config["replay"]))
        oracle_ids = [str(row.get("query_id")) for row in oracle_rows]
        replay_ids = [str(row.get("query_id")) for row in replay_rows]
        if len(oracle_ids) != len(set(oracle_ids)):
            execution_provenance_failures.append(f"{dialect}: duplicate semantic-oracle IDs")
        if len(replay_ids) != len(set(replay_ids)):
            execution_provenance_failures.append(f"{dialect}: duplicate replay IDs")
        oracle_by_id = {row["query_id"]: row for row in oracle_rows}
        replay_by_id = {row["query_id"]: row for row in replay_rows}
        execution_cases = [
            case
            for case in cases
            if case.get("dialect") == dialect
            and case.get("verification", {}).get("method") == "execution"
        ]
        execution_ids = {case["query_id"] for case in execution_cases}
        dialect_case_by_id = {
            case["query_id"]: case for case in cases if case.get("dialect") == dialect
        }
        expected_replay_ids = (
            execution_ids
            if dialect == "sqlite"
            else {
                case["query_id"]
                for case in dialect_case_by_id.values()
                if case.get("version") == "1.5"
                and case.get("version_status") == "current"
            }
        )
        if set(replay_by_id) != expected_replay_ids:
            execution_provenance_failures.append(
                f"{dialect}: replay IDs differ from the exact current runtime scope"
            )
        if set(oracle_by_id) != execution_ids:
            execution_provenance_failures.append(f"{dialect}: semantic-oracle IDs differ from execution cases")
        if not execution_ids.issubset(set(replay_by_id)):
            execution_provenance_failures.append(f"{dialect}: execution cases are missing replay rows")
        promotion = json.loads(config["promotion"].read_text(encoding="utf-8"))
        promotion_records = promotion.get("records", [])
        promotion_id_list = [str(row.get("query_id")) for row in promotion_records]
        promotion_ids = set(promotion_id_list)
        if len(promotion_id_list) != len(promotion_ids):
            execution_provenance_failures.append(f"{dialect}: duplicate promotion record IDs")
        if promotion_ids != execution_ids or promotion.get("promoted_count") != len(execution_ids):
            execution_provenance_failures.append(f"{dialect}: promotion record IDs/count differ")
        if promotion.get("engine_version") != config["engine_version"]:
            execution_provenance_failures.append(f"{dialect}: promotion engine version mismatch")
        if promotion.get("source_replay_sha256") != sha256_file(config["replay"]):
            execution_provenance_failures.append(f"{dialect}: promotion replay hash is stale")
        if promotion.get("semantic_oracle_sha256") != sha256_file(config["oracle"]):
            execution_provenance_failures.append(f"{dialect}: promotion oracle hash is stale")
        expected_runner_sha256 = sha256_file(config["runner"])
        expected_common_sha256 = sha256_file(annotation / "scripts" / "execution_common.py")
        expected_duckdb_runtime_settings = {
            "random_seed": 0.5,
            "database": ":memory:",
            "attachment_isolation": "temporary_directory",
        }
        for replay in replay_rows:
            replay_query_id = str(replay.get("query_id"))
            replay_case = dialect_case_by_id.get(replay_query_id)
            if replay_case is None:
                execution_provenance_failures.append(
                    f"{replay_query_id}: replay has no matching current {dialect} case"
                )
            else:
                if replay.get("execution_input_sha256") != execution_common.execution_input_sha256(
                    replay_case
                ):
                    execution_provenance_failures.append(
                        f"{replay_query_id}: replay execution-input hash is stale"
                    )
                if replay.get("declared_semantic_check") != replay_case.get(
                    "verification", {}
                ).get("semantic_check"):
                    execution_provenance_failures.append(
                        f"{replay_query_id}: declared semantic check differs from current case"
                    )
            if "semantic_oracle" in replay:
                execution_provenance_failures.append(
                    f"{replay_query_id}: raw replay misleadingly embeds semantic_oracle"
                )
            if replay.get("engine_version") != config["engine_version"]:
                execution_provenance_failures.append(
                    f"{replay_query_id}: replay engine version mismatch"
                )
            if (
                replay.get("runner_sha256") != expected_runner_sha256
                or replay.get("execution_common_sha256") != expected_common_sha256
            ):
                execution_provenance_failures.append(
                    f"{replay_query_id}: replay runner/common script provenance is stale"
                )
            if dialect == "sqlite" and (
                replay.get("status") != "PASS"
                or replay.get("engine_version_matches_claim") is not True
                or replay.get("claimed_error_polarity_matches")
                != {"original": True, "fixed": True}
            ):
                execution_provenance_failures.append(
                    f"{replay_query_id}: SQLite replay status/scope mismatch"
                )
            if (
                dialect == "duckdb"
                and replay.get("runtime_settings") != expected_duckdb_runtime_settings
            ):
                execution_provenance_failures.append(
                    f"{replay_query_id}: DuckDB replay runtime settings differ from frozen settings"
                )
            if dialect == "duckdb" and replay.get("version_scope_match") is not True:
                execution_provenance_failures.append(
                    f"{replay_query_id}: DuckDB replay scope mismatch"
                )
        promoter = load_pooling_helpers(config["script"])
        for case in execution_cases:
            query_id = case["query_id"]
            oracle = oracle_by_id.get(query_id, {})
            replay = replay_by_id.get(query_id, {})
            if not replay:
                continue
            expected_input_hash = execution_common.execution_input_sha256(case)
            if replay.get("execution_input_sha256") != expected_input_hash:
                execution_provenance_failures.append(f"{query_id}: replay execution-input hash is stale")
            if oracle.get("passed") is not True:
                execution_provenance_failures.append(f"{query_id}: semantic-oracle review is not PASS")
                continue
            expected = oracle.get("expected")
            oracle_type = oracle.get("oracle_type")
            try:
                if dialect == "sqlite":
                    recomputed_actual = promoter.semantic_value(replay["fixed"], oracle_type, expected)
                else:
                    recomputed_actual = promoter.semantic_value(replay["fixed"], oracle_type)
            except Exception as exc:
                execution_provenance_failures.append(f"{query_id}: cannot project replay semantic value: {exc}")
                continue
            if recomputed_actual != oracle.get("actual"):
                execution_provenance_failures.append(f"{query_id}: semantic-oracle actual differs from replay")
            if not semantic_oracle_matches(expected, recomputed_actual):
                execution_provenance_failures.append(f"{query_id}: replay fails semantic oracle")
            semantic = case.get("verification", {}).get("semantic_check", {})
            if (
                semantic.get("oracle_type") != oracle_type
                or semantic.get("expected") != expected
                or semantic.get("observed") != recomputed_actual
                or semantic.get("passed") is not True
            ):
                execution_provenance_failures.append(f"{query_id}: case semantic check differs from reviewed oracle")
            verification = case.get("verification", {})
            if verification.get("engine_version") != config["engine_version"]:
                execution_provenance_failures.append(f"{query_id}: case/replay engine version mismatch")
            original_replay = replay.get("original", {})
            fixed_replay = replay.get("fixed", {})
            try:
                if dialect == "sqlite":
                    original_projected = promoter.semantic_value(
                        original_replay, oracle_type, expected
                    )
                    fixed_observed_result = recomputed_actual
                else:
                    original_projected = (
                        original_replay.get("result_rows")
                        if original_replay.get("succeeded")
                        else original_replay.get("database_snapshot")
                    )
                    fixed_observed_result = promoter.observed_value(fixed_replay, oracle_type)
            except Exception:
                original_projected = None
                fixed_observed_result = recomputed_actual
            expected_original_outcome = (
                "wrong_result" if original_replay.get("succeeded") else "error"
            )
            expected_fixed_outcome = (
                "expected_result"
                if fixed_replay.get("succeeded")
                else "expected_constraint_error"
            )
            original_case = verification.get("original", {})
            fixed_case = verification.get("fixed", {})
            if (
                original_case.get("executed") is not True
                or original_case.get("outcome") != expected_original_outcome
                or original_case.get("observed_error") != original_replay.get("error")
                or original_case.get("observed_result") != original_projected
            ):
                execution_provenance_failures.append(f"{query_id}: original execution claim differs from replay")
            if (
                fixed_case.get("executed") is not True
                or fixed_case.get("outcome") != expected_fixed_outcome
                or fixed_case.get("observed_error") != fixed_replay.get("error")
                or fixed_case.get("observed_result") != fixed_observed_result
            ):
                execution_provenance_failures.append(f"{query_id}: repaired execution claim differs from replay")
            promotion_record = next(
                (row for row in promotion_records if row.get("query_id") == query_id),
                {},
            )
            if (
                promotion_record.get("semantic_oracle_passed") is not True
                or promotion_record.get("oracle_type") != oracle_type
                or promotion_record.get("original_succeeded")
                is not bool(original_replay.get("succeeded"))
                or promotion_record.get("fixed_succeeded")
                is not bool(fixed_replay.get("succeeded"))
                or promotion_record.get("execution_input_sha256") != expected_input_hash
                or promotion_record.get("runner_sha256") != expected_runner_sha256
                or promotion_record.get("execution_common_sha256") != expected_common_sha256
                or promotion_record.get("runner_sha256") != replay.get("runner_sha256")
                or promotion_record.get("execution_common_sha256")
                != replay.get("execution_common_sha256")
            ):
                execution_provenance_failures.append(f"{query_id}: promotion record differs from replay/oracle")
    add_check(
        checks,
        "execution_replay_and_exact_semantic_oracles",
        not execution_provenance_failures,
        len(execution_provenance_failures),
        0,
        execution_provenance_failures[:200],
    )

    execution_rows = [
        {
            "query_id": case["query_id"],
            "dialect": case["dialect"],
            "version": case.get("version"),
            "verification": case["verification"],
        }
        for case in cases
    ]
    write_jsonl(annotation / "execution_evidence.jsonl", execution_rows)

    # Deterministic, stratified Codex-agent audit: ten cases per dialect.
    rng = random.Random(20260828)
    audit_ids: list[str] = []
    for dialect in ALLOWED_DIALECTS:
        dialect_ids = [case["query_id"] for case in cases if case["dialect"] == dialect]
        audit_ids.extend(sorted(rng.sample(dialect_ids, 10)))
    case_by_id = {case["query_id"]: case for case in cases}
    audit_reviews: dict[str, dict[str, Any]] = {}
    audit_failures: list[str] = []
    audit_part_paths = sorted((annotation / "work" / "audit_parts").glob("*.jsonl"))
    for audit_path in audit_part_paths:
        for review in iter_jsonl(audit_path):
            query_id = str(review.get("query_id"))
            if query_id in audit_reviews:
                audit_failures.append(f"duplicate audit review for {query_id}")
            audit_reviews[query_id] = review
    expected_audit_ids = set(audit_ids)
    if set(audit_reviews) != expected_audit_ids:
        audit_failures.append(
            "audit review IDs differ: missing="
            f"{sorted(expected_audit_ids - set(audit_reviews))}, "
            f"extra={sorted(set(audit_reviews) - expected_audit_ids)}"
        )
    audit_rows: list[dict[str, Any]] = []
    for query_id in sorted(expected_audit_ids):
        review = audit_reviews.get(query_id, {})
        review_checks = review.get("checks", {})
        if set(review_checks) != set(AUDIT_CHECK_FIELDS):
            audit_failures.append(
                f"{query_id}: audit check keys differ from the frozen 11-field checklist"
            )
        missing_checks = [field for field in AUDIT_CHECK_FIELDS if not isinstance(review_checks.get(field), bool)]
        failed_checks = [field for field in AUDIT_CHECK_FIELDS if review_checks.get(field) is False]
        if missing_checks:
            audit_failures.append(f"{query_id}: missing/non-boolean audit checks {missing_checks}")
        if failed_checks:
            audit_failures.append(f"{query_id}: failed audit checks {failed_checks}")
        if review and review.get("status") != ("PASS" if not failed_checks and not missing_checks else "FAIL"):
            audit_failures.append(f"{query_id}: audit status disagrees with checks")
        case = case_by_id.get(query_id, {})
        expected_case_hash = sha256_json(case) if case else None
        if review and review.get("reviewed_case_sha256") != expected_case_hash:
            audit_failures.append(f"{query_id}: audit case hash is missing or stale")
        if review and review.get("corpus_sha256") != corpus_sha256:
            audit_failures.append(f"{query_id}: audit corpus hash is missing or stale")
        if review and not str(review.get("audit_run_id") or "").strip():
            audit_failures.append(f"{query_id}: audit_run_id is missing")
        if review and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            str(review.get("reviewed_at_utc") or ""),
        ):
            audit_failures.append(f"{query_id}: reviewed_at_utc is missing or invalid")
        if review and (
            not isinstance(review.get("findings"), list) or not review.get("findings")
        ):
            audit_failures.append(f"{query_id}: substantive audit finding is missing")
        structural_checks = {
            "primary_evidence_valid": any(
                item.get("chunk_id") == case.get("primary_evidence_chunk_id")
                and item.get("relevance") == 2
                and item.get("role") == "primary"
                for item in case.get("evidence", [])
            ),
            "schema_semantics_valid": not validate_case_shape(case) if case else False,
            "leakage_hard_gate_passed": leakage_by_id.get(query_id, {}).get("status") != "FAIL",
        }
        if not all(structural_checks.values()):
            audit_failures.append(f"{query_id}: failed structural audit checks")
        audit_rows.append(
            {
                "query_id": query_id,
                "dialect": case.get("dialect"),
                "reviewer": review.get("reviewer"),
                "audit_run_id": review.get("audit_run_id"),
                "reviewed_at_utc": review.get("reviewed_at_utc"),
                "reviewed_case_sha256": review.get("reviewed_case_sha256"),
                "corpus_sha256": review.get("corpus_sha256"),
                "status": review.get("status", "MISSING"),
                "checks": review_checks,
                "structural_checks": structural_checks,
                "findings": review.get("findings", []),
            }
        )
    quality_audit = {
        "status": "PASS" if not audit_failures else "FAIL",
        "sampling": {
            "method": "deterministic stratified random sample",
            "seed": 20260828,
            "sample_size": len(expected_audit_ids),
            "per_dialect": 10,
        },
        "review_method": "Independent Codex-agent semantic/evidence consistency review",
        "human_verified": False,
        "note": "Automated internal consistency audit of machine-proposed data; not human verification.",
        "review_part_files": [
            str(path.relative_to(root)).replace("\\", "/") for path in audit_part_paths
        ],
        "failures": audit_failures,
        "records": audit_rows,
    }
    write_json(annotation / "reports" / "quality_audit.json", quality_audit)
    write_json(annotation / "quality_audit.json", quality_audit)
    add_check(
        checks,
        "independent_quality_audit_50_cases",
        not audit_failures,
        {"reviewed": len(audit_reviews), "failures": len(audit_failures)},
        {"reviewed": 50, "failures": 0},
        audit_failures[:200],
    )

    evidence_source_case_counts: Counter[str] = Counter()
    evidence_source_passage_counts: Counter[str] = Counter()
    for case in cases:
        case_source_types: set[str] = set()
        for evidence in case.get("evidence", []):
            chunk = corpus_by_id.get(evidence.get("chunk_id"), {})
            source_type = str(chunk.get("source_type") or "unknown")
            evidence_source_passage_counts[source_type] += 1
            case_source_types.add(source_type)
        evidence_source_case_counts.update(case_source_types)

    statistics = {
        "total_cases": len(cases),
        "cases_per_dialect": dict(dialect_counts),
        "cases_per_version": dict(
            Counter(str(case.get("version") or "unknown") for case in cases)
        ),
        "cases_per_error_category": dict(category_counts),
        "cases_per_version_status": dict(Counter(case.get("version_status") for case in cases)),
        "case_flags_per_dialect": per_dialect_flags,
        "dialect_sensitive_cases": sum(
            bool(case.get("case_flags", {}).get("requires_dialect_reasoning")) for case in cases
        ),
        "version_sensitive_cases": sum(
            bool(case.get("case_flags", {}).get("requires_version_reasoning")) for case in cases
        ),
        "documented_error_cases": documented_errors,
        "plausible_but_wrong_cases": plausible,
        "average_evidence_passages": round(
            sum(len(case.get("evidence", [])) for case in cases) / max(1, len(cases)), 6
        ),
        "verification_method_distribution": dict(
            Counter(case.get("verification", {}).get("method") for case in cases)
        ),
        "verification_status_distribution": dict(
            Counter(case.get("verification", {}).get("status") for case in cases)
        ),
        "verification_pass_rate": round(verification_pass_rate, 6),
        "execution_verified_cases": sum(
            case.get("verification", {}).get("method") == "execution" for case in cases
        ),
        "cases_by_evidence_source_type": dict(evidence_source_case_counts),
        "evidence_passages_by_source_type": dict(evidence_source_passage_counts),
        "duplicate_detection": {
            "exact_duplicate_pairs": len(duplicate_pairs),
            "near_duplicate_pairs": len(near_pairs),
            "near_duplicate_record_count": len(near_record_ids),
            "estimated_near_duplicate_record_rate": round(near_rate, 6),
        },
        "estimated_near_duplicate_record_rate": round(near_rate, 6),
        "candidate_pool_records": len(pools),
        "qrel_judgments": len(qrels),
        "qrel_labels": dict(Counter(str(row.get("relevance")) for row in qrels)),
        "leakage_status": dict(Counter(row.get("status") for row in leakage)),
        "mysql_mariadb_comparison_cases": sum(
            bool(case.get("case_flags", {}).get("compares_mysql_mariadb")) for case in cases
        ),
        "duckdb_specific_cases": sum(
            bool(case.get("case_flags", {}).get("is_duckdb_specific")) for case in cases
        ),
    }
    write_json(annotation / "reports" / "statistics.json", statistics)
    write_json(annotation / "statistics.json", statistics)
    markdown_lines = [
        "# SQLMendRAG Codex development-set statistics",
        "",
        "> Machine-proposed development data; not human-labelled evaluation data.",
        "",
        f"- Total cases: {statistics['total_cases']}",
        f"- Documented-error cases: {documented_errors}",
        f"- Plausible-but-wrong cases: {plausible}",
        f"- Qrel judgments: {len(qrels)}",
        f"- Estimated near-duplicate record rate: {near_rate:.4%}",
        "",
        "## Cases per dialect",
        "",
        "| Dialect | Cases | Dialect-sensitive | Version-sensitive |",
        "|---|---:|---:|---:|",
    ]
    for dialect in ALLOWED_DIALECTS:
        markdown_lines.append(
            f"| {dialect} | {dialect_counts[dialect]} | "
            f"{per_dialect_flags[dialect]['requires_dialect_reasoning']} | "
            f"{per_dialect_flags[dialect]['requires_version_reasoning']} |"
        )
    markdown_lines.extend(["", "## Cases per error category", "", "| Category | Cases |", "|---|---:|"])
    for category in ERROR_CATEGORIES:
        markdown_lines.append(f"| {category} | {category_counts[category]} |")
    statistics_markdown = "\n".join(markdown_lines) + "\n"
    (annotation / "reports" / "statistics.md").write_text(statistics_markdown, encoding="utf-8")
    (annotation / "statistics.md").write_text(statistics_markdown, encoding="utf-8")

    required_artifacts = [
        paths["cases"],
        paths["pools"],
        paths["qrels"],
        paths["leakage"],
        paths["runs"],
        paths["retrieval_config"],
        paths["retrieval_metrics"],
        paths["embedding_model"],
        annotation / "execution_evidence.jsonl",
        annotation / "statistics.json",
        annotation / "statistics.md",
        annotation / "reports" / "statistics.json",
        annotation / "reports" / "statistics.md",
        annotation / "quality_audit.json",
        annotation / "reports" / "quality_audit.json",
        annotation / "schema" / "dev_case.schema.json",
        annotation / "schema" / "candidate_pool.schema.json",
        annotation / "schema" / "qrel.schema.json",
        annotation / "schema" / "query_source_leakage.schema.json",
        annotation / "prompts" / "generation_prompt.md",
        annotation / "reports" / "leakage_report.json",
        annotation / "provenance" / "corpus_snapshot.json",
        annotation / "provenance" / "generation_run.json",
        annotation / "provenance" / "normalization_report.json",
        annotation / "provenance" / "verification_environment.json",
        annotation / "provenance" / "sqlite_3_45_3_reverification.jsonl",
        annotation / "provenance" / "duckdb_1_5_5_reverification.jsonl",
        annotation / "provenance" / "sqlite_execution_promotions.json",
        annotation / "provenance" / "duckdb_execution_promotions.json",
        annotation / "provenance" / "case_corrections.json",
        annotation / "provenance" / "retrieval_provenance_binding.json",
        paths["top30_refresh"],
        paths["annotation_sensitivity"],
        root / "annotation" / "VERSION_HISTORY.md",
        annotation / "work" / "semantic_oracles" / "sqlite_3_45_3.jsonl",
        annotation / "work" / "semantic_oracles" / "duckdb_1_5_5.jsonl",
        annotation / "work" / "audit_parts" / "audit_pg_mysql.jsonl",
        annotation / "work" / "audit_parts" / "audit_sqlite.jsonl",
        annotation / "work" / "audit_parts" / "audit_mariadb.jsonl",
        annotation / "work" / "audit_parts" / "audit_duckdb.jsonl",
        *[
            annotation / "work" / "shards" / f"{dialect}.jsonl"
            for dialect in ALLOWED_DIALECTS
        ],
        annotation / "scripts" / "build_candidate_pools.py",
        annotation / "scripts" / "finalize_retrieval_provenance.py",
        annotation / "scripts" / "merge_shards.py",
        annotation / "scripts" / "validate_annotations.py",
        annotation / "scripts" / "reverify_sqlite.py",
        annotation / "scripts" / "reverify_duckdb.py",
        annotation / "scripts" / "promote_sqlite_execution.py",
        annotation / "scripts" / "promote_duckdb_execution.py",
        annotation / "scripts" / "apply_case_corrections.py",
        annotation / "scripts" / "execution_common.py",
        annotation / "README.md",
        annotation / "requirements.txt",
        annotation / "validate_annotations.py",
    ]
    missing_required_artifacts = [
        str(path.relative_to(root)).replace("\\", "/")
        for path in required_artifacts
        if not path.is_file()
    ]
    add_check(
        checks,
        "all_declared_core_artifacts_present",
        not missing_required_artifacts,
        missing_required_artifacts,
        [],
    )

    status = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
    report = {
        "status": status,
        "checks_passed": sum(check["status"] == "PASS" for check in checks),
        "checks_failed": sum(check["status"] == "FAIL" for check in checks),
        "checks": checks,
    }
    write_json(annotation / "reports" / "validation_report.json", report)
    write_json(annotation / "validation_report.json", report)

    core_artifacts = required_artifacts + [
        annotation / "validation_report.json",
        annotation / "reports" / "validation_report.json",
    ]
    manifest = {
        "dataset_id": "sqlmendrag-codex-dev-250",
        "dataset_version": "1.1.0",
        "annotation_main_version": "v1",
        "schema_version": "1.0.0",
        "purpose": "development_only",
        "split": "dev",
        "record_count": len(cases),
        "annotation_origin": "codex_machine_proposed",
        "human_verified": False,
        "eligible_for_assignment_final_eval": False,
        "generation_run_id": "codex-dev-20260828-01",
        "input_corpus": {
            "path": "construction/data/processed/corpus.jsonl",
            "sha256": corpus_sha256,
            "chunk_count": len(corpus),
            "validation_status": kb_report.get("status"),
        },
        "quotas": {
            "cases_per_dialect": 50,
            "dialect_sensitive_per_dialect_minimum": 15,
            "version_sensitive_per_dialect_minimum": 10,
            "documented_error_minimum": 30,
            "plausible_but_wrong_minimum": 50,
        },
        "artifacts": {
            str(path.relative_to(root)).replace("\\", "/"): (
                sha256_file(path) if path.is_file() else None
            )
            for path in core_artifacts
        },
        "validation_status": status,
    }
    write_json(annotation / "manifest.json", manifest)

    print(json.dumps({"status": status, "passed": report["checks_passed"], "failed": report["checks_failed"]}, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
