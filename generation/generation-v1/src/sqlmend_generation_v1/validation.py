"""Independent on-disk validation for the Phase 10 generation release.

No saved ``PASS`` value is trusted.  Online artifacts are checked before this
module opens qrels or development references; aggregate evaluation values and
quality gates are then recomputed from the sealed formal runs and paired rows.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .audit import (
    canonical_json_sha256,
    load_json,
    release_root,
    repository_root,
    sha256_file,
    verify_protected_audits,
    write_json,
)
from .contracts import (
    EVIDENCE_SCHEMA_VERSION,
    FINAL_RETRIEVAL_SYSTEM_ID,
    G0_SYSTEM_ID,
    G1_SYSTEM_ID,
    GENERATION_SYSTEM_IDS,
    GenerationConfig,
    PreparedQuery,
    EvidenceRow,
    validate_answer_contract,
)
from .io import load_jsonl, load_yaml, read_trec_run, sha256_json
from .manifest import (
    evaluation_directory,
    release_source_snapshot,
    reports_directory,
    verify_manifest,
)
from .metrics import (
    NOT_APPLICABLE,
    aggregate_system_metrics,
    citation_validity,
    context_retrieval_metrics,
    paired_summary,
    task_success,
)


PASS = "PASS"
FAIL = "FAIL"
EXPECTED_QUERY_COUNT = 250
EXPECTED_QUERY_IDS = tuple(f"DEV{index:04d}" for index in range(1, 251))
EXPECTED_QUERY_ID_SET = frozenset(EXPECTED_QUERY_IDS)
EXPECTED_FORMAL_ANSWER_COUNT = 500
TOP_K = 5
EXPECTED_MODEL_TAG = "qwen3.5:4b"
EXPECTED_MODEL_DIGEST = "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd"

FROZEN_INPUT_SHA256 = {
    "safe_queries": "e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c",
    "final_retrieval_run": "774d2d1c90e8e8d58479130a9e016e8a4699cd9ff4b8f72dbf95a3b6f49be566",
    "corpus": "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3",
}

_FORBIDDEN_PREPARED_KEYS = frozenset(
    {
        "annotation_evidence",
        "annotation_status",
        "candidate_label",
        "candidate_labels",
        "case_flags",
        "error_category",
        "evidence_relevance",
        "expected_behavior",
        "expected_root_cause",
        "gold_answer",
        "judgment",
        "judgment_method",
        "judgment_origin",
        "label",
        "labels",
        "primary_evidence_chunk_id",
        "qrel",
        "qrels",
        "reference_answer",
        "reference_explanation",
        "reference_fix",
        "reference_fix_sql",
        "relevance",
        "resolution",
        "root_cause",
        "schema_context",
        "seed_data",
        "setup_sql",
        "verification",
    }
)
_REFERENCE_SEAL_KEYS = frozenset(
    {
        "expected_behavior",
        "root_cause",
        "reference_fix_sql",
        "reference_explanation",
        "qrels",
        "annotation_evidence",
        "case_flags",
        "verification",
    }
)


class ReleaseValidationError(ValueError):
    """Raised by one independent validation check."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseValidationError(message)


def _config_path(paths: Any) -> Path:
    value = getattr(paths, "config_file", None)
    return Path(value) if value is not None else release_root(paths) / "config/generation.yaml"


def _answer_schema_path(paths: Any) -> Path:
    value = getattr(paths, "answer_schema", None)
    return Path(value) if value is not None else release_root(paths) / "schema/answer.schema.json"


def _safe_query_path(paths: Any) -> Path:
    value = getattr(paths, "frozen_serialized_queries", None)
    return (
        Path(value)
        if value is not None
        else repository_root(paths)
        / "retrieval/retrieval-v1/serialized_queries/dev_250_queries.jsonl"
    )


def _final_run_path(paths: Any) -> Path:
    value = getattr(paths, "final_retrieval_run", None)
    return (
        Path(value)
        if value is not None
        else repository_root(paths)
        / "retrieval/retrieval-v1/runs/"
        "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec"
    )


def _corpus_path(paths: Any) -> Path:
    value = getattr(paths, "corpus", None)
    return (
        Path(value)
        if value is not None
        else repository_root(paths) / "construction/data/processed/corpus.jsonl"
    )


def _prepared_query_path(paths: Any) -> Path:
    value = getattr(paths, "prepared_queries", None)
    return (
        Path(value)
        if value is not None
        else release_root(paths) / "prepared_inputs/online_queries.jsonl"
    )


def _evidence_path(paths: Any) -> Path:
    value = getattr(paths, "g1_evidence", None)
    return (
        Path(value)
        if value is not None
        else release_root(paths) / "prepared_inputs/g1_evidence_top5.jsonl"
    )


def _reference_path(paths: Any) -> Path:
    value = getattr(paths, "references", None)
    return (
        Path(value)
        if value is not None
        else repository_root(paths) / "annotation/codex/dev_250.jsonl"
    )


def _qrels_path(paths: Any) -> Path:
    value = getattr(paths, "qrels", None)
    return (
        Path(value)
        if value is not None
        else repository_root(paths)
        / "retrieval/baseline/qrels/qrels_effective_dev250.trec"
    )


def _run_path(paths: Any, system_id: str) -> Path:
    method = getattr(paths, "result_path", None)
    if callable(method):
        return Path(method(system_id))
    name = (
        "g0_closed_book_dev250.jsonl"
        if system_id == G0_SYSTEM_ID
        else "g1_retrieval_v1_rag_dev250.jsonl"
    )
    return release_root(paths) / "runs" / name


def _evaluation_path(paths: Any, name: str) -> Path:
    return evaluation_directory(paths) / name


def _complete_index(records: Iterable[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        query_id = record.get("query_id")
        _require(isinstance(query_id, str) and query_id, f"{label} has an invalid query_id")
        _require(query_id not in result, f"{label} has duplicate query_id {query_id}")
        result[query_id] = record
    missing = sorted(EXPECTED_QUERY_ID_SET - set(result))
    extra = sorted(set(result) - EXPECTED_QUERY_ID_SET)
    _require(not missing and not extra, f"{label} query coverage differs; missing={missing}, extra={extra}")
    _require(len(result) == EXPECTED_QUERY_COUNT, f"{label} must contain exactly 250 records")
    return dict(sorted(result.items()))


def _iter_mapping_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key)
            yield from _iter_mapping_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_mapping_keys(nested)


def _validate_frozen_inputs(paths: Any) -> dict[str, Any]:
    files = {
        "safe_queries": _safe_query_path(paths),
        "final_retrieval_run": _final_run_path(paths),
        "corpus": _corpus_path(paths),
    }
    observed: dict[str, str] = {}
    for label, path in files.items():
        _require(path.is_file(), f"Frozen {label} is missing: {path}")
        observed[label] = sha256_file(path)
        _require(
            observed[label] == FROZEN_INPUT_SHA256[label],
            f"Frozen {label} SHA-256 differs",
        )
    safe = _complete_index(load_jsonl(files["safe_queries"]), "frozen safe queries")
    _require(
        all(record.get("serializer_version") == "sqlmend-query-v1" for record in safe.values()),
        "Frozen safe-query serializer version differs",
    )
    rows = read_trec_run(files["final_retrieval_run"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["query_id"])].append(row)
    _require(set(grouped) == EXPECTED_QUERY_ID_SET, "Final Retrieval-v1 run query coverage differs")
    for query_id, ranking in grouped.items():
        ordered = sorted(ranking, key=lambda item: int(item["rank"]))
        _require(len(ordered) == 30, f"Final run {query_id} does not contain 30 passages")
        _require(
            [int(item["rank"]) for item in ordered] == list(range(1, 31)),
            f"Final run ranks are not continuous for {query_id}",
        )
        _require(
            all(item["run_tag"] == FINAL_RETRIEVAL_SYSTEM_ID for item in ordered),
            f"Final run tag differs for {query_id}",
        )
    return {
        "hashes": observed,
        "safe_query_count": len(safe),
        "final_run_query_count": len(grouped),
        "final_run_row_count": len(rows),
    }


def _validate_prepared_inputs(paths: Any) -> dict[str, Any]:
    config = GenerationConfig.from_mapping(load_yaml(_config_path(paths)))
    _require(config.expected_query_count == EXPECTED_QUERY_COUNT, "Generation config query count differs")
    _require(config.model_tag == EXPECTED_MODEL_TAG, "Frozen generation model tag differs")
    _require(config.model_digest == EXPECTED_MODEL_DIGEST, "Frozen generation model digest differs")
    _require(config.think is False, "Frozen generation think setting must be boolean false")
    safe = _complete_index(load_jsonl(_safe_query_path(paths)), "frozen safe queries")
    prepared_records = load_jsonl(_prepared_query_path(paths))
    prepared = _complete_index(prepared_records, "prepared online queries")
    for query_id, record in prepared.items():
        parsed = PreparedQuery.from_record(record)
        expected = safe[query_id]
        _require(
            parsed.source_fields_used == tuple(expected["source_fields_used"])
            and parsed.serialized_text == expected["serialized_text"]
            and parsed.serialized_text_sha256 == expected["serialized_text_sha256"]
            and parsed.serializer_version == expected["serializer_version"],
            f"Prepared query differs from frozen safe query for {query_id}",
        )

    run_rows = read_trec_run(_final_run_path(paths))
    top_five: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        if int(row["rank"]) <= TOP_K:
            top_five[str(row["query_id"])].append(row)
    for ranking in top_five.values():
        ranking.sort(key=lambda item: int(item["rank"]))
    corpus_rows = load_jsonl(_corpus_path(paths))
    corpus = {str(row["chunk_id"]): row for row in corpus_rows}
    _require(len(corpus) == len(corpus_rows), "Production corpus has duplicate chunk IDs")

    evidence_records = load_jsonl(_evidence_path(paths))
    evidence = _complete_index(evidence_records, "prepared G1 evidence")
    run_hash = sha256_file(_final_run_path(paths))
    expected_passage_fields = (
        "dialect",
        "version",
        "version_min",
        "version_max",
        "version_status",
        "source_type",
        "source_name",
        "source_url",
        "title",
        "section",
        "text",
        "content_hash",
    )
    for query_id, record in evidence.items():
        forbidden = sorted(
            key for key in _iter_mapping_keys(record) if key.casefold() in _FORBIDDEN_PREPARED_KEYS
        )
        _require(not forbidden, f"Prepared evidence contains label/reference fields for {query_id}: {forbidden}")
        parsed = EvidenceRow.from_record(record)
        _require(parsed.run_sha256 == run_hash, f"Evidence run hash differs for {query_id}")
        expected_rows = top_five.get(query_id, [])
        _require(len(expected_rows) == TOP_K, f"No exact Retrieval-v1 Top-5 for {query_id}")
        for actual, expected_run in zip(parsed.passages, expected_rows, strict=True):
            passage_id = str(expected_run["passage_id"])
            _require(actual["passage_id"] == passage_id, f"Evidence ID/rank differs for {query_id}")
            _require(actual["rank"] == expected_run["rank"], f"Evidence rank differs for {query_id}")
            _require(
                math.isclose(float(actual["score"]), float(expected_run["score"]), rel_tol=0.0, abs_tol=1e-15),
                f"Evidence score differs for {query_id} rank {actual['rank']}",
            )
            _require(passage_id in corpus, f"Evidence passage is absent from corpus: {passage_id}")
            source = corpus[passage_id]
            for field in expected_passage_fields:
                _require(
                    actual[field] == source.get(field),
                    f"Prepared evidence {field} differs from corpus for {passage_id}",
                )
    return {
        "config": {
            "experiment_id": config.experiment_id,
            "model_tag": config.model_tag,
            "model_digest": config.model_digest,
            "think": config.think,
            "options": config.options.to_dict(),
            "retry_policy": config.retry_policy.to_dict(),
            "top_k": config.top_k,
        },
        "prepared_query_count": len(prepared),
        "evidence_query_count": len(evidence),
        "passage_count": sum(len(row["passages"]) for row in evidence.values()),
        "evidence": evidence,
    }


def _deep_values(value: Any, names: frozenset[str]) -> list[Any]:
    result: list[Any] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in names:
                result.append(nested)
            result.extend(_deep_values(nested, names))
    elif isinstance(value, list):
        for nested in value:
            result.extend(_deep_values(nested, names))
    return result


def _one_deep_value(record: Mapping[str, Any], names: Sequence[str], label: str) -> Any:
    values = _deep_values(record, frozenset(names))
    _require(values, f"Generation wrapper lacks {label}")
    first = values[0]
    _require(all(value == first for value in values), f"Generation wrapper has conflicting {label}")
    return first


def _answer(record: Mapping[str, Any]) -> Any:
    for key in ("answer", "final_answer", "parsed_answer"):
        if key in record:
            return record[key]
    return None


def _allowed_citation_ids(record: Mapping[str, Any]) -> tuple[str, ...]:
    for key in ("allowed_citation_ids", "provided_passage_ids", "evidence_passage_ids"):
        value = record.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return tuple(value)
    values = _deep_values(
        record,
        frozenset(
            {
                "allowed_citation_ids",
                "provided_passage_ids",
                "evidence_passage_ids",
            }
        ),
    )
    if values and isinstance(values[0], list) and all(isinstance(item, str) for item in values[0]):
        return tuple(values[0])
    return ()


def _record_evidence_ids(record: Mapping[str, Any]) -> tuple[str, ...]:
    for key in ("evidence", "provided_evidence", "passages"):
        value = record.get(key)
        if isinstance(value, list):
            ids: list[str] = []
            for item in value:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, Mapping):
                    candidate = item.get("passage_id", item.get("chunk_id"))
                    if isinstance(candidate, str):
                        ids.append(candidate)
            if ids or value == []:
                return tuple(ids)
    return _allowed_citation_ids(record)


def _attempts(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = record.get("attempts")
    if not isinstance(value, list):
        values = _deep_values(record, frozenset({"attempts"}))
        value = values[0] if values else None
    _require(isinstance(value, list), "Generation wrapper attempts must be a list")
    _require(all(isinstance(item, Mapping) for item in value), "Generation attempts must be objects")
    return list(value)


def _numeric(value: Any, label: str) -> float:
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    result = float(value)
    _require(math.isfinite(result) and result >= 0.0, f"{label} must be finite and non-negative")
    return result


def _attempt_latency(attempt: Mapping[str, Any]) -> float:
    for key in ("latency_wall_ms", "wall_ms", "client_wall_ms", "latency_ms"):
        if key in attempt:
            return _numeric(attempt[key], f"attempt {key}")
    latency = attempt.get("latency")
    if isinstance(latency, Mapping):
        for key in ("wall_ms", "client_wall_ms", "total_ms"):
            if key in latency:
                return _numeric(latency[key], f"attempt latency.{key}")
    raise ReleaseValidationError("Generation attempt has no client wall latency")


def _wrapper_latency(record: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]]) -> float:
    summed = math.fsum(_attempt_latency(attempt) for attempt in attempts)
    candidates: list[Any] = []
    for key in ("latency_wall_ms", "total_latency_ms", "generation_latency_ms"):
        if key in record:
            candidates.append(record[key])
    latency = record.get("latency")
    if isinstance(latency, Mapping):
        for key in ("wall_ms", "total_wall_ms", "total_ms"):
            if key in latency:
                candidates.append(latency[key])
    _require(candidates, "Generation wrapper has no total client wall latency")
    total = _numeric(candidates[0], "wrapper total latency")
    _require(
        all(math.isclose(_numeric(value, "wrapper total latency"), total, rel_tol=0.0, abs_tol=1e-6) for value in candidates),
        "Generation wrapper has conflicting total latency values",
    )
    # The wrapper wall clock intentionally includes validation and retry-loop
    # bookkeeping between attempts, so it must dominate rather than exactly
    # equal the sum of per-request timers.
    _require(total + 1e-6 >= summed, "Wrapper wall latency is below summed attempt latency")
    return total


def _configuration_signature(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "prompt_template_sha256": _one_deep_value(
            record, ("prompt_template_sha256",), "prompt template SHA-256"
        ),
        "answer_schema_sha256": _one_deep_value(
            record,
            ("answer_schema_sha256", "output_schema_sha256"),
            "answer schema SHA-256",
        ),
        "model_tag": _one_deep_value(record, ("model_tag",), "model tag"),
        "model_digest": _one_deep_value(record, ("model_digest",), "model digest"),
        "think": _one_deep_value(record, ("think", "reasoning_effort"), "reasoning effort"),
        "options": _one_deep_value(record, ("generation_options", "options"), "decoding options"),
        "retry_policy": _one_deep_value(record, ("retry_policy",), "retry policy"),
    }


def _validate_formal_runs(paths: Any, prepared_details: Mapping[str, Any]) -> dict[str, Any]:
    config = GenerationConfig.from_mapping(load_yaml(_config_path(paths)))
    answer_schema_hash = sha256_file(_answer_schema_path(paths))
    evidence_records = prepared_details["evidence"]
    systems: dict[str, Any] = {}
    global_signatures: list[dict[str, Any]] = []
    for system_id in GENERATION_SYSTEM_IDS:
        path = _run_path(paths, system_id)
        _require(path.is_file(), f"Formal run is missing: {path}")
        records = _complete_index(load_jsonl(path), f"{system_id} formal wrappers")
        normalized: dict[str, dict[str, Any]] = {}
        structured_count = 0
        citation_occurrences = 0
        valid_citation_occurrences = 0
        signatures: list[dict[str, Any]] = []
        for query_id, record in records.items():
            _require(record.get("system_id") == system_id, f"Wrapper system_id differs for {query_id}")
            status = record.get("status")
            _require(status in {"success", "failed"}, f"Wrapper status is not explicit for {query_id}")
            attempts = _attempts(record)
            _require(1 <= len(attempts) <= config.retry_policy.max_attempts, f"Attempt count differs for {query_id}")
            attempt_count = _one_deep_value(record, ("attempt_count",), "attempt_count")
            retry_count = _one_deep_value(record, ("retry_count",), "retry_count")
            _require(attempt_count == len(attempts), f"attempt_count differs for {query_id}")
            _require(retry_count == len(attempts) - 1, f"retry_count differs for {query_id}")
            latency = _wrapper_latency(record, attempts)
            expected_ids = (
                tuple(str(passage["passage_id"]) for passage in evidence_records[query_id]["passages"])
                if system_id == G1_SYSTEM_ID
                else ()
            )
            allowed_ids = _allowed_citation_ids(record)
            supplied_ids = _record_evidence_ids(record)
            if system_id == G0_SYSTEM_ID:
                _require(not allowed_ids and not supplied_ids, f"G0 received evidence for {query_id}")
            else:
                _require(allowed_ids == expected_ids, f"G1 allowed citation IDs differ from actual Top-5 for {query_id}")
                _require(supplied_ids == expected_ids, f"G1 evidence IDs differ from actual Top-5 for {query_id}")
            answer = _answer(record)
            last_attempt = attempts[-1]
            _require(
                isinstance(last_attempt.get("structured_output_valid"), bool)
                and isinstance(last_attempt.get("contract_valid"), bool),
                f"Final attempt validity flags are missing for {query_id}",
            )
            wrapper_shape_valid = record.get("structured_output_valid")
            wrapper_contract_valid = record.get("contract_valid")
            _require(wrapper_shape_valid is last_attempt["structured_output_valid"], f"Saved structured validity differs from final attempt for {query_id}")
            _require(wrapper_contract_valid is last_attempt["contract_valid"], f"Saved contract validity differs from final attempt for {query_id}")
            if status == "success":
                errors = validate_answer_contract(
                    answer,
                    system_id=system_id,
                    allowed_citation_ids=expected_ids,
                )
                _require(not errors and wrapper_shape_valid is True and wrapper_contract_valid is True, f"Successful answer contract differs for {query_id}")
            else:
                _require(answer is None and wrapper_contract_valid is False, f"Failed wrapper answer/contract differs for {query_id}")
            # Structured Output Validity measures JSON/schema shape only. A
            # shape-valid citation-contract failure is accounted for separately
            # by generation-contract and citation metrics.
            structured_valid = wrapper_shape_valid is True
            structured_count += int(structured_valid)
            if system_id == G1_SYSTEM_ID:
                citation = citation_validity(answer, expected_ids)
                citation_occurrences += int(citation["citation_count"])
                valid_citation_occurrences += int(citation["valid_count"])
                _require(citation["invalid_count"] == 0, f"G1 fabricated a citation for {query_id}")
            elif isinstance(answer, Mapping):
                _require(answer.get("citations") == [], f"G0 citations are not empty for {query_id}")

            signature = _configuration_signature(record)
            signatures.append(signature)
            global_signatures.append(signature)
            normalized[query_id] = {
                "record": record,
                "status": status,
                "answer": answer,
                "structured_output_valid": structured_valid,
                "contract_valid": wrapper_contract_valid,
                "latency_wall_ms": latency,
                "generation_attempt_count": int(attempt_count),
                "generation_retry_count": int(retry_count),
                "allowed_citation_ids": expected_ids,
            }
        _require(all(signature == signatures[0] for signature in signatures), f"{system_id} generation configuration changed across queries")
        signature = signatures[0]
        _require(signature["answer_schema_sha256"] == answer_schema_hash, f"{system_id} answer schema hash differs")
        _require(signature["model_tag"] == config.model_tag, f"{system_id} model tag differs")
        _require(signature["model_digest"] == config.model_digest, f"{system_id} model digest differs")
        _require(signature["think"] == config.think, f"{system_id} reasoning effort differs")
        _require(signature["think"] is False, f"{system_id} think must be boolean false")
        _require(signature["options"] == config.options.to_dict(), f"{system_id} decoding options differ")
        _require(signature["retry_policy"] == config.retry_policy.to_dict(), f"{system_id} retry policy differs")
        validity = structured_count / EXPECTED_QUERY_COUNT
        generation_success_count = sum(
            value["status"] == "success" for value in normalized.values()
        )
        generation_failure_count = len(normalized) - generation_success_count
        systems[system_id] = {
            "path": path,
            "sha256": sha256_file(path),
            "records": normalized,
            "record_count": len(records),
            "success_count_semantics": "generation_contract_success",
            "success_count": generation_success_count,
            "failed_count": generation_failure_count,
            "generation_contract_success_count": generation_success_count,
            "generation_contract_failure_count": generation_failure_count,
            "generation_contract_success_rate": (
                generation_success_count / EXPECTED_QUERY_COUNT
            ),
            "structured_output_validity": validity,
            "citation_count": citation_occurrences,
            "valid_citation_count": valid_citation_occurrences,
            "citation_validity": (
                1.0 if citation_occurrences == 0 else valid_citation_occurrences / citation_occurrences
            ),
            "configuration_signature": signature,
        }
    _require(global_signatures and all(value == global_signatures[0] for value in global_signatures), "G0 and G1 prompt/schema/model/think/decoding/retry settings differ")
    _require(sum(system["record_count"] for system in systems.values()) == EXPECTED_FORMAL_ANSWER_COUNT, "Formal answer wrapper count differs from 500")
    _require(systems[G1_SYSTEM_ID]["citation_validity"] == 1.0, "G1 Citation Validity is not 100%")
    return {"systems": systems, "shared_configuration": global_signatures[0]}


def _seal_run_entry(seal: Mapping[str, Any], system_id: str) -> Mapping[str, Any]:
    runs = seal.get("runs")
    _require(isinstance(runs, Mapping), "Generation seal has no runs mapping")
    for key in (system_id, "g0" if system_id == G0_SYSTEM_ID else "g1"):
        value = runs.get(key)
        if isinstance(value, Mapping):
            return value
    raise ReleaseValidationError(f"Generation seal has no entry for {system_id}")


def _query_ids_sha256(query_ids: Iterable[str]) -> set[str]:
    ordered = sorted(query_ids)
    return {
        sha256_json(ordered),
        canonical_json_sha256(ordered),
        canonical_json_sha256({"query_ids": ordered}),
        hashlib.sha256(("\n".join(ordered) + "\n").encode("utf-8")).hexdigest(),
    }


def _validate_generation_seal(paths: Any, run_details: Mapping[str, Any]) -> dict[str, Any]:
    path = _evaluation_path(paths, "generation_seal.json")
    _require(path.is_file(), "Generation seal is missing")
    seal = load_json(path)
    _require(seal.get("schema_version") == "sqlmend-generation-seal-v1", "Generation seal schema differs")
    forbidden = sorted(key for key in _iter_mapping_keys(seal) if key.casefold() in _REFERENCE_SEAL_KEYS)
    _require(not forbidden, f"Generation seal contains reference fields: {forbidden}")
    for system_id in GENERATION_SYSTEM_IDS:
        actual = run_details["systems"][system_id]
        entry = _seal_run_entry(seal, system_id)
        _require(entry.get("sha256") == actual["sha256"], f"Generation seal hash differs for {system_id}")
        _require(entry.get("byte_size") == actual["path"].stat().st_size, f"Generation seal byte size differs for {system_id}")
        _require(entry.get("record_count") == EXPECTED_QUERY_COUNT, f"Generation seal count differs for {system_id}")
        _require(entry.get("success_count_semantics") == "generation_contract_success", f"Generation seal count semantics differ for {system_id}")
        _require(entry.get("success_count") == actual["success_count"], f"Generation seal success count differs for {system_id}")
        _require(entry.get("failed_count") == actual["failed_count"], f"Generation seal failure count differs for {system_id}")
        _require(entry.get("generation_contract_success_count") == actual["success_count"], f"Generation seal explicit success count differs for {system_id}")
        _require(entry.get("generation_contract_failure_count") == actual["failed_count"], f"Generation seal explicit failure count differs for {system_id}")
        query_hash = entry.get("query_ids_sha256")
        _require(query_hash in _query_ids_sha256(actual["records"]), f"Generation seal query ID hash differs for {system_id}")
    return {
        "path": path,
        "sha256": sha256_file(path),
        "payload": seal,
        "sealed_run_hashes_match": True,
        "contains_reference_fields": False,
    }


def _system_view(row: Mapping[str, Any], system_id: str) -> Mapping[str, Any]:
    keys = (system_id, "g0" if system_id == G0_SYSTEM_ID else "g1")
    for key in keys:
        value = row.get(key)
        if isinstance(value, Mapping):
            return value
    raise ReleaseValidationError(f"Paired row has no view for {system_id}")


def _view_value(view: Mapping[str, Any], field: str) -> Any:
    if field in view:
        return view[field]
    for container in ("judge", "judgment", "metrics", "decision", "evaluation"):
        nested = view.get(container)
        if isinstance(nested, Mapping) and field in nested:
            return nested[field]
    return None


def _bounded(value: Any, label: str) -> float:
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    result = float(value)
    _require(math.isfinite(result) and 0.0 <= result <= 1.0, f"{label} must be in [0,1]")
    return result


_JUDGE_BOOLEAN_FIELDS = (
    "root_cause_correct",
    "sql_repair_correct",
    "dialect_compatible",
    "version_compatible",
)
_JUDGE_SCORE_FIELDS = (
    "answer_relevance",
    "faithfulness",
    "citation_coverage",
)
_JUDGE_DECISION_FIELDS = frozenset(
    (*_JUDGE_BOOLEAN_FIELDS, *_JUDGE_SCORE_FIELDS, "reason")
)


def _validate_judge_side(value: Any, label: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    _require(set(value) == _JUDGE_DECISION_FIELDS, f"{label} fields differ")
    for field in _JUDGE_BOOLEAN_FIELDS:
        _require(isinstance(value.get(field), bool), f"{label}.{field} must be boolean")
    for field in _JUDGE_SCORE_FIELDS:
        _bounded(value.get(field), f"{label}.{field}")
    _require(isinstance(value.get("reason"), str), f"{label}.reason must be a string")
    return value


def _is_failed_judge_side(value: Mapping[str, Any]) -> bool:
    return all(value.get(field) is False for field in _JUDGE_BOOLEAN_FIELDS) and all(
        _float_equal(value.get(field), 0.0) for field in _JUDGE_SCORE_FIELDS
    )


def _validate_judge_attempts(
    judgment: Mapping[str, Any],
    query_id: str,
) -> list[Mapping[str, Any]]:
    attempts = judgment.get("attempts")
    _require(isinstance(attempts, list), f"Judge attempts are missing for {query_id}")
    _require(1 <= len(attempts) <= 3, f"Judge attempts must be between 1 and 3 for {query_id}")
    _require(
        all(isinstance(attempt, Mapping) for attempt in attempts),
        f"Judge attempts must be objects for {query_id}",
    )
    for expected_number, attempt in enumerate(attempts, start=1):
        attempt_number = attempt.get("attempt")
        _require(
            isinstance(attempt_number, int)
            and not isinstance(attempt_number, bool)
            and attempt_number == expected_number,
            f"Judge attempt numbering differs for {query_id}",
        )
        _require(
            attempt.get("status") in {"success", "failed"},
            f"Judge attempt status differs for {query_id}",
        )
        _numeric(attempt.get("wall_ms"), f"Judge attempt wall_ms for {query_id}")
        if expected_number < len(attempts):
            _require(
                attempt.get("status") == "failed",
                f"Judge succeeded before its final attempt for {query_id}",
            )

    attempt_count = judgment.get("attempt_count")
    retry_count = judgment.get("retry_count")
    _require(
        isinstance(attempt_count, int)
        and not isinstance(attempt_count, bool)
        and attempt_count == len(attempts),
        f"Judge attempt_count differs for {query_id}",
    )
    _require(
        isinstance(retry_count, int)
        and not isinstance(retry_count, bool)
        and retry_count == len(attempts) - 1,
        f"Judge retry_count differs for {query_id}",
    )
    _require(
        judgment.get("status") == attempts[-1].get("status"),
        f"Judge status differs from final attempt for {query_id}",
    )
    return list(attempts)


def _load_qrels_after_seal(paths: Any, seal_details: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    _require(seal_details.get("sealed_run_hashes_match") is True, "Reference data cannot be opened before generation seal validation")
    path = _qrels_path(paths)
    _require(path.is_file(), "Offline qrels file is missing")
    result: dict[str, dict[str, int]] = defaultdict(dict)
    if path.suffix.casefold() == ".jsonl":
        rows = [
            (record.get("query_id"), record.get("chunk_id"), record.get("relevance"))
            for record in load_jsonl(path)
        ]
    else:
        rows = []
        with path.open("r", encoding="utf-8") as stream:
            for line_number, raw in enumerate(stream, start=1):
                columns = raw.split()
                _require(len(columns) == 4, f"Malformed offline qrel at line {line_number}")
                query_id, _, chunk_id, relevance_text = columns
                try:
                    relevance = int(relevance_text)
                except ValueError as exc:
                    raise ReleaseValidationError(
                        f"Malformed relevance at qrels line {line_number}"
                    ) from exc
                rows.append((query_id, chunk_id, relevance))
    for query_id, chunk_id, relevance in rows:
        _require(
            isinstance(query_id, str)
            and isinstance(chunk_id, str)
            and isinstance(relevance, int)
            and not isinstance(relevance, bool)
            and relevance in {0, 1, 2},
            "Malformed offline qrel",
        )
        _require(chunk_id not in result[query_id], f"Duplicate offline qrel: {query_id}/{chunk_id}")
        result[query_id][chunk_id] = relevance
    return dict(result)


def _float_equal(actual: Any, expected: Any, *, tolerance: float = 1e-9) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)
    return actual == expected


def _compare_metrics(actual: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    for field, expected_value in expected.items():
        _require(field in actual, f"{label} lacks metric {field}")
        actual_value = actual[field]
        if isinstance(expected_value, Mapping):
            _require(isinstance(actual_value, Mapping), f"{label}.{field} must be an object")
            _compare_metrics(actual_value, expected_value, f"{label}.{field}")
        else:
            _require(_float_equal(actual_value, expected_value), f"{label}.{field} arithmetic differs")


def _validate_offline_evaluation(
    paths: Any,
    run_details: Mapping[str, Any],
    prepared_details: Mapping[str, Any],
) -> dict[str, Any]:
    seal = _validate_generation_seal(paths, run_details)
    # This is the first point in validation at which a relevance/reference artifact is opened.
    qrels = _load_qrels_after_seal(paths, seal)
    reference_path = _reference_path(paths)
    prepared_query_path = _prepared_query_path(paths)
    evidence_path = _evidence_path(paths)
    for input_path, label in (
        (reference_path, "development reference"),
        (prepared_query_path, "prepared online queries"),
        (evidence_path, "prepared G1 evidence"),
    ):
        _require(input_path.is_file(), f"Offline {label} file is missing")
    evaluation_input_sha256 = {
        "development_references_file": sha256_file(reference_path),
        "effective_qrels_file": sha256_file(_qrels_path(paths)),
        "prepared_queries_file": sha256_file(prepared_query_path),
        "g1_evidence_file": sha256_file(evidence_path),
    }
    evaluation_context_sha256 = sha256_json(evaluation_input_sha256)
    sealed_offline_inputs = seal["payload"].get("offline_evaluation_inputs")
    _require(
        isinstance(sealed_offline_inputs, Mapping)
        and sealed_offline_inputs.get("sha256") == evaluation_input_sha256
        and sealed_offline_inputs.get("context_sha256")
        == evaluation_context_sha256,
        "Generation seal offline evaluation input SHA differs from current files",
    )

    judgments_path = _evaluation_path(paths, "judgments.jsonl")
    paired_path = _evaluation_path(paths, "per_query_comparison.jsonl")
    overall_path = _evaluation_path(paths, "overall_metrics.json")
    for path in (judgments_path, paired_path, overall_path):
        _require(path.is_file(), f"Offline evaluation artifact is missing: {path.name}")
    judgments = _complete_index(load_jsonl(judgments_path), "offline judgments")
    paired = _complete_index(load_jsonl(paired_path), "paired comparison")
    _require(len(judgments) == EXPECTED_QUERY_COUNT, "Offline judge did not retain all queries")

    config = GenerationConfig.from_mapping(load_yaml(_config_path(paths)))
    sealed_run_hashes = {
        "g0": run_details["systems"][G0_SYSTEM_ID]["sha256"],
        "g1": run_details["systems"][G1_SYSTEM_ID]["sha256"],
    }
    judge_signatures: list[dict[str, Any]] = []
    for ordinal, query_id in enumerate(EXPECTED_QUERY_IDS, start=1):
        judgment = judgments[query_id]
        _require(
            judgment.get("schema_version") == "sqlmend-generation-judgment-v1",
            f"Judge schema differs for {query_id}",
        )
        judge_ordinal = judgment.get("ordinal")
        _require(
            isinstance(judge_ordinal, int)
            and not isinstance(judge_ordinal, bool)
            and judge_ordinal == ordinal,
            f"Judge ordinal differs for {query_id}",
        )
        expected_assignment = (
            {"A": G0_SYSTEM_ID, "B": G1_SYSTEM_ID}
            if ordinal % 2 == 1
            else {"A": G1_SYSTEM_ID, "B": G0_SYSTEM_ID}
        )
        _require(judgment.get("assignment") == expected_assignment, f"Judge counterbalance differs for {query_id}")
        _require(judgment.get("status") in {"success", "failed"}, f"Judge status differs for {query_id}")
        _require(judgment.get("run_sha256") == sealed_run_hashes, f"Judge run seal differs for {query_id}")
        _require(
            judgment.get("evaluation_input_sha256") == evaluation_input_sha256,
            f"Judge offline input SHA differs for {query_id}",
        )
        _require(
            judgment.get("evaluation_context_sha256")
            == evaluation_context_sha256,
            f"Judge offline context SHA differs for {query_id}",
        )
        judge_attempts = _validate_judge_attempts(judgment, query_id)
        decision = judgment.get("decision")
        _require(isinstance(decision, Mapping) and set(decision) == {"g0", "g1"}, f"Judge decision differs for {query_id}")
        normalized_decision = {
            system_id: _validate_judge_side(
                decision["g0" if system_id == G0_SYSTEM_ID else "g1"],
                f"Judge decision for {query_id}/{system_id}",
            )
            for system_id in GENERATION_SYSTEM_IDS
        }
        if judgment.get("status") == "failed":
            _require(
                all(_is_failed_judge_side(side) for side in normalized_decision.values()),
                f"Failed judge decision is not conservative for {query_id}",
            )
        else:
            anonymous_decision = {
                label: normalized_decision[expected_assignment[label]]
                for label in ("A", "B")
            }
            formal_pair_succeeded = all(
                run_details["systems"][system_id]["records"][query_id]["status"]
                == "success"
                for system_id in GENERATION_SYSTEM_IDS
            )
            response_sha256 = judge_attempts[-1].get("response_sha256")
            if formal_pair_succeeded:
                _require(
                    response_sha256 == canonical_json_sha256(anonymous_decision),
                    f"Judge final response hash differs from decision for {query_id}",
                )
            else:
                # The evaluator conservatively overwrites the side belonging to
                # a failed formal wrapper after hashing the raw anonymous judge
                # response, so only the digest shape remains independently
                # checkable for that exceptional path.
                _require(
                    isinstance(response_sha256, str)
                    and len(response_sha256) == 64
                    and all(character in "0123456789abcdef" for character in response_sha256),
                    f"Judge final response hash is malformed for {query_id}",
                )
        for system_id in GENERATION_SYSTEM_IDS:
            formal = run_details["systems"][system_id]["records"][query_id]
            if formal["status"] == "failed":
                _require(
                    _is_failed_judge_side(normalized_decision[system_id]),
                    f"Judge decision does not preserve formal generation failure for {query_id}/{system_id}",
                )
        signature = {
            "model_tag": _one_deep_value(judgment, ("model_tag", "model"), "judge model tag"),
            "model_digest": _one_deep_value(judgment, ("model_digest",), "judge model digest"),
            "think": _one_deep_value(judgment, ("think", "reasoning_effort"), "judge think setting"),
        }
        _require(signature["model_tag"] == config.model_tag, f"Judge model tag differs for {query_id}")
        _require(signature["model_digest"] == config.model_digest, f"Judge model digest differs for {query_id}")
        _require(signature["think"] is False and signature["think"] == config.think, f"Judge think must equal generation bool false for {query_id}")
        judge_signatures.append(signature)
    _require(all(signature == judge_signatures[0] for signature in judge_signatures), "Judge model provenance changes across queries")

    normalized_by_system: dict[str, list[dict[str, Any]]] = {
        G0_SYSTEM_ID: [],
        G1_SYSTEM_ID: [],
    }
    for ordinal, query_id in enumerate(EXPECTED_QUERY_IDS, start=1):
        row = paired[query_id]
        judgment = judgments[query_id]
        _require(
            row.get("schema_version") == "sqlmend-generation-paired-query-v1",
            f"Paired schema differs for {query_id}",
        )
        paired_ordinal = row.get("ordinal")
        _require(
            isinstance(paired_ordinal, int)
            and not isinstance(paired_ordinal, bool)
            and paired_ordinal == ordinal,
            f"Paired ordinal differs for {query_id}",
        )
        _require(
            row.get("judge_status") == judgment.get("status"),
            f"Paired judge status differs for {query_id}",
        )
        paired_judge_attempt_count = row.get("judge_attempt_count")
        _require(
            isinstance(paired_judge_attempt_count, int)
            and not isinstance(paired_judge_attempt_count, bool)
            and paired_judge_attempt_count == judgment.get("attempt_count"),
            f"Paired judge attempt count differs for {query_id}",
        )
        paired_judge_retry_count = row.get("judge_retry_count")
        _require(
            isinstance(paired_judge_retry_count, int)
            and not isinstance(paired_judge_retry_count, bool)
            and paired_judge_retry_count == judgment.get("retry_count"),
            f"Paired judge retry count differs for {query_id}",
        )
        row_core: dict[str, dict[str, bool]] = {}
        row_task_success: dict[str, bool] = {}
        row_answer_relevance: dict[str, float] = {}
        for system_id in GENERATION_SYSTEM_IDS:
            view = _system_view(row, system_id)
            short_name = "g0" if system_id == G0_SYSTEM_ID else "g1"
            journal_side = judgment["decision"][short_name]
            core: dict[str, bool] = {}
            for field in (
                "root_cause_correct",
                "sql_repair_correct",
                "dialect_compatible",
                "version_compatible",
            ):
                value = _view_value(view, field)
                _require(isinstance(value, bool), f"{query_id}/{system_id} {field} must be boolean")
                _require(
                    value is journal_side[field],
                    f"Paired semantic judgment differs from journal for {query_id}/{system_id}/{field}",
                )
                core[field] = value
            expected_task = task_success(core)
            _require(_view_value(view, "task_success") is expected_task, f"Task Success is not the required conjunction for {query_id}/{system_id}")
            row_core[system_id] = core
            row_task_success[system_id] = expected_task

            formal = run_details["systems"][system_id]["records"][query_id]
            _require(
                _view_value(view, "status") == formal["status"],
                f"Paired generation status differs for {query_id}/{system_id}",
            )
            structured = _view_value(view, "structured_output_valid")
            _require(structured is formal["structured_output_valid"], f"Structured validity differs for {query_id}/{system_id}")
            _require(
                _view_value(view, "contract_valid") is formal["contract_valid"],
                f"Generation contract validity differs for {query_id}/{system_id}",
            )
            latency = _numeric(_view_value(view, "latency_wall_ms"), f"{query_id}/{system_id} latency")
            _require(math.isclose(latency, formal["latency_wall_ms"], rel_tol=0.0, abs_tol=1e-6), f"Latency differs from sealed wrapper for {query_id}/{system_id}")
            generation_attempt_count = _view_value(
                view, "generation_attempt_count"
            )
            generation_retry_count = _view_value(
                view, "generation_retry_count"
            )
            _require(
                generation_attempt_count == formal["generation_attempt_count"],
                f"Generation attempt count differs for {query_id}/{system_id}",
            )
            _require(
                generation_retry_count == formal["generation_retry_count"],
                f"Generation retry count differs for {query_id}/{system_id}",
            )
            answer_relevance = _bounded(_view_value(view, "answer_relevance"), f"{query_id}/{system_id} answer relevance")
            _require(
                _float_equal(answer_relevance, journal_side["answer_relevance"]),
                f"Paired answer relevance differs from journal for {query_id}/{system_id}",
            )
            _require(
                _view_value(view, "judge_reason") == journal_side["reason"],
                f"Paired judge reason differs from journal for {query_id}/{system_id}",
            )
            row_answer_relevance[system_id] = answer_relevance
            normalized: dict[str, Any] = {
                "status": formal["status"],
                **core,
                "task_success": expected_task,
                "structured_output_valid": structured,
                "answer_relevance": answer_relevance,
                "latency_wall_ms": latency,
                "generation_attempt_count": generation_attempt_count,
                "generation_retry_count": generation_retry_count,
            }
            if system_id == G0_SYSTEM_ID:
                _require(_view_value(view, "faithfulness") == NOT_APPLICABLE, f"G0 faithfulness must be the string N/A for {query_id}")
                _require(_view_value(view, "context_precision") == NOT_APPLICABLE, f"G0 context precision must be the string N/A for {query_id}")
            else:
                passage_ids = formal["allowed_citation_ids"]
                citation = citation_validity(formal["answer"], passage_ids)
                context = context_retrieval_metrics(passage_ids, qrels.get(query_id, {}))
                _require(context["fully_judged"] is True, f"G1 Top-5 has an unjudged passage for {query_id}")
                citation_score = _bounded(_view_value(view, "citation_validity"), f"{query_id} citation validity")
                _require(_float_equal(citation_score, citation["score"]), f"Citation validity arithmetic differs for {query_id}")
                context_precision = _bounded(_view_value(view, "context_precision"), f"{query_id} context precision")
                _require(_float_equal(context_precision, context["context_precision"]), f"Context precision arithmetic differs for {query_id}")
                coverage = _bounded(_view_value(view, "citation_coverage"), f"{query_id} citation coverage")
                faithfulness = _bounded(_view_value(view, "faithfulness"), f"{query_id} faithfulness")
                _require(
                    _float_equal(coverage, journal_side["citation_coverage"]),
                    f"Paired citation coverage differs from journal for {query_id}",
                )
                _require(
                    _float_equal(faithfulness, journal_side["faithfulness"]),
                    f"Paired faithfulness differs from journal for {query_id}",
                )
                normalized.update(
                    {
                        "citation_validity": citation_score,
                        "citation_coverage": coverage,
                        "faithfulness": faithfulness,
                        "context_precision": context_precision,
                        "context_query_hit": context["context_query_hit"],
                        "context_fully_judged": context["fully_judged"],
                    }
                )
            normalized_by_system[system_id].append(normalized)

        paired_view = row.get("paired")
        _require(isinstance(paired_view, Mapping), f"Paired delta payload is missing for {query_id}")
        expected_task_delta = int(row_task_success[G1_SYSTEM_ID]) - int(
            row_task_success[G0_SYSTEM_ID]
        )
        task_success_delta = paired_view.get("task_success_delta")
        _require(
            isinstance(task_success_delta, int)
            and not isinstance(task_success_delta, bool)
            and task_success_delta == expected_task_delta,
            f"Paired Task Success delta differs for {query_id}",
        )
        expected_component_delta = sum(
            int(row_core[G1_SYSTEM_ID][field])
            - int(row_core[G0_SYSTEM_ID][field])
            for field in _JUDGE_BOOLEAN_FIELDS
        )
        semantic_component_delta = paired_view.get("semantic_component_delta")
        _require(
            isinstance(semantic_component_delta, int)
            and not isinstance(semantic_component_delta, bool)
            and semantic_component_delta == expected_component_delta,
            f"Paired semantic component delta differs for {query_id}",
        )
        expected_relevance_delta = (
            row_answer_relevance[G1_SYSTEM_ID]
            - row_answer_relevance[G0_SYSTEM_ID]
        )
        _require(
            _float_equal(
                paired_view.get("answer_relevance_delta"),
                expected_relevance_delta,
            ),
            f"Paired answer relevance delta differs for {query_id}",
        )
        expected_outcome = (
            "g1_improved"
            if expected_task_delta > 0
            else "g1_regressed"
            if expected_task_delta < 0
            else "tied"
        )
        _require(
            paired_view.get("outcome") == expected_outcome,
            f"Paired outcome differs for {query_id}",
        )
        _require(
            paired_view.get("outcome_basis") == "offline_task_success",
            f"Paired outcome basis differs for {query_id}",
        )

    recomputed_systems = {
        G0_SYSTEM_ID: aggregate_system_metrics(normalized_by_system[G0_SYSTEM_ID], rag_system=False),
        G1_SYSTEM_ID: aggregate_system_metrics(normalized_by_system[G1_SYSTEM_ID], rag_system=True),
    }
    recomputed_paired = paired_summary(list(paired.values()))
    overall = load_json(overall_path)
    _require(overall.get("query_count") == EXPECTED_QUERY_COUNT, "Overall evaluation query count differs")
    _require(overall.get("formal_result_wrapper_count") == EXPECTED_QUERY_COUNT * 2, "Overall formal result wrapper count differs")
    _require(
        overall.get("formal_answer_count_semantics")
        == "formal_result_wrappers_including_explicit_failure_records",
        "Overall formal answer count semantics differ",
    )
    _require(overall.get("generation_seals") == seal["payload"].get("runs"), "Overall metrics generation seals differ")
    overall_judge = overall.get("judge")
    _require(isinstance(overall_judge, Mapping), "Overall metrics lacks judge provenance")
    _require(
        overall_judge.get("evaluation_input_sha256") == evaluation_input_sha256,
        "Overall judge offline input SHA differs from current files",
    )
    _require(
        overall_judge.get("evaluation_context_sha256")
        == evaluation_context_sha256,
        "Overall judge offline context SHA differs from current files",
    )
    judge_success_count = sum(
        judgment.get("status") == "success" for judgment in judgments.values()
    )
    judge_failure_count = EXPECTED_QUERY_COUNT - judge_success_count
    judge_retry_count = sum(
        int(judgment.get("retry_count", 0)) for judgment in judgments.values()
    )
    _require(
        overall_judge.get("logical_query_count") == EXPECTED_QUERY_COUNT,
        "Overall judge logical query count differs",
    )
    _require(
        overall_judge.get("completed_count") == judge_success_count,
        "Overall judge success count differs",
    )
    _require(
        overall_judge.get("failed_count") == judge_failure_count,
        "Overall judge failure count differs",
    )
    _require(
        overall_judge.get("completed_count_semantics") == "judge_call_success",
        "Overall judge completed-count semantics differ",
    )
    _require(
        overall_judge.get("judge_call_success_count") == judge_success_count,
        "Overall explicit judge-call success count differs",
    )
    _require(
        overall_judge.get("judge_call_failure_count") == judge_failure_count,
        "Overall explicit judge-call failure count differs",
    )
    _require(
        overall_judge.get("retry_count") == judge_retry_count,
        "Overall judge retry count differs",
    )
    overall_judge_signature = {
        "model_tag": overall_judge.get("model_tag", overall_judge.get("model")),
        "model_digest": overall_judge.get("model_digest"),
        "think": overall_judge.get("think", overall_judge.get("reasoning_effort")),
    }
    _require(overall_judge_signature == judge_signatures[0], "Overall judge model provenance differs from judgment journal")
    systems_payload = overall.get("systems")
    _require(isinstance(systems_payload, Mapping), "Overall metrics has no systems mapping")
    for system_id in GENERATION_SYSTEM_IDS:
        short_name = "g0" if system_id == G0_SYSTEM_ID else "g1"
        actual = systems_payload.get(system_id, systems_payload.get(short_name))
        _require(isinstance(actual, Mapping), f"Overall metrics lacks {system_id}")
        _compare_metrics(actual, recomputed_systems[system_id], f"overall.systems.{system_id}")
    actual_paired = overall.get("paired")
    _require(isinstance(actual_paired, Mapping), "Overall metrics lacks paired summary")
    _compare_metrics(actual_paired, recomputed_paired, "overall.paired")

    g0 = recomputed_systems[G0_SYSTEM_ID]
    g1 = recomputed_systems[G1_SYSTEM_ID]
    all_judge_calls_succeeded = all(
        judgment.get("status") == "success" for judgment in judgments.values()
    )
    engineering_gates = {
        "all_250_judgments_succeeded": all_judge_calls_succeeded,
        "all_250_judge_calls_succeeded": all_judge_calls_succeeded,
        "g0_structured_output_validity_at_least_98pct": g0["structured_output_validity"] >= 0.98,
        "g1_structured_output_validity_at_least_98pct": g1["structured_output_validity"] >= 0.98,
    }
    integrity_gates = {
        "g1_citation_validity_100pct": g1["citation_validity"] == 1.0,
    }
    quality_gates = {
        "task_success_absolute_gain_at_least_10pp": g1["task_success_rate"] - g0["task_success_rate"] >= 0.10,
        "g1_dialect_compatibility_not_below_g0": g1["dialect_compatibility"] >= g0["dialect_compatibility"],
        "g1_version_compatibility_not_below_g0": g1["version_compatibility"] >= g0["version_compatibility"],
        "g1_root_cause_accuracy_not_below_g0": g1["root_cause_accuracy"] >= g0["root_cause_accuracy"],
        "g1_sql_repair_correctness_not_below_g0": g1["sql_repair_correctness"] >= g0["sql_repair_correctness"],
    }
    # The saved quality claim may fail, but it must not disagree with recomputation.
    saved_target = actual_paired.get("success_target")
    if isinstance(saved_target, Mapping):
        observed_delta = g1["task_success_rate"] - g0["task_success_rate"]
        if "observed_absolute_delta" in saved_target:
            _require(_float_equal(saved_target["observed_absolute_delta"], observed_delta), "Saved Task Success delta differs")
        if "passed" in saved_target:
            _require(saved_target["passed"] is quality_gates["task_success_absolute_gain_at_least_10pp"], "Saved Task Success target claim differs")
        if "achieved" in saved_target:
            _require(saved_target["achieved"] is quality_gates["task_success_absolute_gain_at_least_10pp"], "Saved Task Success target claim differs")

    acceptance_path = _evaluation_path(paths, "acceptance.json")
    _require(acceptance_path.is_file(), "Standalone acceptance artifact is missing")
    acceptance = load_json(acceptance_path)
    _require(acceptance.get("schema_version") == "sqlmend-generation-acceptance-v1", "Acceptance schema differs")
    embedded_acceptance = overall.get("acceptance")
    _require(
        isinstance(embedded_acceptance, Mapping)
        and embedded_acceptance == acceptance,
        "Standalone acceptance differs from overall embedded acceptance",
    )
    expected_saved_checks = {
        "engineering": {
            "g0_has_250_formal_results": g0["formal_result_count"] == EXPECTED_QUERY_COUNT,
            "g1_has_250_formal_results": g1["formal_result_count"] == EXPECTED_QUERY_COUNT,
            "all_queries_have_judgment_records": len(judgments) == EXPECTED_QUERY_COUNT,
            **engineering_gates,
        },
        "integrity": {
            "sealed_before_reference_access": True,
            "paired_query_ids_identical": True,
            "g0_received_no_evidence": True,
            "g1_citation_validity_is_100pct": integrity_gates["g1_citation_validity_100pct"],
            "g1_context_is_fully_qrels_judged": all(
                row["g1"]["context_fully_judged"] is True for row in paired.values()
            ),
            "g0_citations_are_empty": all(row["g0"].get("citations_empty") is True for row in paired.values()),
        },
        "quality": {
            "g1_task_success_improves_by_at_least_10pp": quality_gates["task_success_absolute_gain_at_least_10pp"],
            "g1_dialect_compatibility_not_below_g0": quality_gates["g1_dialect_compatibility_not_below_g0"],
            "g1_version_compatibility_not_below_g0": quality_gates["g1_version_compatibility_not_below_g0"],
            "g1_root_cause_accuracy_not_below_g0": quality_gates["g1_root_cause_accuracy_not_below_g0"],
            "g1_sql_repair_correctness_not_below_g0": quality_gates["g1_sql_repair_correctness_not_below_g0"],
        },
    }
    for section, expected_checks in expected_saved_checks.items():
        saved_section = acceptance.get(section)
        _require(isinstance(saved_section, Mapping), f"Acceptance lacks {section} section")
        _require(saved_section.get("checks") == expected_checks, f"Acceptance {section} checks differ")
        expected_status = PASS if all(expected_checks.values()) else FAIL
        _require(saved_section.get("status") == expected_status, f"Acceptance {section} status differs")
    expected_artifact_status = (
        PASS
        if all(expected_saved_checks["engineering"].values())
        and all(expected_saved_checks["integrity"].values())
        else FAIL
    )
    _require(acceptance.get("artifact_validation_status") == expected_artifact_status, "Acceptance artifact status differs")
    expected_phase_success = expected_artifact_status == PASS and all(expected_saved_checks["quality"].values())
    _require(acceptance.get("phase_success") is expected_phase_success, "Acceptance phase_success differs")

    boundary = overall.get("offline_boundary")
    boundary_evidence = isinstance(boundary, Mapping) and (
        boundary.get("generation_outputs_sealed_before_reference_load") is True
        or boundary.get("seal_before_reference_read") is True
    )
    # If explicit provenance is stored in the seal, accept the equivalent field there.
    seal_boundary = seal["payload"].get("offline_boundary")
    boundary_evidence = boundary_evidence or (
        isinstance(seal_boundary, Mapping)
        and seal_boundary.get("generation_outputs_sealed_before_reference_load") is True
    )
    reference_access = seal["payload"].get("reference_access")
    boundary_evidence = boundary_evidence or (
        isinstance(reference_access, Mapping)
        and reference_access.get("seal_written_before_reference_access") is True
        and isinstance(reference_access.get("references_opened_at_utc"), str)
        and isinstance(reference_access.get("qrels_opened_at_utc"), str)
    )
    _require(boundary_evidence, "No explicit evidence that generation outputs were sealed before reference loading")
    for artifact in (judgments_path, paired_path, overall_path):
        _require(seal["path"].stat().st_mtime_ns <= artifact.stat().st_mtime_ns, f"{artifact.name} predates the generation seal")

    return {
        "generation_seal_sha256": seal["sha256"],
        "query_count": EXPECTED_QUERY_COUNT,
        "judgment_count": len(judgments),
        "judge_success_count": sum(
            judgment.get("status") == "success" for judgment in judgments.values()
        ),
        "judge_failure_count": sum(
            judgment.get("status") != "success" for judgment in judgments.values()
        ),
        "judge_call_success_count": sum(
            judgment.get("status") == "success" for judgment in judgments.values()
        ),
        "judge_call_failure_count": sum(
            judgment.get("status") != "success" for judgment in judgments.values()
        ),
        "judge_retry_count": sum(
            int(judgment.get("retry_count", 0)) for judgment in judgments.values()
        ),
        "paired_row_count": len(paired),
        "systems": recomputed_systems,
        "paired": recomputed_paired,
        "engineering_gates": engineering_gates,
        "integrity_gates": integrity_gates,
        "quality_gates": quality_gates,
        "quality_status": PASS if all(quality_gates.values()) else FAIL,
        "offline_boundary_verified": True,
    }


def _validate_test_evidence(paths: Any) -> dict[str, Any]:
    path = reports_directory(paths) / "test_results.json"
    _require(path.is_file(), "Test evidence is missing")
    evidence = load_json(path)
    current = release_source_snapshot(paths)
    before = evidence.get("source_tree_sha256_before", evidence.get("source_tree_sha256"))
    after = evidence.get("source_tree_sha256_after")
    saved_current = evidence.get("source_tree_sha256_current", after)
    _require(isinstance(before, str) and isinstance(after, str) and isinstance(saved_current, str), "Test evidence lacks before/after/current source hashes")
    _require(before == after == saved_current == current["tree_sha256"], "Test evidence source before/after/current/current-disk hashes differ")
    _require(evidence.get("returncode") == 0, "Test command did not exit successfully")
    _require(evidence.get("status") == PASS, "Test evidence status is not PASS")
    if "source_file_count" in evidence:
        _require(evidence["source_file_count"] == current["file_count"], "Test evidence source file count differs")
    return {
        "source_file_count": current["file_count"],
        "source_tree_sha256": current["tree_sha256"],
        "before_after_current_identical": True,
        "returncode": 0,
    }


def _json_report_value(value: Any) -> Any:
    """Convert validation details to bounded, standard-JSON-safe values."""

    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {
            str(key): _json_report_value(nested) for key, nested in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_report_value(nested) for nested in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_report_value(nested) for nested in value)
    return value


def _reportable_check_details(name: str, details: Any) -> Any:
    """Keep internal validation context out of the published report payload."""

    if not isinstance(details, Mapping):
        return _json_report_value(details)
    summary = dict(details)
    if name == "prepared_top5_and_no_labels":
        summary.pop("evidence", None)
        summary["evidence_payload_embedded"] = False
    elif name == "formal_runs_and_shared_generation_contract":
        systems = summary.get("systems")
        if isinstance(systems, Mapping):
            summarized_systems: dict[str, Any] = {}
            for system_id, system_details in systems.items():
                if isinstance(system_details, Mapping):
                    system_summary = dict(system_details)
                    system_summary.pop("records", None)
                    system_summary["record_payloads_embedded"] = False
                    summarized_systems[str(system_id)] = system_summary
                else:
                    summarized_systems[str(system_id)] = system_details
            summary["systems"] = summarized_systems
    return _json_report_value(summary)


def validate_release(paths: Any) -> dict[str, Any]:
    """Run all integrity checks while reporting quality failure separately."""

    checks: list[dict[str, Any]] = []
    context: dict[str, Any] = {}

    def run_check(name: str, function: Any) -> Any:
        try:
            details = function()
            if isinstance(details, Mapping) and details.get("status") == FAIL:
                raise ReleaseValidationError("; ".join(str(item) for item in details.get("errors", [])) or f"{name} failed")
            checks.append(
                {
                    "name": name,
                    "status": PASS,
                    "details": _reportable_check_details(name, details),
                }
            )
            return details
        except Exception as exc:  # A validation report must preserve every failure.
            checks.append({"name": name, "status": FAIL, "error": str(exc)})
            return None

    context["frozen"] = run_check("frozen_online_input_hashes", lambda: _validate_frozen_inputs(paths))
    context["prepared"] = run_check("prepared_top5_and_no_labels", lambda: _validate_prepared_inputs(paths))
    if context["prepared"] is not None:
        context["runs"] = run_check(
            "formal_runs_and_shared_generation_contract",
            lambda: _validate_formal_runs(paths, context["prepared"]),
        )
    else:
        context["runs"] = None
        checks.append({"name": "formal_runs_and_shared_generation_contract", "status": FAIL, "error": "blocked by prepared-input validation"})
    protected = run_check("protected_before_after_current", lambda: verify_protected_audits(paths))
    context["protected"] = protected
    if context["runs"] is not None and context["prepared"] is not None:
        context["evaluation"] = run_check(
            "sealed_offline_evaluation_and_metric_arithmetic",
            lambda: _validate_offline_evaluation(paths, context["runs"], context["prepared"]),
        )
    else:
        context["evaluation"] = None
        checks.append({"name": "sealed_offline_evaluation_and_metric_arithmetic", "status": FAIL, "error": "blocked by online artifact validation"})
    context["tests"] = run_check("test_evidence_source_stability", lambda: _validate_test_evidence(paths))
    context["manifest"] = run_check("manifest_fixed_point_and_file_hashes", lambda: verify_manifest(paths))

    failed = [check for check in checks if check["status"] == FAIL]
    artifact_check_status = PASS if not failed else FAIL
    evaluation = context.get("evaluation")
    engineering_gates = (
        evaluation.get("engineering_gates")
        if isinstance(evaluation, Mapping)
        else None
    )
    integrity_gates = (
        evaluation.get("integrity_gates")
        if isinstance(evaluation, Mapping)
        else None
    )
    engineering_gate_status = (
        PASS
        if isinstance(engineering_gates, Mapping)
        and all(value is True for value in engineering_gates.values())
        else FAIL
        if isinstance(engineering_gates, Mapping)
        else "NOT_EVALUATED"
    )
    integrity_status = (
        PASS
        if isinstance(integrity_gates, Mapping)
        and all(value is True for value in integrity_gates.values())
        else FAIL
        if isinstance(integrity_gates, Mapping)
        else "NOT_EVALUATED"
    )
    engineering_status = (
        PASS
        if artifact_check_status == PASS
        and engineering_gate_status == PASS
        and integrity_status == PASS
        else FAIL
    )
    quality_status = evaluation.get("quality_status") if isinstance(evaluation, Mapping) else "NOT_EVALUATED"
    gate_errors: list[str] = []
    for scope, gates in (
        ("engineering", engineering_gates),
        ("integrity", integrity_gates),
    ):
        if isinstance(gates, Mapping):
            gate_errors.extend(
                f"{scope} acceptance gate failed: {name}"
                for name, passed in gates.items()
                if passed is not True
            )
    report = {
        "schema_version": "sqlmend-generation-v1-validation-report-v1",
        "release": "generation-v1",
        "evaluation_label": "machine-proposed development evaluation",
        "machine_proposed_development_only": True,
        "artifact_check_status": artifact_check_status,
        "engineering_gate_status": engineering_gate_status,
        "integrity_status": integrity_status,
        "engineering_status": engineering_status,
        "quality_status": quality_status,
        "status": engineering_status,
        "overall_success": engineering_status == PASS and quality_status == PASS,
        "check_count": len(checks),
        "passed_check_count": len(checks) - len(failed),
        "failed_check_count": len(failed),
        "checks": checks,
        "errors": [str(check.get("error")) for check in failed] + gate_errors,
        "engineering_gates": engineering_gates,
        "integrity_gates": integrity_gates,
        "quality_gates": evaluation.get("quality_gates") if isinstance(evaluation, Mapping) else None,
    }
    return report


def write_validation_report(paths: Any, output_path: Path | None = None) -> dict[str, Any]:
    report = validate_release(paths)
    write_json(output_path or reports_directory(paths) / "validation_report.json", report)
    return report


__all__ = [
    "EXPECTED_FORMAL_ANSWER_COUNT",
    "EXPECTED_QUERY_COUNT",
    "EXPECTED_QUERY_IDS",
    "FAIL",
    "FROZEN_INPUT_SHA256",
    "PASS",
    "ReleaseValidationError",
    "TOP_K",
    "validate_release",
    "write_validation_report",
]
