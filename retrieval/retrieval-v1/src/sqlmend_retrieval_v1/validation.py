"""Independent, disk-backed release validation for retrieval v1.

Unlike the online ranking path, this module is explicitly offline and may read
qrels in order to audit pool completeness and reproduce published metrics.  It
never feeds qrels or development-only fields back into retrieval.  Every PASS
is derived from current bytes; status strings stored in artifacts are treated
as claims to verify, not as evidence by themselves.
"""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from .audit import protected_snapshot, verify_static_lock
from .evaluation import METRIC_NAMES, evaluate_system
from .experiment import evaluate_acceptance
from .io import (
    group_run,
    load_json,
    load_jsonl,
    load_yaml,
    read_qrels,
    read_trec_run,
    sha256_file,
    validate_run,
)
from .manifest import (
    EVALUATION_LABEL,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    VALIDATION_RESULT_FILENAMES,
    build_manifest,
    release_source_snapshot,
)
from .models import CandidatePassage, RunEntry
from .pipeline import (
    BM25_RUN_SHA256,
    CORPUS_SHA256,
    DENSE_RUN_SHA256,
    EXPECTED_DIALECTS,
    HYBRID_RUN_SHA256,
    QRELS_SHA256,
    QUERY_SHA256,
    RUN_FILES,
    SERIALIZED_QUERY_SHA256,
    SYSTEM_CONFIG_FILES,
)
from .pool import FORMAL_SYSTEM_IDS, audit_pool, pool_expansion_artifact_data
from .query import (
    ALLOWED_SOURCE_FIELDS,
    FORBIDDEN_ONLINE_FIELDS,
    SERIALIZER_VERSION,
    project_online_queries,
    serialize_query,
)
from .ranking import reconstruct_rrf_candidates, render_passage, verify_frozen_hybrid_reconstruction


PASS = "PASS"
FAIL = "FAIL"
VALIDATION_SCHEMA_VERSION = "sqlmend-retrieval-v1-validation-v1"
EXPECTED_CORPUS_COUNT = 12_000
EXPECTED_QUERY_COUNT = 250
EXPECTED_QREL_COUNT = 23_452
EXPECTED_QREL_LABEL_COUNTS = {0: 20_154, 1: 2_839, 2: 459}
EXPECTED_DEPTH = 30
EXPECTED_DIALECT_COUNTS = {dialect: 2_400 for dialect in EXPECTED_DIALECTS}
EXPECTED_QUERY_DIALECT_COUNTS = {dialect: 50 for dialect in EXPECTED_DIALECTS}


class ReleaseValidationError(ValueError):
    """A concrete release invariant failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseValidationError(message)


def _relative(paths: Any, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(paths.root).resolve()).as_posix()
    except ValueError as exc:
        raise ReleaseValidationError(f"Path escapes repository root: {path}") from exc


def _tree_digest(files: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, observed in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(observed.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _finite_number(value: Any, label: str, *, nonnegative: bool = False) -> float:
    _require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value)),
        f"{label} must be a finite number",
    )
    number = float(value)
    _require(not nonnegative or number >= 0.0, f"{label} must be non-negative")
    return number


def _assert_deep_close(observed: Any, expected: Any, label: str, *, tolerance: float = 1e-12) -> None:
    """Compare JSON-like values, allowing only tiny floating round-off."""

    if isinstance(expected, Mapping):
        _require(isinstance(observed, Mapping), f"{label} must be a mapping")
        _require(set(observed) == set(expected), f"{label} keys differ")
        for key in expected:
            _assert_deep_close(observed[key], expected[key], f"{label}.{key}", tolerance=tolerance)
        return
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        _require(
            isinstance(observed, Sequence) and not isinstance(observed, (str, bytes, bytearray)),
            f"{label} must be a sequence",
        )
        _require(len(observed) == len(expected), f"{label} length differs")
        for index, (left, right) in enumerate(zip(observed, expected, strict=True)):
            _assert_deep_close(left, right, f"{label}[{index}]", tolerance=tolerance)
        return
    if (
        not isinstance(expected, bool)
        and isinstance(expected, (int, float))
        and not isinstance(observed, bool)
        and isinstance(observed, (int, float))
    ):
        left = _finite_number(observed, label)
        right = _finite_number(expected, label)
        _require(math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance), f"{label} differs")
        return
    _require(observed == expected, f"{label} differs: {observed!r} != {expected!r}")


def _load_required_json(path: Path) -> dict[str, Any]:
    _require(path.is_file(), f"Required JSON artifact is missing: {path}")
    return load_json(path)


def _validate_static_lock(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = verify_static_lock(paths)
    errors = result.get("errors")
    _require(isinstance(errors, list) and not errors, "Baseline static-lock recomputation has errors")
    _require(not result.get("protected_git_status"), "Git reports protected-path changes")
    _require(result.get("status") == PASS, "Baseline static-lock status is not PASS")

    lock = _load_required_json(Path(paths.config) / "baseline_lock.json")
    checked: dict[str, str] = {}
    for section in ("critical_files", "input_files"):
        entries = lock.get(section)
        _require(isinstance(entries, Mapping), f"baseline lock {section} is missing")
        for relative, expected in entries.items():
            path = Path(paths.root) / str(relative)
            _require(path.is_file(), f"Locked file is missing: {relative}")
            observed = sha256_file(path)
            checked[str(relative)] = observed
            _require(observed == expected, f"Locked hash differs: {relative}")
    _require(result.get("checked_locked_files") == checked, "Static-lock checked-file evidence differs")
    context["static_lock"] = result
    return {
        "tracked_baseline_file_count": result.get("tracked_baseline_file_count"),
        "tracked_baseline_tree_sha256": result.get("tracked_baseline_tree_sha256"),
        "checked_locked_file_count": len(checked),
    }


def _validate_snapshot_payload(payload: Mapping[str, Any], label: str) -> dict[str, str]:
    snapshot = payload.get("snapshot")
    _require(isinstance(snapshot, Mapping), f"{label} snapshot is missing")
    files = snapshot.get("files")
    _require(isinstance(files, Mapping), f"{label} snapshot files are missing")
    normalized: dict[str, str] = {}
    for relative, observed in files.items():
        _require(isinstance(relative, str) and relative, f"{label} has an invalid path")
        _require(
            isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
            f"{label} has an invalid SHA256 for {relative}",
        )
        normalized[relative] = observed
    _require(snapshot.get("file_count") == len(normalized), f"{label} file_count differs")
    _require(snapshot.get("tree_sha256") == _tree_digest(normalized), f"{label} tree hash differs")
    return normalized


def _validate_protected_snapshots(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    before = _load_required_json(Path(paths.reports) / "protected_paths_before.json")
    after = _load_required_json(Path(paths.reports) / "protected_paths_after.json")
    _require(before.get("phase") == "before", "Protected before audit has the wrong phase")
    _require(after.get("phase") == "after", "Protected after audit has the wrong phase")
    before_files = _validate_snapshot_payload(before, "protected before")
    after_files = _validate_snapshot_payload(after, "protected after")
    current = protected_snapshot(paths)
    current_files = current.get("files")
    _require(isinstance(current_files, Mapping), "Current protected snapshot is invalid")
    _require(before_files == after_files, "Protected bytes changed between before and after audits")
    _require(after_files == current_files, "Protected bytes changed after the after audit")
    _require(not before.get("errors") and not after.get("errors"), "Protected audit recorded errors")
    _require(before.get("status") == PASS and after.get("status") == PASS, "Protected audit status is not PASS")
    _require(after.get("protected_paths_unchanged") is True, "After audit does not assert unchanged bytes")
    for label, payload in (("before", before), ("after", after)):
        static = payload.get("static_lock")
        _require(isinstance(static, Mapping), f"Protected {label} static-lock evidence is missing")
        _require(not static.get("errors"), f"Protected {label} static-lock evidence has errors")
        _require(static.get("status") == PASS, f"Protected {label} static-lock evidence is not PASS")
    context["protected_snapshot"] = current
    return {
        "file_count": current.get("file_count"),
        "tree_sha256": current.get("tree_sha256"),
        "before_after_current_identical": True,
    }


def _validate_inputs(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    expected_hashes = {
        Path(paths.corpus): CORPUS_SHA256,
        Path(paths.queries): QUERY_SHA256,
        Path(paths.qrels): QRELS_SHA256,
        Path(paths.baseline_bm25_run): BM25_RUN_SHA256,
        Path(paths.baseline_dense_run): DENSE_RUN_SHA256,
        Path(paths.baseline_run): HYBRID_RUN_SHA256,
        Path(paths.baseline_serialized_queries): SERIALIZED_QUERY_SHA256,
    }
    observed_hashes: dict[str, str] = {}
    for path, expected in expected_hashes.items():
        _require(path.is_file(), f"Required input is missing: {path}")
        observed = sha256_file(path)
        observed_hashes[_relative(paths, path)] = observed
        _require(observed == expected, f"Input hash differs: {_relative(paths, path)}")

    corpus_records = load_jsonl(Path(paths.corpus))
    _require(len(corpus_records) == EXPECTED_CORPUS_COUNT, "Corpus count is not 12000")
    corpus_by_id: dict[str, dict[str, Any]] = {}
    corpus_dialects: Counter[str] = Counter()
    for record in corpus_records:
        chunk_id = record.get("chunk_id")
        _require(
            isinstance(chunk_id, str) and chunk_id and chunk_id not in corpus_by_id,
            f"Invalid or duplicate corpus chunk_id: {chunk_id!r}",
        )
        _require(
            isinstance(record.get("text"), str) and bool(record["text"].strip()),
            f"Corpus chunk has empty text: {chunk_id}",
        )
        dialect = record.get("dialect")
        _require(dialect in EXPECTED_DIALECTS, f"Corpus chunk has invalid dialect: {chunk_id}")
        corpus_dialects[str(dialect)] += 1
        corpus_by_id[chunk_id] = record
    _require(dict(corpus_dialects) == EXPECTED_DIALECT_COUNTS, "Corpus dialect counts differ")

    raw_queries = load_jsonl(Path(paths.queries))
    _require(len(raw_queries) == EXPECTED_QUERY_COUNT, "Query count is not 250")
    query_ids: list[str] = []
    query_dialects: Counter[str] = Counter()
    for record in raw_queries:
        query_id = record.get("query_id")
        _require(isinstance(query_id, str) and query_id, "Query has an invalid query_id")
        query_ids.append(query_id)
        query_dialects[str(record.get("dialect"))] += 1
    _require(len(set(query_ids)) == len(query_ids), "Query IDs are not unique")
    _require(dict(query_dialects) == EXPECTED_QUERY_DIALECT_COUNTS, "Query dialect counts differ")

    qrels = read_qrels(Path(paths.qrels))
    qrel_count = sum(len(values) for values in qrels.values())
    labels = Counter(value for judgments in qrels.values() for value in judgments.values())
    _require(qrel_count == EXPECTED_QREL_COUNT, "Qrel count differs")
    _require(dict(labels) == EXPECTED_QREL_LABEL_COUNTS, "Qrel label counts differ")
    _require(set(qrels) == set(query_ids), "Qrel query coverage differs")
    unknown_qrel_chunks = {
        chunk_id for judgments in qrels.values() for chunk_id in judgments if chunk_id not in corpus_by_id
    }
    _require(not unknown_qrel_chunks, "Qrels contain chunks outside the corpus")

    context.update(
        {
            "corpus_records": corpus_records,
            "corpus_by_id": corpus_by_id,
            "raw_queries": raw_queries,
            "query_ids": tuple(sorted(query_ids)),
            "qrels": qrels,
        }
    )
    return {
        "hashes": observed_hashes,
        "corpus_record_count": len(corpus_records),
        "query_count": len(raw_queries),
        "qrel_count": qrel_count,
        "qrel_label_counts": dict(sorted(labels.items())),
    }


def _canonical_serialized_bytes(raw_queries: Sequence[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
    records = [
        serialize_query(record).to_dict()
        for record in sorted(raw_queries, key=lambda item: item["query_id"])
    ]
    rendered = "".join(
        json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
        for record in records
    ).encode("utf-8")
    return rendered, records


def _validate_serialized_queries(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    raw_queries = context.get("raw_queries")
    _require(isinstance(raw_queries, list), "Input validation did not load queries")
    expected_bytes, expected_records = _canonical_serialized_bytes(raw_queries)
    frozen_bytes = Path(paths.baseline_serialized_queries).read_bytes()
    release_bytes = Path(paths.serialized_queries).read_bytes()
    _require(expected_bytes == frozen_bytes, "Fresh safe serialization differs from frozen bytes")
    _require(release_bytes == frozen_bytes, "Retrieval-v1 serialized bytes differ from frozen bytes")
    _require(sha256_file(Path(paths.serialized_queries)) == SERIALIZED_QUERY_SHA256, "Serialized hash differs")

    observed_records = load_jsonl(Path(paths.serialized_queries))
    _require(observed_records == expected_records, "Serialized records differ from safe reserialization")
    allowed_record_keys = {
        "query_id",
        "source_fields_used",
        "serialized_text",
        "serialized_text_sha256",
        "serializer_version",
    }
    for record in observed_records:
        _require(set(record) == allowed_record_keys, "Serialized query has an unexpected field")
        source_fields = record.get("source_fields_used")
        _require(isinstance(source_fields, list), "Serialized source_fields_used is invalid")
        _require(set(source_fields) <= ALLOWED_SOURCE_FIELDS, "Serialized query uses a forbidden source field")
        _require(not set(source_fields).intersection(FORBIDDEN_ONLINE_FIELDS), "Forbidden field entered serialization")
        _require(record.get("serializer_version") == SERIALIZER_VERSION, "Serializer version differs")

    online_values = project_online_queries(raw_queries)
    online_queries = {query.query_id: query for query in online_values}
    _require(set(online_queries) == set(context["query_ids"]), "Online query coverage differs")
    context["online_queries"] = online_queries
    return {
        "query_count": len(observed_records),
        "sha256": sha256_file(Path(paths.serialized_queries)),
        "frozen_byte_identical": True,
        "allowed_source_fields": sorted(ALLOWED_SOURCE_FIELDS),
    }


def _corpus_passages(corpus_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, CandidatePassage]:
    result: dict[str, CandidatePassage] = {}
    for chunk_id, record in sorted(corpus_by_id.items()):
        result[chunk_id] = CandidatePassage(
            chunk_id=chunk_id,
            dialect=record.get("dialect") if isinstance(record.get("dialect"), str) else None,
            version=record.get("version") if isinstance(record.get("version"), str) else None,
            version_min=record.get("version_min") if isinstance(record.get("version_min"), str) else None,
            version_max=record.get("version_max") if isinstance(record.get("version_max"), str) else None,
            version_status=str(record.get("version_status") or "unknown"),
            source_type=record.get("source_type") if isinstance(record.get("source_type"), str) else None,
            title=record.get("title") if isinstance(record.get("title"), str) else None,
            section=record.get("section") if isinstance(record.get("section"), str) else None,
            text=render_passage(record),
            baseline_rank=1,
            baseline_score=0.0,
        )
    return result


def _system_run_path(paths: Any, system_id: str) -> Path:
    if system_id == "hybrid_rrf_frozen_control_v1":
        return Path(paths.baseline_run)
    return Path(paths.runs) / RUN_FILES[system_id]


def _validate_configs_and_runs(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    query_ids = context.get("query_ids")
    corpus_by_id = context.get("corpus_by_id")
    _require(isinstance(query_ids, tuple) and isinstance(corpus_by_id, Mapping), "Inputs are unavailable")

    expected_ids = tuple(FORMAL_SYSTEM_IDS)
    _require(tuple(SYSTEM_CONFIG_FILES) == expected_ids, "Five-system config order differs")
    configs: dict[str, dict[str, Any]] = {}
    run_tags: set[str] = set()
    for system_id in expected_ids:
        path = Path(paths.system_configs) / SYSTEM_CONFIG_FILES[system_id]
        config = load_yaml(path)
        _require(config.get("system_id") == system_id, f"Config ID differs for {system_id}")
        tag = config.get("run_tag")
        _require(isinstance(tag, str) and tag, f"Run tag is invalid for {system_id}")
        _require(tag not in run_tags, f"Run tag is not independent for {system_id}")
        run_tags.add(tag)
        configs[system_id] = config
    control = configs[expected_ids[0]]
    _require(control.get("run_path") == _relative(paths, Path(paths.baseline_run)), "Control does not reference frozen run")
    _require(control.get("required_sha256") == HYBRID_RUN_SHA256, "Control frozen hash differs")

    component_runs = {
        "bm25": validate_run(
            read_trec_run(Path(paths.baseline_bm25_run)),
            expected_query_ids=query_ids,
            known_chunk_ids=corpus_by_id,
            expected_run_tag="bm25_formal_v1",
            depth=EXPECTED_DEPTH,
        ),
        "dense": validate_run(
            read_trec_run(Path(paths.baseline_dense_run)),
            expected_query_ids=query_ids,
            known_chunk_ids=corpus_by_id,
            expected_run_tag="dense_formal_v1",
            depth=EXPECTED_DEPTH,
        ),
    }
    candidates = reconstruct_rrf_candidates(
        component_runs["bm25"], component_runs["dense"], corpus_by_id
    )
    candidate_pairs = {
        (query_id, state.passage.chunk_id)
        for query_id, states in candidates.items()
        for state in states
    }
    union_depths = [len(states) for states in candidates.values()]
    _require(all(EXPECTED_DEPTH <= depth <= 60 for depth in union_depths), "Candidate union depth is invalid")

    runs: dict[str, list[RunEntry]] = {}
    for system_id in expected_ids:
        run_path = _system_run_path(paths, system_id)
        run = validate_run(
            read_trec_run(run_path),
            expected_query_ids=query_ids,
            known_chunk_ids=corpus_by_id,
            expected_run_tag=str(configs[system_id]["run_tag"]),
            depth=EXPECTED_DEPTH,
        )
        _require(
            all((row.query_id, row.chunk_id) in candidate_pairs for row in run),
            f"{system_id} contains a candidate outside BM25 union Dense",
        )
        runs[system_id] = run
    verify_frozen_hybrid_reconstruction(candidates, runs[expected_ids[0]])

    provenance_counts: dict[str, int] = {}
    for system_id in expected_ids:
        run_path = _system_run_path(paths, system_id)
        provenance_path = run_path.with_suffix(".provenance.jsonl")
        _require(provenance_path.is_file(), f"Provenance is missing for {system_id}")
        if system_id == expected_ids[0]:
            # The frozen provenance has its own Phase 6 schema and is protected
            # by the static lock/tree audit.  Its byte presence is sufficient
            # here; new provenance is additionally bound row-by-row below.
            provenance_counts[system_id] = sum(
                1 for line in provenance_path.read_text(encoding="utf-8").splitlines() if line
            )
            continue
        provenance = load_jsonl(provenance_path)
        _require(len(provenance) == EXPECTED_QUERY_COUNT * EXPECTED_DEPTH, f"Provenance count differs for {system_id}")
        run_pairs = {(row.query_id, row.chunk_id) for row in runs[system_id]}
        provenance_pairs = {
            (row.get("query_id"), row.get("chunk_id")) for row in provenance
        }
        _require(provenance_pairs == run_pairs, f"Provenance rows differ from run for {system_id}")
        provenance_counts[system_id] = len(provenance)

    passages = _corpus_passages(corpus_by_id)
    context.update(
        {
            "configs": configs,
            "runs": runs,
            "component_runs": component_runs,
            "candidate_pairs": candidate_pairs,
            "corpus_passages": passages,
        }
    )
    return {
        "system_ids": list(expected_ids),
        "run_tags": {system_id: configs[system_id]["run_tag"] for system_id in expected_ids},
        "query_count_per_run": EXPECTED_QUERY_COUNT,
        "depth": EXPECTED_DEPTH,
        "candidate_union_minimum": min(union_depths),
        "candidate_union_maximum": max(union_depths),
        "all_run_candidates_in_component_union": True,
        "provenance_row_counts": provenance_counts,
    }


def _validate_determinism(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    runs = context.get("runs")
    _require(isinstance(runs, Mapping), "Formal runs are unavailable")
    v1_path = Path(paths.evaluation) / "run_determinism.json"
    v1_payload = _load_required_json(v1_path)
    v1_systems = v1_payload.get("systems")
    _require(isinstance(v1_systems, Mapping), "V1 determinism systems mapping is missing")
    expected_new = set(FORMAL_SYSTEM_IDS[1:])
    _require(set(v1_systems) == expected_new, "V1 determinism system coverage differs")

    verified: dict[str, str] = {}
    for system_id in FORMAL_SYSTEM_IDS[1:]:
        evidence = v1_systems[system_id]
        _require(isinstance(evidence, Mapping), f"Determinism evidence is invalid for {system_id}")
        actual = sha256_file(_system_run_path(paths, system_id))
        _require(evidence.get("byte_identical") is True, f"Repeated run bytes differ for {system_id}")
        _require(evidence.get("provenance_identical") is True, f"Repeated provenance differs for {system_id}")
        _require(evidence.get("first_sha256") == actual, f"First run hash differs for {system_id}")
        _require(evidence.get("second_sha256") == actual, f"Second run hash differs for {system_id}")
        verified[system_id] = actual

    baseline_payload = _load_required_json(
        Path(paths.baseline) / "evaluation" / "run_determinism.json"
    )
    baseline = baseline_payload.get("hybrid")
    _require(isinstance(baseline, Mapping), "Frozen Hybrid determinism evidence is missing")
    actual_baseline = sha256_file(Path(paths.baseline_run))
    _require(baseline.get("byte_identical") is True, "Frozen Hybrid repeated bytes differ")
    _require(baseline.get("first_sha256") == actual_baseline, "Frozen Hybrid first hash differs")
    _require(baseline.get("second_sha256") == actual_baseline, "Frozen Hybrid second hash differs")
    verified[FORMAL_SYSTEM_IDS[0]] = actual_baseline
    context["deterministic_run_hashes"] = verified
    return {"systems": dict(sorted(verified.items()))}


def _validate_pool(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    runs = context.get("runs")
    qrels = context.get("qrels")
    passages = context.get("corpus_passages")
    _require(isinstance(runs, Mapping) and isinstance(qrels, Mapping) and isinstance(passages, Mapping), "Pool inputs are unavailable")
    recomputed = audit_pool(
        runs,
        qrels,
        passages,
        system_ids=tuple(FORMAL_SYSTEM_IDS),
        depth=EXPECTED_DEPTH,
    )
    expansion_records, recomputed_summary = pool_expansion_artifact_data(recomputed)
    summary = _load_required_json(Path(paths.pool_expansion) / "pool_expansion_summary.json")
    coverage = _load_required_json(Path(paths.evaluation) / "judged_coverage.json")
    _assert_deep_close(summary, recomputed_summary, "pool_expansion_summary")
    _assert_deep_close(coverage, recomputed_summary, "judged_coverage")
    _require(recomputed.get("evaluation_integrity_status") == PASS, "Pool integrity is not PASS")
    _require(recomputed.get("pool_expansion_required") is False, "Pool expansion is required")
    _require(recomputed.get("unjudged_top30_occurrence_count") == 0, "Pool has unjudged top-30 pairs")
    _require(recomputed.get("pool_expansion_record_count") == 0, "Pool expansion record count is nonzero")
    _require(not expansion_records, "Recomputed pool expansion records are non-empty")
    per_system = recomputed.get("per_system")
    _require(isinstance(per_system, Mapping) and set(per_system) == set(FORMAL_SYSTEM_IDS), "Pool system coverage differs")
    for system_id in FORMAL_SYSTEM_IDS:
        values = per_system[system_id]
        _require(values.get("Judged@30") == 1.0, f"{system_id} Judged@30 is not 1.0")
        _require(values.get("query_count") == EXPECTED_QUERY_COUNT, f"{system_id} pool query count differs")
    expansion_path = Path(paths.pool_expansion) / "pool_expansion_required.jsonl"
    _require(expansion_path.is_file() and expansion_path.read_bytes() == b"", "Pool expansion artifact is not empty")
    context["pool"] = recomputed
    return {
        "evaluation_integrity_status": PASS,
        "Judged@30": 1.0,
        "unjudged_top30_occurrence_count": 0,
        "systems": list(FORMAL_SYSTEM_IDS),
    }


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    _require(path.is_file(), f"Required CSV artifact is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        _require(reader.fieldnames is not None, f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _validate_metric_csvs(paths: Any, results: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    per_fields, per_rows = _read_csv(Path(paths.evaluation) / "per_query_metrics.csv")
    expected_per_fields = ["system_id", "query_id", *METRIC_NAMES]
    _require(per_fields == expected_per_fields, "Per-query CSV header differs")
    expected_per_rows = [
        (system_id, query_id, results[system_id]["per_query"][query_id])
        for system_id in FORMAL_SYSTEM_IDS
        for query_id in sorted(results[system_id]["per_query"])
    ]
    _require(len(per_rows) == len(expected_per_rows), "Per-query CSV row count differs")
    for index, (observed, (system_id, query_id, metrics)) in enumerate(
        zip(per_rows, expected_per_rows, strict=True)
    ):
        _require(observed["system_id"] == system_id and observed["query_id"] == query_id, f"Per-query CSV identity differs at row {index}")
        for metric in METRIC_NAMES:
            _assert_deep_close(float(observed[metric]), metrics[metric], f"per_query_csv[{index}].{metric}")

    slice_fields, slice_rows = _read_csv(Path(paths.evaluation) / "slice_metrics.csv")
    expected_slice_fields = [
        "system_id",
        "slice_name",
        "slice_value",
        "source_field",
        "query_count",
        *METRIC_NAMES,
        "estimate_warning",
    ]
    _require(slice_fields == expected_slice_fields, "Slice CSV header differs")
    expected_slices = [row for system_id in FORMAL_SYSTEM_IDS for row in results[system_id]["slices"]]
    _require(len(slice_rows) == len(expected_slices), "Slice CSV row count differs")
    for index, (observed, expected) in enumerate(zip(slice_rows, expected_slices, strict=True)):
        for field in ("system_id", "slice_name", "slice_value", "source_field"):
            _require(observed[field] == str(expected[field]), f"Slice CSV {field} differs at row {index}")
        _require(int(observed["query_count"]) == expected["query_count"], f"Slice CSV query_count differs at row {index}")
        for metric in METRIC_NAMES:
            expected_value = expected[metric]
            if expected_value is None:
                _require(observed[metric] == "", f"Slice CSV undefined metric differs at row {index}")
            else:
                _assert_deep_close(float(observed[metric]), expected_value, f"slice_csv[{index}].{metric}")
        warning = "" if expected["estimate_warning"] is None else str(expected["estimate_warning"])
        _require(observed["estimate_warning"] == warning, f"Slice CSV warning differs at row {index}")
    return {"per_query_row_count": len(per_rows), "slice_row_count": len(slice_rows)}


def _validate_metrics(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    runs = context.get("runs")
    qrels = context.get("qrels")
    online_queries = context.get("online_queries")
    passages = context.get("corpus_passages")
    raw_queries = context.get("raw_queries")
    _require(
        isinstance(runs, Mapping)
        and isinstance(qrels, Mapping)
        and isinstance(online_queries, Mapping)
        and isinstance(passages, Mapping)
        and isinstance(raw_queries, list),
        "Metric inputs are unavailable",
    )
    results = {
        system_id: evaluate_system(
            runs[system_id],
            qrels,
            online_queries,
            passages,
            raw_queries,
            system_id=system_id,
        )
        for system_id in FORMAL_SYSTEM_IDS
    }
    overall = _load_required_json(Path(paths.evaluation) / "overall_metrics.json")
    _require(overall.get("schema_version") == "sqlmend-retrieval-v1-overall-metrics-v1", "Overall metric schema differs")
    _require(overall.get("evaluation_label") == EVALUATION_LABEL, "Overall evaluation label differs")
    _assert_deep_close(
        overall.get("systems"),
        {system_id: results[system_id]["overall"] for system_id in FORMAL_SYSTEM_IDS},
        "overall_metrics.systems",
    )

    comparison = _load_required_json(Path(paths.evaluation) / "comparison_results.json")
    expected_comparison = {
        "schema_version": "sqlmend-retrieval-v1-comparison-v1",
        "evaluation_label": EVALUATION_LABEL,
        "system_order": list(FORMAL_SYSTEM_IDS),
        "systems": {
            system_id: {
                "overall": results[system_id]["overall"],
                "slices": results[system_id]["slices"],
            }
            for system_id in FORMAL_SYSTEM_IDS
        },
    }
    _assert_deep_close(comparison, expected_comparison, "comparison_results")
    csv_counts = _validate_metric_csvs(paths, results)
    context["evaluation_results"] = results
    return {
        "system_count": len(results),
        "query_count_per_system": EXPECTED_QUERY_COUNT,
        "metrics": list(METRIC_NAMES),
        **csv_counts,
    }


def _all_passed_flags(value: Any, path: str = "acceptance") -> list[str]:
    failures: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "passed" and child is not True:
                failures.append(child_path)
            else:
                failures.extend(_all_passed_flags(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            failures.extend(_all_passed_flags(child, f"{path}[{index}]"))
    return failures


def _validate_acceptance(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    results = context.get("evaluation_results")
    _require(isinstance(results, Mapping), "Recomputed metrics are unavailable")
    config = load_yaml(Path(paths.config) / "evaluation.yaml")
    recomputed = evaluate_acceptance(results, config)
    published = _load_required_json(Path(paths.evaluation) / "acceptance.json")
    _assert_deep_close(published, recomputed, "acceptance")
    failed_flags = _all_passed_flags(recomputed)
    _require(not failed_flags, f"Acceptance contains failed gates: {failed_flags}")
    statuses = recomputed.get("status")
    _require(
        isinstance(statuses, Mapping)
        and set(statuses) == {"phase7", "phase8", "phase9", "final"}
        and all(value == PASS for value in statuses.values()),
        "Acceptance phase statuses are not all PASS",
    )
    _require(recomputed.get("retrieval_quality_status") == PASS, "Retrieval quality status is not PASS")

    status = _load_required_json(Path(paths.evaluation) / "evaluation_status.json")
    _require(status.get("evaluation_integrity_status") == PASS, "Evaluation integrity status is not PASS")
    _require(status.get("retrieval_quality_status") == PASS, "Published retrieval quality status is not PASS")
    _require(status.get("machine_proposed_development_only") is True, "Evaluation data label is unsafe")
    _require(status.get("Judged@30") == 1.0, "Published Judged@30 is not 1.0")
    context["acceptance"] = recomputed
    return {"phase_status": dict(statuses), "retrieval_quality_status": PASS}


def _latency_summary(value: Any, label: str) -> dict[str, float]:
    _require(isinstance(value, Mapping), f"{label} latency summary is missing")
    result = {
        field: _finite_number(value.get(field), f"{label}.{field}", nonnegative=True)
        for field in ("mean_ms", "p50_ms", "p95_ms")
    }
    _require(result["p50_ms"] <= result["p95_ms"] + 1e-12, f"{label} P50 exceeds P95")
    return result


def _assert_summary_sum(observed: Any, parts: Sequence[Mapping[str, float]], label: str) -> None:
    summary = _latency_summary(observed, label)
    for field in summary:
        expected = math.fsum(part[field] for part in parts)
        _require(math.isclose(summary[field], expected, rel_tol=0.0, abs_tol=1e-9), f"{label}.{field} formula differs")


def _validate_latency(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    del context
    payload = _load_required_json(Path(paths.reports) / "latency.json")
    _require(payload.get("schema_version") == "sqlmend-retrieval-v1-latency-v1", "Latency schema differs")
    _require(payload.get("evaluation_label") == EVALUATION_LABEL, "Latency evaluation label differs")
    _require(payload.get("machine_proposed_development_only") is True, "Latency data label is unsafe")
    _require(payload.get("query_count") == EXPECTED_QUERY_COUNT, "Latency query count differs")
    repetitions = payload.get("repetitions")
    _require(isinstance(repetitions, int) and not isinstance(repetitions, bool) and repetitions > 0, "Latency repetitions is invalid")
    _require(payload.get("each_query_measured_at_least_once") is True, "Latency did not measure every query")

    expected_stage_ids = {
        "dialect_metadata_rerank": FORMAL_SYSTEM_IDS[1],
        "version_metadata_rerank": FORMAL_SYSTEM_IDS[2],
        "dialect_version_metadata_rerank": FORMAL_SYSTEM_IDS[3],
        "lexical_reranker": FORMAL_SYSTEM_IDS[4],
    }
    measured = payload.get("measured_incremental_online_latency")
    _require(isinstance(measured, Mapping) and set(measured) == set(expected_stage_ids), "Latency stage coverage differs")
    stage_summaries: dict[str, dict[str, float]] = {}
    for stage, system_id in expected_stage_ids.items():
        row = measured[stage]
        _require(isinstance(row, Mapping), f"Latency stage is invalid: {stage}")
        _require(row.get("system_id") == system_id, f"Latency stage system differs: {stage}")
        _require(row.get("latency_type") == "measured_increment", f"Latency stage type differs: {stage}")
        _require(row.get("query_count") == EXPECTED_QUERY_COUNT, f"Latency stage query count differs: {stage}")
        _require(row.get("repetitions") == repetitions, f"Latency repetitions differ: {stage}")
        _require(row.get("sample_count") == EXPECTED_QUERY_COUNT * repetitions, f"Latency sample count differs: {stage}")
        stage_summaries[stage] = _latency_summary(row, stage)

    systems = payload.get("systems")
    _require(
        isinstance(systems, Mapping) and set(systems) == set(FORMAL_SYSTEM_IDS),
        "Latency system coverage differs",
    )
    frozen_system = systems[FORMAL_SYSTEM_IDS[0]]
    _require(frozen_system.get("total_latency_type") == "frozen_measured_reference", "Frozen latency type differs")
    baseline = _latency_summary(frozen_system.get("total_latency_ms"), "frozen total")
    reference = payload.get("frozen_hybrid_reference")
    _require(isinstance(reference, Mapping), "Frozen latency reference is missing")
    _require(reference.get("system_id") == FORMAL_SYSTEM_IDS[0], "Frozen latency reference ID differs")
    _require(reference.get("latency_type") == "frozen_measured_reference", "Frozen reference type differs")
    _assert_deep_close(reference.get("total_latency_ms"), baseline, "frozen_hybrid_reference.total_latency_ms")

    system_stage = {
        FORMAL_SYSTEM_IDS[1]: "dialect_metadata_rerank",
        FORMAL_SYSTEM_IDS[2]: "version_metadata_rerank",
        FORMAL_SYSTEM_IDS[3]: "dialect_version_metadata_rerank",
    }
    for system_id, stage in system_stage.items():
        row = systems[system_id]
        _require(row.get("total_latency_type") == "estimate", f"Latency total type differs for {system_id}")
        _assert_deep_close(row.get("incremental_online_latency_ms"), stage_summaries[stage], f"{system_id}.increment")
        _assert_summary_sum(row.get("total_latency_estimate_ms"), (baseline, stage_summaries[stage]), f"{system_id}.total")

    final = systems[FORMAL_SYSTEM_IDS[4]]
    _require(final.get("total_latency_type") == "estimate", "Final latency total type differs")
    final_parts = (
        stage_summaries["dialect_version_metadata_rerank"],
        stage_summaries["lexical_reranker"],
    )
    _assert_summary_sum(final.get("incremental_online_latency_ms"), final_parts, "final.increment")
    _assert_summary_sum(final.get("total_latency_estimate_ms"), (baseline, *final_parts), "final.total")
    return {
        "query_count": EXPECTED_QUERY_COUNT,
        "repetitions": repetitions,
        "systems": {
            system_id: (
                systems[system_id].get("total_latency_ms")
                if system_id == FORMAL_SYSTEM_IDS[0]
                else systems[system_id].get("total_latency_estimate_ms")
            )
            for system_id in FORMAL_SYSTEM_IDS
        },
    }


def _validate_test_evidence(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    del context
    evidence = _load_required_json(Path(paths.reports) / "test_results.json")
    current = release_source_snapshot(paths)
    before_hash = evidence.get("source_tree_sha256_before", evidence.get("source_tree_sha256"))
    after_hash = evidence.get("source_tree_sha256_after")
    _require(isinstance(before_hash, str), "Test evidence has no before source hash")
    _require(isinstance(after_hash, str), "Test evidence has no after source hash")
    _require(before_hash == after_hash, "Source changed during the recorded test run")
    _require(after_hash == current["tree_sha256"], "Current source differs from tested source")
    before_count = evidence.get("source_file_count_before", evidence.get("source_file_count"))
    after_count = evidence.get("source_file_count_after", evidence.get("source_file_count"))
    _require(before_count == current["file_count"], "Test before source file count differs")
    _require(after_count == current["file_count"], "Test after source file count differs")
    _require(evidence.get("source_stable_during_tests") is True, "Test evidence says source was unstable")
    _require(evidence.get("status") == PASS, "Test evidence status is not PASS")
    _require(evidence.get("returncode") == 0, "Recorded test command failed")
    command = evidence.get("command")
    _require(
        (isinstance(command, str) and "pytest" in command)
        or (isinstance(command, list) and any("pytest" in str(value) for value in command)),
        "Test evidence command is not pytest",
    )
    stdout = evidence.get("stdout")
    _require(isinstance(stdout, str) and re.search(r"\b\d+ passed\b", stdout) is not None, "Test evidence has no passing pytest summary")
    return {
        "source_file_count": current["file_count"],
        "source_tree_sha256": current["tree_sha256"],
        "before_after_current_identical": True,
    }


def _validate_manifest_hashes(paths: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
    groups = manifest.get("artifact_groups")
    _require(isinstance(groups, Mapping) and groups, "Manifest artifact groups are missing")
    all_files: dict[str, str] = {}
    root = Path(paths.root).resolve()
    release = Path(paths.release).resolve()
    for group_name, snapshot in groups.items():
        _require(isinstance(snapshot, Mapping), f"Manifest group is invalid: {group_name}")
        files = snapshot.get("files")
        _require(isinstance(files, Mapping), f"Manifest group files are missing: {group_name}")
        normalized: dict[str, str] = {}
        for relative, expected in files.items():
            _require(isinstance(relative, str) and relative, f"Manifest path is invalid in {group_name}")
            _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) is not None, f"Manifest hash is invalid: {relative}")
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ReleaseValidationError(f"Manifest path escapes root: {relative}") from exc
            try:
                release_relative = path.relative_to(release).as_posix().casefold()
            except ValueError:
                release_relative = None
            if release_relative is not None:
                _require(release_relative != MANIFEST_FILENAME, "Manifest includes itself")
                lowered_name = path.name.casefold()
                _require(lowered_name not in VALIDATION_RESULT_FILENAMES, "Manifest includes validation results")
                _require(not lowered_name.startswith("validation_results."), "Manifest includes validation results")
                _require(not any(part.casefold() in {"__pycache__", ".pytest_cache", "model_cache", "cache"} for part in path.parts), f"Manifest includes a cache file: {relative}")
                _require(not lowered_name.endswith((".tmp", ".temp", ".pyc", ".pyo", ".swp", "~")), f"Manifest includes a temporary file: {relative}")
            _require(path.is_file(), f"Manifest artifact is missing: {relative}")
            observed = sha256_file(path)
            _require(observed == expected, f"Manifest hash differs: {relative}")
            normalized[relative] = expected
            previous = all_files.setdefault(relative, expected)
            _require(previous == expected, f"Manifest records conflicting hashes: {relative}")
        _require(snapshot.get("file_count") == len(normalized), f"Manifest group file_count differs: {group_name}")
        _require(snapshot.get("tree_sha256") == _tree_digest(normalized), f"Manifest group tree hash differs: {group_name}")
    _require(manifest.get("artifact_file_count") == len(all_files), "Manifest artifact_file_count differs")
    _require(manifest.get("artifact_tree_sha256") == _tree_digest(all_files), "Manifest artifact tree hash differs")
    return {"file_count": len(all_files), "tree_sha256": _tree_digest(all_files)}


def _validate_manifest(paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    del context
    path = Path(paths.release) / MANIFEST_FILENAME
    manifest = _load_required_json(path)
    _require(manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION, "Manifest schema differs")
    _require(manifest.get("evaluation_label") == EVALUATION_LABEL, "Manifest evaluation label differs")
    _require(manifest.get("machine_proposed_development_only") is True, "Manifest data label is unsafe")
    _require(manifest.get("system_order") == list(FORMAL_SYSTEM_IDS), "Manifest system order differs")
    systems = manifest.get("systems")
    _require(isinstance(systems, list) and len(systems) == 5, "Manifest does not record five systems")
    _require([row.get("system_id") for row in systems] == list(FORMAL_SYSTEM_IDS), "Manifest system IDs differ")
    _require(systems[0].get("frozen_baseline_reference") is True, "Manifest control is not a frozen reference")
    _require(systems[0].get("run", {}).get("path") == _relative(paths, Path(paths.baseline_run)), "Manifest control path differs")
    _require(all(row.get("frozen_baseline_reference") is False for row in systems[1:]), "A new system is mislabeled frozen")
    hash_details = _validate_manifest_hashes(paths, manifest)
    rebuilt = build_manifest(paths)
    _assert_deep_close(manifest, rebuilt, "manifest", tolerance=0.0)
    return {**hash_details, "rebuilt_byte_contract_identical": True}


CheckFunction = Callable[[Any, dict[str, Any]], dict[str, Any]]


def _run_check(name: str, function: CheckFunction, paths: Any, context: dict[str, Any]) -> dict[str, Any]:
    try:
        details = function(paths, context)
        return {"name": name, "status": PASS, "details": details, "errors": []}
    except Exception as exc:  # A validation report must surface every failed invariant.
        return {
            "name": name,
            "status": FAIL,
            "details": {},
            "errors": [f"{type(exc).__name__}: {exc}"],
        }


def validate_release(paths: Any) -> dict[str, Any]:
    """Independently validate all formal retrieval-v1 release artifacts.

    The function is read-only and always returns an explicit PASS/FAIL report.
    Failed prerequisites are reported as failed dependent checks rather than
    silently skipped.
    """

    context: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    definitions: tuple[tuple[str, CheckFunction], ...] = (
        ("baseline_static_lock", _validate_static_lock),
        ("protected_before_after_current", _validate_protected_snapshots),
        ("input_hashes_and_counts", _validate_inputs),
        ("safe_serialized_queries", _validate_serialized_queries),
        ("five_configs_runs_and_candidate_union", _validate_configs_and_runs),
        ("duplicate_run_determinism", _validate_determinism),
        ("pool_completeness", _validate_pool),
        ("metrics_recomputed_from_qrels", _validate_metrics),
        ("acceptance_recomputed", _validate_acceptance),
        ("latency_schema_and_arithmetic", _validate_latency),
        ("test_evidence_and_source_hash", _validate_test_evidence),
        ("manifest_all_hashes", _validate_manifest),
    )
    for name, function in definitions:
        checks.append(_run_check(name, function, paths, context))

    failures = [check for check in checks if check["status"] == FAIL]
    status = PASS if not failures else FAIL
    errors = [
        f"{check['name']}: {message}"
        for check in failures
        for message in check["errors"]
    ]
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "status": status,
        "engineering_status": status,
        "evaluation_integrity_status": (
            PASS
            if all(
                check["status"] == PASS
                for check in checks
                if check["name"] in {
                    "pool_completeness",
                    "metrics_recomputed_from_qrels",
                }
            )
            else FAIL
        ),
        "retrieval_quality_status": (
            PASS
            if next(check for check in checks if check["name"] == "acceptance_recomputed")["status"] == PASS
            else FAIL
        ),
        "overall_success": status == PASS,
        "check_count": len(checks),
        "passed_check_count": len(checks) - len(failures),
        "failed_check_count": len(failures),
        "errors": errors,
        "checks": checks,
    }


__all__ = [
    "EXPECTED_CORPUS_COUNT",
    "EXPECTED_DEPTH",
    "EXPECTED_QREL_COUNT",
    "EXPECTED_QUERY_COUNT",
    "FAIL",
    "PASS",
    "ReleaseValidationError",
    "VALIDATION_SCHEMA_VERSION",
    "validate_release",
]
