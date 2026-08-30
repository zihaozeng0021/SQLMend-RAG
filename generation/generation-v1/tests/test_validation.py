from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sqlmend_generation_v1.audit import canonical_json_sha256, sha256_file, write_json
from sqlmend_generation_v1.contracts import G0_SYSTEM_ID, G1_SYSTEM_ID
from sqlmend_generation_v1.io import sha256_json, write_jsonl
from sqlmend_generation_v1.metrics import (
    NOT_APPLICABLE,
    aggregate_system_metrics,
    paired_summary,
)
from sqlmend_generation_v1.validation import (
    EXPECTED_MODEL_DIGEST,
    EXPECTED_MODEL_TAG,
    EXPECTED_QUERY_IDS,
    PASS,
    ReleaseValidationError,
    _reportable_check_details,
    _validate_formal_runs,
    _validate_offline_evaluation,
    _validate_test_evidence,
)


def _config() -> dict[str, object]:
    return {
        "schema_version": "sqlmend-generation-config-v1",
        "experiment_id": "phase10-test",
        "expected_query_count": 250,
        "systems": {
            G0_SYSTEM_ID: {"evidence_mode": "none", "output_file": "g0.jsonl"},
            G1_SYSTEM_ID: {
                "evidence_mode": "retrieval_v1_final_top5",
                "output_file": "g1.jsonl",
            },
        },
        "retrieval": {
            "system_id": "hybrid_rrf_dialect_version_lexical_rerank_v1",
            "top_k": 5,
            "rationale": "fixed top five fixture",
        },
        "ollama": {
            "base_url": "http://127.0.0.1:11434",
            "model_tag": EXPECTED_MODEL_TAG,
            "model_digest": EXPECTED_MODEL_DIGEST,
            "think": False,
            "timeout_seconds": 300,
            "options": {
                "temperature": 0.0,
                "seed": 20260830,
                "num_ctx": 16384,
                "num_predict": 1024,
                "top_k": 40,
                "top_p": 0.9,
                "repeat_penalty": 1.0,
            },
        },
        "retry_policy": {
            "max_attempts": 3,
            "retry_on": [
                "transport_or_protocol_error",
                "invalid_json",
                "output_schema_violation",
                "citation_contract_violation",
            ],
        },
    }


def _answer(citations: list[str]) -> dict[str, object]:
    return {
        "diagnosis": "The statement uses the wrong construct.",
        "root_cause": "The selected construct is not valid for this input.",
        "corrected_sql": "SELECT 1;",
        "explanation": "Use the target dialect's supported construct.",
        "dialect_compatibility": {
            "status": "compatible",
            "explanation": "Valid for the target dialect.",
        },
        "version_compatibility": {
            "status": "compatible",
            "explanation": "Valid for the target version.",
        },
        "confidence": 0.8,
        "insufficient_evidence": False,
        "citations": citations,
    }


def _paths(tmp_path: Path) -> SimpleNamespace:
    release = tmp_path / "generation" / "generation-v1"
    config_file = release / "config/generation.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(yaml.safe_dump(_config(), sort_keys=False), encoding="utf-8")
    answer_schema = release / "schema/answer.schema.json"
    write_json(answer_schema, {"type": "object"})
    g0 = release / "runs/g0_closed_book_dev250.jsonl"
    g1 = release / "runs/g1_retrieval_v1_rag_dev250.jsonl"
    evaluation = release / "evaluation"
    reports = release / "reports"
    qrels = tmp_path / "retrieval/baseline/qrels/qrels_effective_dev250.trec"
    references = tmp_path / "annotation/codex/dev_250.jsonl"
    prepared_queries = release / "prepared_inputs/online_queries.jsonl"
    g1_evidence = release / "prepared_inputs/g1_evidence_top5.jsonl"

    paths = SimpleNamespace(
        root=tmp_path,
        release=release,
        config_file=config_file,
        answer_schema=answer_schema,
        g0_run=g0,
        g1_run=g1,
        evaluation=evaluation,
        reports=reports,
        qrels=qrels,
        references=references,
        prepared_queries=prepared_queries,
        g1_evidence=g1_evidence,
    )
    paths.result_path = lambda system_id: g0 if system_id == G0_SYSTEM_ID else g1
    return paths


def _evidence() -> dict[str, dict[str, object]]:
    return {
        query_id: {
            "query_id": query_id,
            "passages": [
                {"passage_id": f"passage-{rank:02d}"} for rank in range(1, 6)
            ],
        }
        for query_id in EXPECTED_QUERY_IDS
    }


def _wrapper(
    query_id: str,
    system_id: str,
    schema_hash: str,
    evidence_ids: list[str],
) -> dict[str, object]:
    citations = [] if system_id == G0_SYSTEM_ID else evidence_ids[:1]
    attempt = {
        "attempt_number": 1,
        "status": "success",
        "wall_ms": 10.0,
        "structured_output_valid": True,
        "contract_valid": True,
        "validation_errors": [],
    }
    return {
        "schema_version": "sqlmend-generation-record-v1",
        "experiment_id": "phase10-test",
        "query_id": query_id,
        "system_id": system_id,
        "status": "success",
        "answer": _answer(citations),
        "structured_output_valid": True,
        "contract_valid": True,
        "validation_errors": [],
        "failure": None,
        "input_provenance": {
            "serialized_query_sha256": "1" * 64,
            "evidence_sha256": None if not evidence_ids else "2" * 64,
            "evidence_passage_ids": evidence_ids,
            "prompt_sha256": "3" * 64,
        },
        "generation_provenance": {
            "run_id": "phase10-test",
            "model_tag": EXPECTED_MODEL_TAG,
            "model_digest": EXPECTED_MODEL_DIGEST,
            "ollama_version": "fixture",
            "think": False,
            "options": _config()["ollama"]["options"],
            "retry_policy": _config()["retry_policy"],
            "output_schema_sha256": schema_hash,
            "system_prompt_sha256": "4" * 64,
            "prompt_template_sha256": "5" * 64,
            "request_sha256": "6" * 64,
            "raw_response_sha256": "7" * 64,
            "attempt_count": 1,
            "retry_count": 0,
            "attempts": [attempt],
        },
        "latency": {
            "wall_ms": 10.5,
            "ollama_total_ms": 9.0,
            "load_ms": 0.0,
            "prompt_eval_ms": 2.0,
            "eval_ms": 7.0,
            "prompt_tokens": 10,
            "completion_tokens": 20,
        },
    }


def _formal_fixture(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, object], dict[str, object]]:
    paths = _paths(tmp_path)
    evidence = _evidence()
    schema_hash = sha256_file(paths.answer_schema)
    g0_rows = [
        _wrapper(query_id, G0_SYSTEM_ID, schema_hash, [])
        for query_id in EXPECTED_QUERY_IDS
    ]
    g1_rows = [
        _wrapper(
            query_id,
            G1_SYSTEM_ID,
            schema_hash,
            [str(item["passage_id"]) for item in evidence[query_id]["passages"]],
        )
        for query_id in EXPECTED_QUERY_IDS
    ]
    write_jsonl(paths.g0_run, g0_rows)
    write_jsonl(paths.g1_run, g1_rows)
    prepared = {"evidence": evidence}
    details = _validate_formal_runs(paths, prepared)
    return paths, prepared, details


def test_formal_runs_recompute_shared_qwen_false_contract_and_citations(tmp_path: Path) -> None:
    _, _, details = _formal_fixture(tmp_path)

    assert details["shared_configuration"]["model_tag"] == EXPECTED_MODEL_TAG
    assert details["shared_configuration"]["model_digest"] == EXPECTED_MODEL_DIGEST
    assert details["shared_configuration"]["think"] is False
    assert details["systems"][G0_SYSTEM_ID]["structured_output_validity"] == 1.0
    assert details["systems"][G1_SYSTEM_ID]["citation_validity"] == 1.0


def test_structured_validity_is_distinct_from_citation_contract_failure(tmp_path: Path) -> None:
    paths, prepared, _ = _formal_fixture(tmp_path)
    rows = json.loads(
        "[" + ",".join(paths.g1_run.read_text(encoding="utf-8").splitlines()) + "]"
    )
    failed = rows[0]
    failed["status"] = "failed"
    failed["answer"] = None
    failed["structured_output_valid"] = True
    failed["contract_valid"] = False
    failed["validation_errors"] = ["citation is outside supplied Top-5"]
    failed["failure"] = {
        "type": "citation_contract_violation",
        "message": "citation is outside supplied Top-5",
        "retry_exhausted": False,
    }
    final_attempt = failed["generation_provenance"]["attempts"][-1]
    final_attempt["status"] = "failed"
    final_attempt["error_type"] = "citation_contract_violation"
    final_attempt["error_message"] = "citation is outside supplied Top-5"
    final_attempt["structured_output_valid"] = True
    final_attempt["contract_valid"] = False
    final_attempt["validation_errors"] = ["citation is outside supplied Top-5"]
    write_jsonl(paths.g1_run, rows)

    details = _validate_formal_runs(paths, prepared)
    g1 = details["systems"][G1_SYSTEM_ID]
    assert g1["structured_output_validity"] == 1.0
    assert g1["generation_contract_success_count"] == 249
    assert g1["generation_contract_failure_count"] == 1


def test_validation_report_details_are_bounded_and_json_safe(tmp_path: Path) -> None:
    prepared = _reportable_check_details(
        "prepared_top5_and_no_labels",
        {"evidence_query_count": 250, "evidence": {"DEV0001": {"large": True}}},
    )
    formal = _reportable_check_details(
        "formal_runs_and_shared_generation_contract",
        {
            "systems": {
                G0_SYSTEM_ID: {
                    "path": tmp_path / "g0.jsonl",
                    "records": {"DEV0001": {"large": True}},
                    "record_count": 250,
                }
            }
        },
    )

    assert "evidence" not in prepared
    assert prepared["evidence_payload_embedded"] is False
    assert "records" not in formal["systems"][G0_SYSTEM_ID]
    assert formal["systems"][G0_SYSTEM_ID]["record_payloads_embedded"] is False
    assert formal["systems"][G0_SYSTEM_ID]["path"].endswith("g0.jsonl")
    json.dumps({"prepared": prepared, "formal": formal}, allow_nan=False)


def test_formal_runs_reject_g1_citation_outside_actual_top5(tmp_path: Path) -> None:
    paths, prepared, _ = _formal_fixture(tmp_path)
    rows = json.loads("[" + ",".join(paths.g1_run.read_text(encoding="utf-8").splitlines()) + "]")
    rows[0]["answer"]["citations"] = ["invented-passage"]
    write_jsonl(paths.g1_run, rows)

    with pytest.raises(ReleaseValidationError, match="Successful answer contract differs"):
        _validate_formal_runs(paths, prepared)


def _write_offline_fixture(
    paths: SimpleNamespace,
    prepared: dict[str, object],
    formal: dict[str, object],
) -> None:
    write_jsonl(
        paths.prepared_queries,
        [{"query_id": query_id, "fixture": "safe"} for query_id in EXPECTED_QUERY_IDS],
    )
    write_jsonl(
        paths.g1_evidence,
        [prepared["evidence"][query_id] for query_id in EXPECTED_QUERY_IDS],
    )
    write_jsonl(
        paths.references,
        [{"query_id": query_id, "fixture": "reference"} for query_id in EXPECTED_QUERY_IDS],
    )
    paths.qrels.parent.mkdir(parents=True, exist_ok=True)
    qrel_lines = []
    for query_id in EXPECTED_QUERY_IDS:
        for rank in range(1, 6):
            qrel_lines.append(
                f"{query_id} 0 passage-{rank:02d} {1 if rank == 1 else 0}\n"
            )
    paths.qrels.write_text("".join(qrel_lines), encoding="utf-8")
    evaluation_input_sha256 = {
        "development_references_file": sha256_file(paths.references),
        "effective_qrels_file": sha256_file(paths.qrels),
        "prepared_queries_file": sha256_file(paths.prepared_queries),
        "g1_evidence_file": sha256_file(paths.g1_evidence),
    }
    evaluation_context_sha256 = sha256_json(evaluation_input_sha256)

    query_hash = hashlib.sha256(
        ("\n".join(EXPECTED_QUERY_IDS) + "\n").encode("utf-8")
    ).hexdigest()
    seal_runs: dict[str, object] = {}
    for short, system_id in (("g0", G0_SYSTEM_ID), ("g1", G1_SYSTEM_ID)):
        system = formal["systems"][system_id]
        seal_runs[short] = {
            "path": str(system["path"]),
            "sha256": system["sha256"],
            "byte_size": system["path"].stat().st_size,
            "record_count": 250,
            "success_count_semantics": "generation_contract_success",
            "success_count": 250,
            "failed_count": 0,
            "generation_contract_success_count": 250,
            "generation_contract_failure_count": 0,
            "query_ids_sha256": query_hash,
        }
    seal = {
        "schema_version": "sqlmend-generation-seal-v1",
        "sealed_at_utc": "2026-08-30T08:00:00Z",
        "runs": seal_runs,
        "reference_access": {
            "seal_written_before_reference_access": True,
            "references_opened_at_utc": "2026-08-30T08:00:01Z",
            "qrels_opened_at_utc": "2026-08-30T08:00:02Z",
        },
        "offline_evaluation_inputs": {
            "sha256": evaluation_input_sha256,
            "context_sha256": evaluation_context_sha256,
        },
        "event_sequence": [
            {"event": "generation_runs_sealed", "at_utc": "2026-08-30T08:00:00Z"},
            {"event": "offline_references_opened", "at_utc": "2026-08-30T08:00:01Z"},
        ],
    }
    paths.evaluation.mkdir(parents=True, exist_ok=True)
    write_json(paths.evaluation / "generation_seal.json", seal)

    judgments: list[dict[str, object]] = []
    paired: list[dict[str, object]] = []
    g0_metric_rows: list[dict[str, object]] = []
    g1_metric_rows: list[dict[str, object]] = []
    for ordinal, query_id in enumerate(EXPECTED_QUERY_IDS, start=1):
        g0_decision = {
            "root_cause_correct": False,
            "sql_repair_correct": False,
            "dialect_compatible": False,
            "version_compatible": False,
            "answer_relevance": 0.5,
            "faithfulness": 0.0,
            "citation_coverage": 0.0,
            "reason": "fixture g0",
        }
        g1_decision = {
            "root_cause_correct": True,
            "sql_repair_correct": True,
            "dialect_compatible": True,
            "version_compatible": True,
            "answer_relevance": 1.0,
            "faithfulness": 0.8,
            "citation_coverage": 0.8,
            "reason": "fixture g1",
        }
        assignment = (
            {"A": G0_SYSTEM_ID, "B": G1_SYSTEM_ID}
            if ordinal % 2 == 1
            else {"A": G1_SYSTEM_ID, "B": G0_SYSTEM_ID}
        )
        decision = {"g0": g0_decision, "g1": g1_decision}
        anonymous_decision = {
            label: decision[
                "g0" if assignment[label] == G0_SYSTEM_ID else "g1"
            ]
            for label in ("A", "B")
        }
        judgments.append(
            {
                "schema_version": "sqlmend-generation-judgment-v1",
                "query_id": query_id,
                "ordinal": ordinal,
                "assignment": assignment,
                "status": "success",
                "model": EXPECTED_MODEL_TAG,
                "model_tag": EXPECTED_MODEL_TAG,
                "model_digest": EXPECTED_MODEL_DIGEST,
                "think": False,
                "policy_sha256": "8" * 64,
                "run_sha256": {
                    "g0": formal["systems"][G0_SYSTEM_ID]["sha256"],
                    "g1": formal["systems"][G1_SYSTEM_ID]["sha256"],
                },
                "evaluation_input_sha256": evaluation_input_sha256,
                "evaluation_context_sha256": evaluation_context_sha256,
                "attempt_count": 1,
                "retry_count": 0,
                "attempts": [
                    {
                        "attempt": 1,
                        "status": "success",
                        "wall_ms": 1.0,
                        "response_sha256": canonical_json_sha256(
                            anonymous_decision
                        ),
                    }
                ],
                "decision": decision,
            }
        )
        g0_view = {
            "status": "success",
            "structured_output_valid": True,
            "contract_valid": True,
            "generation_attempt_count": 1,
            "generation_retry_count": 0,
            "root_cause_correct": False,
            "sql_repair_correct": False,
            "dialect_compatible": False,
            "version_compatible": False,
            "answer_relevance": 0.5,
            "task_success": False,
            "latency_wall_ms": 10.5,
            "judge_reason": "fixture g0",
            "citation_validity": NOT_APPLICABLE,
            "citation_coverage": NOT_APPLICABLE,
            "faithfulness": NOT_APPLICABLE,
            "context_precision": NOT_APPLICABLE,
            "context_query_hit": NOT_APPLICABLE,
            "context_fully_judged": NOT_APPLICABLE,
            "citation_count": 0,
            "citations_empty": True,
        }
        g1_view = {
            "status": "success",
            "structured_output_valid": True,
            "contract_valid": True,
            "generation_attempt_count": 1,
            "generation_retry_count": 0,
            "root_cause_correct": True,
            "sql_repair_correct": True,
            "dialect_compatible": True,
            "version_compatible": True,
            "answer_relevance": 1.0,
            "task_success": True,
            "latency_wall_ms": 10.5,
            "judge_reason": "fixture g1",
            "citation_validity": 1.0,
            "citation_coverage": 0.8,
            "faithfulness": 0.8,
            "context_precision": 0.2,
            "context_query_hit": True,
            "context_fully_judged": True,
        }
        paired.append(
            {
                "schema_version": "sqlmend-generation-paired-query-v1",
                "query_id": query_id,
                "ordinal": ordinal,
                "judge_status": "success",
                "judge_attempt_count": 1,
                "judge_retry_count": 0,
                "g0": g0_view,
                "g1": g1_view,
                "paired": {
                    "task_success_delta": 1,
                    "semantic_component_delta": 4,
                    "answer_relevance_delta": 0.5,
                    "outcome": "g1_improved",
                    "outcome_basis": "offline_task_success",
                },
            }
        )
        g0_metric_rows.append(dict(g0_view))
        g1_metric_rows.append(dict(g1_view))
    write_jsonl(paths.evaluation / "judgments.jsonl", judgments)
    write_jsonl(paths.evaluation / "per_query_comparison.jsonl", paired)

    g0_metrics = aggregate_system_metrics(g0_metric_rows, rag_system=False)
    g1_metrics = aggregate_system_metrics(g1_metric_rows, rag_system=True)
    paired_metrics = paired_summary(paired)
    paired_metrics["success_target"] = {
        "required_absolute_delta": 0.10,
        "achieved": True,
    }
    overall = {
        "schema_version": "sqlmend-generation-evaluation-v1",
        "query_count": 250,
        "formal_answer_count": 500,
        "formal_result_wrapper_count": 500,
        "formal_answer_count_semantics": (
            "formal_result_wrappers_including_explicit_failure_records"
        ),
        "generation_seals": seal_runs,
        "judge": {
            "model": EXPECTED_MODEL_TAG,
            "model_tag": EXPECTED_MODEL_TAG,
            "model_digest": EXPECTED_MODEL_DIGEST,
            "think": False,
            "evaluation_input_sha256": evaluation_input_sha256,
            "evaluation_context_sha256": evaluation_context_sha256,
            "logical_query_count": 250,
            "completed_count": 250,
            "failed_count": 0,
            "completed_count_semantics": "judge_call_success",
            "judge_call_success_count": 250,
            "judge_call_failure_count": 0,
            "retry_count": 0,
        },
        "systems": {
            "g0": {"system_id": G0_SYSTEM_ID, **g0_metrics},
            "g1": {"system_id": G1_SYSTEM_ID, **g1_metrics},
        },
        "paired": paired_metrics,
    }
    engineering = {
        "g0_has_250_formal_results": True,
        "g1_has_250_formal_results": True,
        "all_queries_have_judgment_records": True,
        "all_250_judgments_succeeded": True,
        "all_250_judge_calls_succeeded": True,
        "g0_structured_output_validity_at_least_98pct": True,
        "g1_structured_output_validity_at_least_98pct": True,
    }
    integrity = {
        "sealed_before_reference_access": True,
        "paired_query_ids_identical": True,
        "g0_received_no_evidence": True,
        "g1_citation_validity_is_100pct": True,
        "g1_context_is_fully_qrels_judged": True,
        "g0_citations_are_empty": True,
    }
    quality = {
        "g1_task_success_improves_by_at_least_10pp": True,
        "g1_dialect_compatibility_not_below_g0": True,
        "g1_version_compatibility_not_below_g0": True,
        "g1_root_cause_accuracy_not_below_g0": True,
        "g1_sql_repair_correctness_not_below_g0": True,
    }
    acceptance = {
        "schema_version": "sqlmend-generation-acceptance-v1",
        "engineering": {"status": PASS, "checks": engineering},
        "integrity": {"status": PASS, "checks": integrity},
        "quality": {"status": PASS, "checks": quality},
        "artifact_validation_status": PASS,
        "phase_success": True,
        "quality_failure_does_not_suppress_artifacts": True,
    }
    overall["acceptance"] = acceptance
    write_json(paths.evaluation / "overall_metrics.json", overall)
    write_json(paths.evaluation / "acceptance.json", acceptance)


def test_offline_evaluation_recomputes_conjunction_metrics_seal_and_na(tmp_path: Path) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)

    details = _validate_offline_evaluation(paths, formal, prepared)

    assert details["offline_boundary_verified"] is True
    assert details["systems"][G0_SYSTEM_ID]["faithfulness"] == NOT_APPLICABLE
    assert details["systems"][G0_SYSTEM_ID]["context_precision"] == NOT_APPLICABLE
    assert details["systems"][G1_SYSTEM_ID]["context_precision"] == 0.2
    assert details["engineering_gates"]["all_250_judgments_succeeded"] is True
    assert details["engineering_gates"]["all_250_judge_calls_succeeded"] is True
    assert details["judge_success_count"] == 250
    assert details["judge_failure_count"] == 0
    assert details["judge_call_success_count"] == 250
    assert details["judge_call_failure_count"] == 0
    assert details["quality_status"] == PASS


def test_offline_evaluation_requires_standalone_and_embedded_acceptance_identity(
    tmp_path: Path,
) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)

    acceptance_path = paths.evaluation / "acceptance.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["phase_success"] = False
    write_json(acceptance_path, acceptance)

    with pytest.raises(
        ReleaseValidationError,
        match="Standalone acceptance differs from overall embedded acceptance",
    ):
        _validate_offline_evaluation(paths, formal, prepared)


def test_offline_evaluation_recomputes_judge_attempt_contract(tmp_path: Path) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)
    judgments_path = paths.evaluation / "judgments.jsonl"
    baseline = [
        json.loads(line)
        for line in judgments_path.read_text(encoding="utf-8").splitlines()
    ]

    cases = (
        ("numbering", "Judge attempt numbering differs"),
        ("attempt_count", "Judge attempt_count differs"),
        ("retry_count", "Judge retry_count differs"),
        ("final_status", "Judge status differs from final attempt"),
        ("response_hash", "Judge final response hash differs from decision"),
        ("too_many", "Judge attempts must be between 1 and 3"),
    )
    for case, expected_error in cases:
        judgments = json.loads(json.dumps(baseline))
        judgment = judgments[0]
        if case == "numbering":
            judgment["attempts"][0]["attempt"] = 2
        elif case == "attempt_count":
            judgment["attempt_count"] = 2
        elif case == "retry_count":
            judgment["retry_count"] = 1
        elif case == "final_status":
            judgment["status"] = "failed"
        elif case == "response_hash":
            judgment["attempts"][0]["response_sha256"] = "0" * 64
        else:
            judgment["attempts"] = [
                {
                    "attempt": number,
                    "status": "failed" if number < 4 else "success",
                    "wall_ms": 1.0,
                    "response_sha256": (
                        judgment["attempts"][0]["response_sha256"]
                        if number == 4
                        else None
                    ),
                }
                for number in range(1, 5)
            ]
            judgment["attempt_count"] = 4
            judgment["retry_count"] = 3
        write_jsonl(judgments_path, judgments)
        with pytest.raises(ReleaseValidationError, match=expected_error):
            _validate_offline_evaluation(paths, formal, prepared)

    write_jsonl(judgments_path, baseline)


def test_offline_evaluation_cross_binds_paired_rows_to_journal_and_runs(
    tmp_path: Path,
) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)
    paired_path = paths.evaluation / "per_query_comparison.jsonl"
    baseline = [
        json.loads(line)
        for line in paired_path.read_text(encoding="utf-8").splitlines()
    ]

    cases = (
        ("judge_status", "Paired judge status differs"),
        ("judge_attempt_count", "Paired judge attempt count differs"),
        ("generation_status", "Paired generation status differs"),
        ("semantic", "Paired semantic judgment differs from journal"),
        ("task_delta", "Paired Task Success delta differs"),
        ("outcome", "Paired outcome differs"),
    )
    for case, expected_error in cases:
        rows = json.loads(json.dumps(baseline))
        row = rows[0]
        if case == "judge_status":
            row["judge_status"] = "failed"
        elif case == "judge_attempt_count":
            row["judge_attempt_count"] = 2
        elif case == "generation_status":
            row["g0"]["status"] = "failed"
        elif case == "semantic":
            row["g1"]["root_cause_correct"] = False
        elif case == "task_delta":
            row["paired"]["task_success_delta"] = 0
        else:
            row["paired"]["outcome"] = "tied"
        write_jsonl(paired_path, rows)
        with pytest.raises(ReleaseValidationError, match=expected_error):
            _validate_offline_evaluation(paths, formal, prepared)

    write_jsonl(paired_path, baseline)


def test_offline_evaluation_rejects_nonconjunctive_task_success(tmp_path: Path) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)
    rows = json.loads(
        "["
        + ",".join(
            (paths.evaluation / "per_query_comparison.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        + "]"
    )
    rows[0]["g0"]["task_success"] = True
    write_jsonl(paths.evaluation / "per_query_comparison.jsonl", rows)

    with pytest.raises(ReleaseValidationError, match="required conjunction"):
        _validate_offline_evaluation(paths, formal, prepared)


@pytest.mark.parametrize("input_name", ["references", "qrels"])
def test_offline_evaluation_rejects_stale_reference_or_qrels_binding(
    tmp_path: Path,
    input_name: str,
) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)
    path = getattr(paths, input_name)
    if input_name == "references":
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    else:
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace(" 1\n", " 0\n", 1), encoding="utf-8")

    with pytest.raises(ReleaseValidationError, match="offline evaluation input SHA"):
        _validate_offline_evaluation(paths, formal, prepared)


def test_offline_evaluation_records_judge_call_gate_failure(tmp_path: Path) -> None:
    paths, prepared, formal = _formal_fixture(tmp_path)
    _write_offline_fixture(paths, prepared, formal)

    judgments_path = paths.evaluation / "judgments.jsonl"
    judgments = [
        json.loads(line)
        for line in judgments_path.read_text(encoding="utf-8").splitlines()
    ]
    failed_side = {
        "root_cause_correct": False,
        "sql_repair_correct": False,
        "dialect_compatible": False,
        "version_compatible": False,
        "answer_relevance": 0.0,
        "faithfulness": 0.0,
        "citation_coverage": 0.0,
        "reason": "judge failed or answer unavailable",
    }
    judgments[0]["status"] = "failed"
    judgments[0]["attempts"] = [
        {
            "attempt": 1,
            "status": "failed",
            "wall_ms": 1.0,
            "error_type": "ValueError",
            "error": "fixture judge failure",
        }
    ]
    judgments[0]["decision"] = {
        "g0": dict(failed_side),
        "g1": dict(failed_side),
    }
    write_jsonl(judgments_path, judgments)

    paired_path = paths.evaluation / "per_query_comparison.jsonl"
    paired = [
        json.loads(line)
        for line in paired_path.read_text(encoding="utf-8").splitlines()
    ]
    paired[0]["judge_status"] = "failed"
    for system in ("g0", "g1"):
        paired[0][system].update(
            {
                **{
                    field: False
                    for field in (
                        "root_cause_correct",
                        "sql_repair_correct",
                        "dialect_compatible",
                        "version_compatible",
                    )
                },
                "answer_relevance": 0.0,
                "task_success": False,
                "judge_reason": failed_side["reason"],
            }
        )
    paired[0]["g1"]["faithfulness"] = 0.0
    paired[0]["g1"]["citation_coverage"] = 0.0
    paired[0]["paired"] = {
        "task_success_delta": 0,
        "semantic_component_delta": 0,
        "answer_relevance_delta": 0.0,
        "outcome": "tied",
        "outcome_basis": "offline_task_success",
    }
    write_jsonl(paired_path, paired)

    overall_path = paths.evaluation / "overall_metrics.json"
    overall = json.loads(overall_path.read_text(encoding="utf-8"))
    overall["judge"]["completed_count"] = 249
    overall["judge"]["failed_count"] = 1
    overall["judge"]["judge_call_success_count"] = 249
    overall["judge"]["judge_call_failure_count"] = 1
    overall["systems"] = {
        "g0": {
            "system_id": G0_SYSTEM_ID,
            **aggregate_system_metrics(
                [row["g0"] for row in paired], rag_system=False
            ),
        },
        "g1": {
            "system_id": G1_SYSTEM_ID,
            **aggregate_system_metrics(
                [row["g1"] for row in paired], rag_system=True
            ),
        },
    }
    overall["paired"] = paired_summary(paired)
    overall["paired"]["success_target"] = {
        "required_absolute_delta": 0.10,
        "achieved": True,
    }

    acceptance_path = paths.evaluation / "acceptance.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["engineering"]["checks"]["all_250_judgments_succeeded"] = False
    acceptance["engineering"]["checks"]["all_250_judge_calls_succeeded"] = False
    acceptance["engineering"]["status"] = "FAIL"
    acceptance["artifact_validation_status"] = "FAIL"
    acceptance["phase_success"] = False
    overall["acceptance"] = acceptance
    write_json(overall_path, overall)
    write_json(acceptance_path, acceptance)

    details = _validate_offline_evaluation(paths, formal, prepared)
    assert details["engineering_gates"]["all_250_judgments_succeeded"] is False
    assert details["engineering_gates"]["all_250_judge_calls_succeeded"] is False
    assert details["judge_call_success_count"] == 249
    assert details["judge_call_failure_count"] == 1


def test_test_evidence_binds_source_before_after_and_current(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    for relative in (
        "schema/evidence.schema.json",
        "src/sqlmend_generation_v1/module.py",
        "tests/test_module.py",
        "pyproject.toml",
        "requirements.txt",
        "README.md",
    ):
        target = paths.release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n", encoding="utf-8")
    from sqlmend_generation_v1.manifest import release_source_snapshot

    source = release_source_snapshot(paths)
    paths.reports.mkdir(parents=True, exist_ok=True)
    write_json(
        paths.reports / "test_results.json",
        {
            "status": PASS,
            "returncode": 0,
            "source_file_count": source["file_count"],
            "source_tree_sha256_before": source["tree_sha256"],
            "source_tree_sha256_after": source["tree_sha256"],
            "source_tree_sha256_current": source["tree_sha256"],
        },
    )
    assert _validate_test_evidence(paths)["before_after_current_identical"] is True

    (paths.release / "README.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="source before/after/current"):
        _validate_test_evidence(paths)
