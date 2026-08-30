"""Resumable paired-system runner with strict retries and provenance."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping, Protocol

from .contracts import (
    GENERATION_RECORD_SCHEMA_VERSION,
    GENERATION_SYSTEM_IDS,
    BASELINE_SYSTEM_ID,
    GENERATION_V1_SYSTEM_ID,
    GenerationConfig,
    validate_answer_contract,
    validate_answer_shape,
)
from .inputs import (
    load_generation_v1_evidence,
    load_generation_config,
    load_prepared_queries,
)
from .io import (
    append_jsonl,
    load_json,
    load_jsonl,
    sha256_file,
    sha256_json,
    sha256_text,
    write_jsonl,
)
from .ollama import (
    OllamaClient,
    OllamaIdentity,
    OllamaResponse,
    build_chat_payload,
)
from .paths import ProjectPaths
from .prompting import RETRY_FEEDBACK_TEMPLATE_SHA256, build_prompt, retry_messages


class GenerationClient(Protocol):
    def preflight(self, expected_tag: str, expected_digest: str) -> OllamaIdentity: ...

    def chat(
        self,
        *,
        messages: tuple[dict[str, str], ...],
        output_schema: Mapping[str, Any],
        model_tag: str,
        think: bool | str,
        options: Mapping[str, int | float],
    ) -> OllamaResponse: ...


def _validate_existing_records(
    path: Path,
    *,
    system_id: str,
    query_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = load_jsonl(path)
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("schema_version") != GENERATION_RECORD_SCHEMA_VERSION:
            raise ValueError(f"Existing run has an invalid schema_version: {path}")
        if record.get("system_id") != system_id:
            raise ValueError(f"Existing run mixes generation systems: {path}")
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or query_id not in query_ids or query_id in by_id:
            raise ValueError(f"Existing run has an unknown or duplicate query_id: {query_id!r}")
        if record.get("status") not in {"success", "failed"}:
            raise ValueError(f"Existing run has an invalid status for {query_id}")
        by_id[query_id] = record
    return by_id


def _attempt_metrics(response: OllamaResponse | None) -> dict[str, int | float]:
    if response is None:
        return {
            "ollama_total_ms": 0.0,
            "load_ms": 0.0,
            "prompt_eval_ms": 0.0,
            "eval_ms": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    return {
        "ollama_total_ms": response.ollama_total_ms,
        "load_ms": response.load_ms,
        "prompt_eval_ms": response.prompt_eval_ms,
        "eval_ms": response.eval_ms,
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
    }


def _sum_attempt_metric(attempts: list[dict[str, Any]], field: str) -> int | float:
    values = [attempt[field] for attempt in attempts]
    if field in {"prompt_tokens", "completion_tokens"}:
        return sum(int(value) for value in values)
    return sum(float(value) for value in values)


def _generate_one(
    *,
    query: Any,
    evidence_passages: tuple[dict[str, Any], ...],
    evidence_sha256: str | None,
    system_id: str,
    config: GenerationConfig,
    output_schema: Mapping[str, Any],
    output_schema_sha256: str,
    identity: OllamaIdentity,
    client: GenerationClient,
    run_id: str,
) -> dict[str, Any]:
    prompt = build_prompt(query, evidence_passages)
    allowed_ids = tuple(str(passage["passage_id"]) for passage in evidence_passages)
    options = config.options.to_dict()
    base_request_payload = build_chat_payload(
        model_tag=config.model_tag,
        messages=prompt.messages,
        output_schema=output_schema,
        think=config.think,
        options=options,
    )
    base_request_sha256 = sha256_json(base_request_payload)
    attempts: list[dict[str, Any]] = []
    final_answer: dict[str, Any] | None = None
    final_shape_valid = False
    final_contract_valid = False
    final_validation_errors: list[str] = []
    last_failure_type: str | None = None
    last_failure_message: str | None = None
    last_raw_response_sha256: str | None = None
    final_request_sha256 = base_request_sha256
    previous_error_type: str | None = None
    started_all = time.perf_counter()

    for attempt_number in range(1, config.retry_policy.max_attempts + 1):
        attempt_messages = retry_messages(
            prompt.messages,
            previous_error_type,
            attempt_number,
        )
        attempt_payload = build_chat_payload(
            model_tag=config.model_tag,
            messages=attempt_messages,
            output_schema=output_schema,
            think=config.think,
            options=options,
        )
        attempt_messages_sha256 = sha256_json(list(attempt_messages))
        attempt_request_sha256 = sha256_json(attempt_payload)
        final_request_sha256 = attempt_request_sha256
        response: OllamaResponse | None = None
        attempt_started = time.perf_counter()
        raw_content: str | None = None
        parsed: Any = None
        shape_errors: list[str] = []
        contract_errors: list[str] = []
        error_type: str | None = None
        error_message: str | None = None
        try:
            response = client.chat(
                messages=attempt_messages,
                output_schema=output_schema,
                model_tag=config.model_tag,
                think=config.think,
                options=options,
            )
            if response.request_sha256 != attempt_request_sha256:
                raise ValueError("Ollama client request hash differs from the frozen request")
            last_raw_response_sha256 = response.raw_response_sha256
            raw_content = response.content
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                error_type = "invalid_json"
                error_message = f"Model output is not JSON: {exc.msg}"
                shape_errors = [error_message]
                contract_errors = list(shape_errors)
            else:
                shape_errors = validate_answer_shape(parsed)
                contract_errors = validate_answer_contract(
                    parsed,
                    system_id=system_id,
                    allowed_citation_ids=allowed_ids,
                )
                if shape_errors:
                    error_type = "output_schema_violation"
                    error_message = "; ".join(shape_errors)
                elif contract_errors:
                    error_type = "citation_contract_violation"
                    error_message = "; ".join(contract_errors)
                else:
                    final_answer = dict(parsed)
                    final_shape_valid = True
                    final_contract_valid = True
                    final_validation_errors = []
        except Exception as exc:  # Per-query errors must become formal failure records.
            error_type = "transport_or_protocol_error"
            error_message = f"{type(exc).__name__}: {exc}"
            shape_errors = [error_message]
            contract_errors = list(shape_errors)

        attempt_wall_ms = (time.perf_counter() - attempt_started) * 1000.0
        attempt_record: dict[str, Any] = {
            "attempt_number": attempt_number,
            "status": "success" if final_contract_valid else "failed",
            "error_type": error_type,
            "error_message": error_message,
            "wall_ms": attempt_wall_ms,
            "messages_sha256": attempt_messages_sha256,
            "request_sha256": attempt_request_sha256,
            "raw_response_sha256": (
                response.raw_response_sha256 if response is not None else None
            ),
            "raw_content": raw_content,
            "structured_output_valid": not shape_errors,
            "contract_valid": not contract_errors,
            "validation_errors": contract_errors,
            **_attempt_metrics(response),
        }
        attempts.append(attempt_record)
        if final_contract_valid:
            break
        final_shape_valid = not shape_errors
        final_contract_valid = False
        final_validation_errors = contract_errors
        last_failure_type = error_type
        last_failure_message = error_message
        previous_error_type = error_type

    wall_ms = (time.perf_counter() - started_all) * 1000.0
    success = final_answer is not None and final_contract_valid
    failure = None
    if not success:
        failure = {
            "type": last_failure_type or "unknown_generation_failure",
            "message": last_failure_message or "Generation failed without an error message",
            "retry_exhausted": len(attempts) == config.retry_policy.max_attempts,
        }
    return {
        "schema_version": GENERATION_RECORD_SCHEMA_VERSION,
        "experiment_id": config.experiment_id,
        "query_id": query.query_id,
        "system_id": system_id,
        "status": "success" if success else "failed",
        "answer": final_answer if success else None,
        "structured_output_valid": final_shape_valid,
        "contract_valid": final_contract_valid,
        "validation_errors": final_validation_errors,
        "failure": failure,
        "input_provenance": {
            "serialized_query_sha256": query.serialized_text_sha256,
            "evidence_sha256": evidence_sha256,
            "evidence_passage_ids": list(allowed_ids),
            "prompt_sha256": prompt.rendered_prompt_sha256,
        },
        "generation_provenance": {
            "run_id": run_id,
            "model_tag": identity.model_tag,
            "model_digest": identity.model_digest,
            "ollama_version": identity.ollama_version,
            "think": config.think,
            "options": options,
            "retry_policy": config.retry_policy.to_dict(),
            "output_schema_sha256": output_schema_sha256,
            "system_prompt_sha256": prompt.system_prompt_sha256,
            "prompt_template_sha256": prompt.prompt_template_sha256,
            "base_prompt_sha256": prompt.rendered_prompt_sha256,
            "retry_feedback_template_sha256": RETRY_FEEDBACK_TEMPLATE_SHA256,
            "base_request_sha256": base_request_sha256,
            "request_sha256": final_request_sha256,
            "raw_response_sha256": last_raw_response_sha256,
            "attempt_count": len(attempts),
            "retry_count": max(0, len(attempts) - 1),
            "attempts": attempts,
        },
        "latency": {
            "wall_ms": wall_ms,
            "ollama_total_ms": _sum_attempt_metric(attempts, "ollama_total_ms"),
            "load_ms": _sum_attempt_metric(attempts, "load_ms"),
            "prompt_eval_ms": _sum_attempt_metric(attempts, "prompt_eval_ms"),
            "eval_ms": _sum_attempt_metric(attempts, "eval_ms"),
            "prompt_tokens": _sum_attempt_metric(attempts, "prompt_tokens"),
            "completion_tokens": _sum_attempt_metric(attempts, "completion_tokens"),
        },
    }


def generate_system(
    paths: ProjectPaths,
    system_id: str,
    client: GenerationClient | None = None,
    resume: bool = True,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Generate every query for one formal system, retaining all failures.

    The baseline branch deliberately never loads, stats, hashes, or opens the
    generation-v1 evidence
    evidence artifact.  ``prepare_inputs`` is therefore an explicit earlier
    step instead of an implicit call from this function.
    """

    if system_id not in GENERATION_SYSTEM_IDS:
        raise ValueError(f"Unknown generation system: {system_id!r}")
    config = load_generation_config(paths.config_file)
    queries = load_prepared_queries(paths.prepared_queries)
    if len(queries) != config.expected_query_count:
        raise ValueError(
            f"Prepared query count is {len(queries)}, expected {config.expected_query_count}"
        )
    query_ids = {query.query_id for query in queries}

    # This conditional is the enforced file-I/O boundary between baseline and
    # generation-v1.
    evidence_by_query = None
    if system_id == GENERATION_V1_SYSTEM_ID:
        evidence_by_query = load_generation_v1_evidence(paths.generation_v1_evidence)
        if set(evidence_by_query) != query_ids:
            raise ValueError(
                "generation-v1 evidence query universe differs from prepared queries"
            )

    output_schema = load_json(paths.answer_schema)
    output_schema_sha256 = sha256_file(paths.answer_schema)
    active_client: GenerationClient = client or OllamaClient(
        config.base_url, timeout_seconds=config.timeout_seconds
    )
    identity = active_client.preflight(config.model_tag, config.model_digest)
    if identity.model_tag != config.model_tag or identity.model_digest != config.model_digest:
        raise ValueError("Generation client preflight returned an unexpected model identity")

    output_path = paths.result_path(system_id)
    if resume:
        existing = _validate_existing_records(
            output_path,
            system_id=system_id,
            query_ids=query_ids,
        )
    else:
        write_jsonl(output_path, ())
        existing = {}

    generated = 0
    failures = sum(record.get("status") == "failed" for record in existing.values())
    successes = sum(record.get("status") == "success" for record in existing.values())
    effective_run_id = run_id or config.experiment_id
    for query in queries:
        if query.query_id in existing:
            continue
        if evidence_by_query is None:
            passages: tuple[dict[str, Any], ...] = ()
            evidence_sha256 = None
        else:
            evidence = evidence_by_query[query.query_id]
            passages = evidence.passages
            evidence_sha256 = evidence.evidence_sha256
        record = _generate_one(
            query=query,
            evidence_passages=passages,
            evidence_sha256=evidence_sha256,
            system_id=system_id,
            config=config,
            output_schema=output_schema,
            output_schema_sha256=output_schema_sha256,
            identity=identity,
            client=active_client,
            run_id=effective_run_id,
        )
        append_jsonl(output_path, record)
        generated += 1
        if record["status"] == "success":
            successes += 1
        else:
            failures += 1

    final_records = _validate_existing_records(
        output_path,
        system_id=system_id,
        query_ids=query_ids,
    )
    if len(final_records) != len(queries):
        raise ValueError("Generation stopped before every query received a formal record")
    return {
        "schema_version": "sqlmend-generation-run-summary-v1",
        "experiment_id": config.experiment_id,
        "run_id": effective_run_id,
        "system_id": system_id,
        "result_path": str(output_path),
        "result_sha256": sha256_file(output_path),
        "query_count": len(queries),
        "generated_this_invocation": generated,
        "resumed_record_count": len(existing),
        "success_count_semantics": "generation_contract_success",
        "success_count": successes,
        "failure_count": failures,
        "generation_contract_success_count": successes,
        "generation_contract_failure_count": failures,
        "complete": len(final_records) == len(queries),
    }
