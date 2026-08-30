from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from sqlmend_generation_v1.migration import (
    BASELINE_SYSTEM_ID,
    CANONICAL_EXPERIMENT_ID,
    GENERATION_V1_SYSTEM_ID,
    MigrationError,
    MigrationPaths,
    immutable_projection,
    migrate_metadata,
)


CONFIG_SHA = "a" * 64
EVALUATION_INPUT_SHA256 = {"generation_v1_evidence_file": "1" * 64}
CONTEXT_SHA = hashlib.sha256(
    json.dumps(
        EVALUATION_INPUT_SHA256,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


def _run_row(query_id: str, system_id: str, *, answer: str) -> dict:
    return {
        "schema_version": "sqlmend-generation-record-v1",
        "experiment_id": "phase10_g0_g1_dev250",
        "query_id": query_id,
        "system_id": system_id,
        "status": "success",
        "answer": {"diagnosis": answer},
        "structured_output_valid": True,
        "contract_valid": True,
        "validation_errors": [],
        "failure": None,
        "input_provenance": {
            "serialized_query_sha256": "c" * 64,
            "evidence_sha256": None,
            "evidence_passage_ids": [],
            "prompt_sha256": "d" * 64,
        },
        "generation_provenance": {
            "run_id": "phase10_g0_g1_dev250",
            "model_tag": "qwen3.5:4b",
            "model_digest": "e" * 64,
            "ollama_version": "0.24.0",
            "think": False,
            "options": {"temperature": 0.0, "seed": 20260830},
            "retry_policy": {"max_attempts": 3, "retry_on": ["invalid_json"]},
            "output_schema_sha256": "f" * 64,
            "system_prompt_sha256": "1" * 64,
            "prompt_template_sha256": "2" * 64,
            "base_prompt_sha256": "3" * 64,
            "retry_feedback_template_sha256": "4" * 64,
            "base_request_sha256": "5" * 64,
            "request_sha256": "6" * 64,
            "raw_response_sha256": "7" * 64,
            "attempt_count": 1,
            "retry_count": 0,
            "attempts": [
                {
                    "attempt_number": 1,
                    "status": "success",
                    "request_sha256": "6" * 64,
                    "raw_response_sha256": "7" * 64,
                    "raw_content": '{"diagnosis":"ok"}',
                    "wall_ms": 12.5,
                }
            ],
        },
        "latency": {
            "wall_ms": 12.5,
            "ollama_total_ms": 11.5,
            "load_ms": 1.0,
            "prompt_eval_ms": 2.0,
            "eval_ms": 8.5,
            "prompt_tokens": 10,
            "completion_tokens": 4,
        },
    }


def _judgment(query_id: str) -> dict:
    decision = {
        "root_cause_correct": True,
        "sql_repair_correct": True,
        "dialect_compatible": True,
        "version_compatible": True,
        "answer_relevance": 1.0,
        "citation_coverage": 0.0,
        "faithfulness": 1.0,
        "reason": "preserve this judge payload",
    }
    return {
        "schema_version": "sqlmend-generation-judgment-v1",
        "query_id": query_id,
        "ordinal": 1,
        "assignment": {"A": "g0_closed_book", "B": "g1_retrieval_v1_rag"},
        "counterbalance": "odd:g0=A;even:g1=A",
        "decision": {
            "g0": copy.deepcopy(decision),
            "g1": copy.deepcopy(decision),
        },
        "run_sha256": {"g0": None, "g1": None},
        "evaluation_context_sha256": "0" * 64,
        "evaluation_input_sha256": {"g1_evidence_file": "1" * 64},
        "policy_source_config_sha256": "2" * 64,
        "prompt_sha256": "3" * 64,
        "model": "qwen3.5:4b",
        "model_digest": "4" * 64,
        "model_tag": "qwen3.5:4b",
        "think": False,
        "thinking_disabled": True,
        "attempt_count": 1,
        "retry_count": 0,
        "attempts": [{"attempt": 1, "status": "success", "response_sha256": "5" * 64}],
        "status": "success",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> bytes:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _fixture(tmp_path: Path) -> tuple[MigrationPaths, list[dict], list[dict], list[dict]]:
    paths = MigrationPaths(
        baseline_legacy=tmp_path / "generation/generation-v1/runs/g0.jsonl",
        generation_v1_legacy=tmp_path / "generation/generation-v1/runs/g1.jsonl",
        judgments_legacy=tmp_path / "generation/generation-v1/evaluation/judgments.jsonl",
        baseline=tmp_path / "generation/baseline/runs/baseline.jsonl",
        generation_v1=tmp_path / "generation/generation-v1/runs/generation_v1.jsonl",
        judgments=tmp_path / "generation/generation-v1/evaluation/judgments.jsonl",
        ledger=tmp_path / "generation/baseline/migration_ledger.json",
        repository_root=tmp_path,
    )
    baseline = [_run_row("DEV0001", "g0_closed_book", answer="baseline")]
    generation = [_run_row("DEV0001", "g1_retrieval_v1_rag", answer="rag")]
    judgments = [_judgment("DEV0001")]
    baseline_bytes = _write_jsonl(paths.baseline_legacy, baseline)
    generation_bytes = _write_jsonl(paths.generation_v1_legacy, generation)
    judgments[0]["run_sha256"] = {
        "g0": hashlib.sha256(baseline_bytes).hexdigest(),
        "g1": hashlib.sha256(generation_bytes).hexdigest(),
    }
    _write_jsonl(paths.judgments_legacy, judgments)
    return paths, baseline, generation, judgments


def test_migration_rebinds_only_metadata_and_is_idempotent(tmp_path: Path) -> None:
    paths, baseline, generation, judgments = _fixture(tmp_path)
    # Keep a separate legacy journal because the normal release path is also
    # the canonical evaluation destination.
    legacy_judgments = tmp_path / "legacy-judgments.jsonl"
    legacy_judgments.write_bytes(paths.judgments_legacy.read_bytes())
    paths.judgments.unlink()
    paths = MigrationPaths(
        paths.baseline_legacy,
        paths.generation_v1_legacy,
        legacy_judgments,
        paths.baseline,
        paths.generation_v1,
        paths.judgments,
        paths.ledger,
        paths.repository_root,
    )
    old_bytes = {
        "baseline": paths.baseline_legacy.read_bytes(),
        "generation_v1": paths.generation_v1_legacy.read_bytes(),
        "judgments": paths.judgments_legacy.read_bytes(),
    }

    ledger = migrate_metadata(
        paths,
        config_source_sha256=CONFIG_SHA,
        evaluation_context_sha256=CONTEXT_SHA,
        evaluation_input_sha256=EVALUATION_INPUT_SHA256,
        expected_query_count=1,
    )
    migrated_baseline = json.loads(paths.baseline.read_text().splitlines()[0])
    migrated_generation = json.loads(paths.generation_v1.read_text().splitlines()[0])
    migrated_judgment = json.loads(paths.judgments.read_text().splitlines()[0])

    assert migrated_baseline["system_id"] == BASELINE_SYSTEM_ID
    assert migrated_generation["system_id"] == GENERATION_V1_SYSTEM_ID
    assert migrated_baseline["experiment_id"] == CANONICAL_EXPERIMENT_ID
    assert migrated_generation["generation_provenance"]["run_id"] == CANONICAL_EXPERIMENT_ID
    assert migrated_baseline["answer"] == baseline[0]["answer"]
    assert migrated_generation["generation_provenance"]["attempts"] == generation[0]["generation_provenance"]["attempts"]
    assert migrated_judgment["assignment"] == {"A": "baseline", "B": "generation_v1"}
    assert set(migrated_judgment["decision"]) == {"baseline", "generation_v1"}
    assert migrated_judgment["decision"]["baseline"] == judgments[0]["decision"]["g0"]
    assert paths.judgments.read_bytes() == (
        json.dumps(migrated_judgment, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    assert migrated_judgment["attempts"] == judgments[0]["attempts"]
    assert migrated_judgment["prompt_sha256"] == judgments[0]["prompt_sha256"]
    assert migrated_judgment["policy_source_config_sha256"] == CONFIG_SHA
    assert migrated_judgment["evaluation_context_sha256"] == CONTEXT_SHA
    assert migrated_judgment["evaluation_input_sha256"] == EVALUATION_INPUT_SHA256
    assert migrated_judgment["run_sha256"] == {
        "baseline": hashlib.sha256(paths.baseline.read_bytes()).hexdigest(),
        "generation_v1": hashlib.sha256(paths.generation_v1.read_bytes()).hexdigest(),
    }
    assert ledger["run_sha256_binding"]["baseline"]["legacy"] == hashlib.sha256(
        paths.baseline_legacy.read_bytes()
    ).hexdigest()
    assert ledger["run_sha256_binding"]["baseline"]["canonical"] == migrated_judgment["run_sha256"]["baseline"]
    assert ledger["ollama_called"] is False
    assert all(entry["immutable_projection_equal"] for entry in ledger["artifacts"].values())
    assert all(paths_.read_bytes() == old_bytes[label] for label, paths_ in {
        "baseline": paths.baseline_legacy,
        "generation_v1": paths.generation_v1_legacy,
        "judgments": paths.judgments_legacy,
    }.items())

    output_bytes = {label: path.read_bytes() for label, path in {
        "baseline": paths.baseline,
        "generation_v1": paths.generation_v1,
        "judgments": paths.judgments,
    }.items()}
    assert migrate_metadata(
        paths,
        config_source_sha256=CONFIG_SHA,
        evaluation_context_sha256=CONTEXT_SHA,
        evaluation_input_sha256=EVALUATION_INPUT_SHA256,
        expected_query_count=1,
    ) == ledger
    assert {label: path.read_bytes() for label, path in {
        "baseline": paths.baseline,
        "generation_v1": paths.generation_v1,
        "judgments": paths.judgments,
    }.items()} == output_bytes
    assert not list(tmp_path.rglob("*.migration-*"))


def test_immutable_projection_covers_answer_failure_attempts_and_latency() -> None:
    old = _run_row("DEV0001", "g0_closed_book", answer="keep")
    new = copy.deepcopy(old)
    new["system_id"] = "baseline"
    new["experiment_id"] = CANONICAL_EXPERIMENT_ID
    new["generation_provenance"]["run_id"] = CANONICAL_EXPERIMENT_ID
    assert immutable_projection(old, kind="run") == immutable_projection(new, kind="run")
    new["answer"]["diagnosis"] = "tampered"
    assert immutable_projection(old, kind="run") != immutable_projection(new, kind="run")


def test_migration_rejects_tampered_legacy_source_or_canonical_output(tmp_path: Path) -> None:
    paths, _, _, _ = _fixture(tmp_path)
    legacy_judgments = tmp_path / "legacy-judgments.jsonl"
    legacy_judgments.write_bytes(paths.judgments_legacy.read_bytes())
    paths.judgments.unlink()
    paths = MigrationPaths(
        paths.baseline_legacy,
        paths.generation_v1_legacy,
        legacy_judgments,
        paths.baseline,
        paths.generation_v1,
        paths.judgments,
        paths.ledger,
        paths.repository_root,
    )
    migrate_metadata(
        paths,
        config_source_sha256=CONFIG_SHA,
        evaluation_context_sha256=CONTEXT_SHA,
        evaluation_input_sha256=EVALUATION_INPUT_SHA256,
        expected_query_count=1,
    )
    tampered = json.loads(paths.baseline_legacy.read_text().splitlines()[0])
    tampered["answer"]["diagnosis"] = "tampered"
    _write_jsonl(paths.baseline_legacy, [tampered])
    with pytest.raises(MigrationError, match="legacy source was modified"):
        migrate_metadata(
            paths,
            config_source_sha256=CONFIG_SHA,
            evaluation_context_sha256=CONTEXT_SHA,
            evaluation_input_sha256=EVALUATION_INPUT_SHA256,
            expected_query_count=1,
        )


def test_migration_rejects_tampered_canonical_output_on_resume(tmp_path: Path) -> None:
    paths, _, _, _ = _fixture(tmp_path)
    legacy_judgments = tmp_path / "legacy-judgments.jsonl"
    legacy_judgments.write_bytes(paths.judgments_legacy.read_bytes())
    paths.judgments.unlink()
    paths = MigrationPaths(
        paths.baseline_legacy,
        paths.generation_v1_legacy,
        legacy_judgments,
        paths.baseline,
        paths.generation_v1,
        paths.judgments,
        paths.ledger,
        paths.repository_root,
    )
    migrate_metadata(
        paths,
        config_source_sha256=CONFIG_SHA,
        evaluation_context_sha256=CONTEXT_SHA,
        evaluation_input_sha256=EVALUATION_INPUT_SHA256,
        expected_query_count=1,
    )
    paths.generation_v1.write_bytes(paths.generation_v1.read_bytes() + b" ")
    with pytest.raises(MigrationError, match="canonical output was modified"):
        migrate_metadata(
            paths,
            config_source_sha256=CONFIG_SHA,
            evaluation_context_sha256=CONTEXT_SHA,
            evaluation_input_sha256=EVALUATION_INPUT_SHA256,
            expected_query_count=1,
        )
