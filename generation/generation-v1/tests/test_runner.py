from __future__ import annotations

import json
from pathlib import Path

import yaml

from sqlmend_generation_v1.contracts import (
    EVIDENCE_SCHEMA_VERSION,
    FINAL_RETRIEVAL_SYSTEM_ID,
    BASELINE_SYSTEM_ID,
    GENERATION_V1_SYSTEM_ID,
    GenerationConfig,
    PreparedQuery,
)
from sqlmend_generation_v1.io import (
    load_json,
    load_jsonl,
    sha256_json,
    sha256_text,
    write_jsonl,
)
from sqlmend_generation_v1.ollama import (
    OllamaIdentity,
    OllamaResponse,
    build_chat_payload,
)
from sqlmend_generation_v1.paths import ProjectPaths
from sqlmend_generation_v1.runner import generate_system


MODULE_ROOT = Path(__file__).resolve().parents[1]
MODEL_TAG = "qwen3.5:4b"
MODEL_DIGEST = "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd"


def _answer(citations: list[str]) -> dict:
    return {
        "diagnosis": "A keyword is misspelled.",
        "root_cause": "SELEC is not SELECT.",
        "corrected_sql": "SELECT 1;",
        "explanation": "Spell SELECT correctly.",
        "dialect_compatibility": {"status": "compatible", "explanation": "SQLite syntax."},
        "version_compatibility": {"status": "compatible", "explanation": "Works in 3.45.3."},
        "confidence": 0.99,
        "insufficient_evidence": False,
        "citations": citations,
    }


class FakeClient:
    def __init__(self, outputs: list[dict]):
        self.outputs = [json.dumps(output, separators=(",", ":")) for output in outputs]
        self.calls = 0
        self.payloads: list[dict] = []

    def preflight(self, expected_tag: str, expected_digest: str) -> OllamaIdentity:
        assert expected_tag == MODEL_TAG
        assert expected_digest == MODEL_DIGEST
        return OllamaIdentity(expected_tag, expected_digest, "0.test")

    def chat(self, *, messages, output_schema, model_tag, think, options) -> OllamaResponse:
        content = self.outputs[self.calls]
        self.calls += 1
        payload = build_chat_payload(
            model_tag=model_tag,
            messages=messages,
            output_schema=output_schema,
            think=think,
            options=options,
        )
        self.payloads.append(payload)
        return OllamaResponse(
            content=content,
            raw_response_sha256=sha256_text(content),
            request_sha256=sha256_json(payload),
            wall_ms=1.0,
            ollama_total_ms=0.9,
            load_ms=0.1,
            prompt_eval_ms=0.2,
            eval_ms=0.6,
            prompt_tokens=20,
            completion_tokens=10,
        )


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(tmp_path)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((MODULE_ROOT / "config" / "generation.yaml").read_text(encoding="utf-8"))
    config["expected_query_count"] = 1
    paths.config_file.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n"
    )
    paths.answer_schema.parent.mkdir(parents=True, exist_ok=True)
    paths.answer_schema.write_bytes((MODULE_ROOT / "schema" / "answer.schema.json").read_bytes())
    query_text = "Dialect: sqlite\n\nVersion: 3.45.3\n\nQuestion:\nWhy fail?\n\nSQL:\nSELEC 1;"
    query = PreparedQuery(
        query_id="DEVTEST",
        source_fields_used=("dialect", "version", "user_problem", "sql"),
        serialized_text=query_text,
        serialized_text_sha256=sha256_text(query_text),
    )
    write_jsonl(paths.prepared_queries, (query.to_record(),))
    return paths


def _write_evidence(paths: ProjectPaths) -> None:
    passage = {
        "passage_id": "p1",
        "rank": 1,
        "score": 1.0,
        "dialect": "sqlite",
        "version": "3.45.3",
        "version_min": "3.45.3",
        "version_max": "3.45.3",
        "version_status": "exact",
        "source_type": "official_docs",
        "source_name": "SQLite docs",
        "source_url": "https://sqlite.org/lang_select.html",
        "title": "SELECT",
        "section": "SELECT",
        "text": "SELECT is the query keyword.",
        "content_hash": "a" * 64,
    }
    passages = []
    for rank in range(1, 6):
        item = dict(passage)
        item["passage_id"] = f"p{rank}"
        item["rank"] = rank
        passages.append(item)
    record = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "query_id": "DEVTEST",
        "retrieval_system_id": FINAL_RETRIEVAL_SYSTEM_ID,
        "top_k": 5,
        "run_sha256": "b" * 64,
        "passages": passages,
    }
    record["evidence_sha256"] = sha256_json(record)
    write_jsonl(paths.generation_v1_evidence, (record,))


def test_baseline_never_loads_or_opens_generation_v1_evidence(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path)

    def forbidden_loader(_path):
        raise AssertionError("baseline attempted to load generation-v1 evidence")

    monkeypatch.setattr(
        "sqlmend_generation_v1.runner.load_generation_v1_evidence", forbidden_loader
    )
    client = FakeClient([_answer([])])
    summary = generate_system(paths, BASELINE_SYSTEM_ID, client=client, resume=False)
    record = load_jsonl(paths.baseline_run)[0]

    assert summary["complete"] is True
    assert client.calls == 1
    assert record["status"] == "success"
    assert record["answer"]["citations"] == []
    assert record["input_provenance"]["evidence_sha256"] is None
    assert record["input_provenance"]["evidence_passage_ids"] == []
    assert record["generation_provenance"]["think"] is False
    assert record["generation_provenance"]["options"]["seed"] == 20260830
    assert client.payloads[0]["model"] == MODEL_TAG
    assert client.payloads[0]["think"] is False


def test_generation_v1_retries_fabricated_citation_and_records_every_attempt(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_evidence(paths)
    client = FakeClient([_answer(["invented"]), _answer(["p1"])])

    summary = generate_system(paths, GENERATION_V1_SYSTEM_ID, client=client, resume=False)
    record = load_jsonl(paths.generation_v1_run)[0]

    assert summary["success_count"] == 1
    assert summary["success_count_semantics"] == "generation_contract_success"
    assert summary["generation_contract_success_count"] == 1
    assert summary["generation_contract_failure_count"] == 0
    assert client.calls == 2
    assert record["answer"]["citations"] == ["p1"]
    assert record["structured_output_valid"] is True
    assert record["contract_valid"] is True
    provenance = record["generation_provenance"]
    assert provenance["attempt_count"] == 2
    assert provenance["retry_count"] == 1
    assert provenance["attempts"][0]["error_type"] == "citation_contract_violation"
    assert provenance["attempts"][1]["status"] == "success"
    assert len(client.payloads[0]["messages"]) == 2
    assert len(client.payloads[1]["messages"]) == 3
    assert "citation_contract_violation" in client.payloads[1]["messages"][-1]["content"]
    assert provenance["base_request_sha256"] == provenance["attempts"][0]["request_sha256"]
    assert provenance["request_sha256"] == provenance["attempts"][1]["request_sha256"]
    assert provenance["base_prompt_sha256"] == record["input_provenance"]["prompt_sha256"]
    assert provenance["attempts"][0]["messages_sha256"] != provenance["attempts"][1]["messages_sha256"]
    assert record["input_provenance"]["evidence_passage_ids"] == [
        "p1",
        "p2",
        "p3",
        "p4",
        "p5",
    ]
    assert record["latency"]["prompt_tokens"] == 40
    assert record["latency"]["completion_tokens"] == 20


def test_resume_preserves_existing_formal_record_without_regeneration(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    first_client = FakeClient([_answer([])])
    generate_system(paths, BASELINE_SYSTEM_ID, client=first_client, resume=False)
    before = paths.baseline_run.read_bytes()

    second_client = FakeClient([])
    summary = generate_system(paths, BASELINE_SYSTEM_ID, client=second_client, resume=True)

    assert second_client.calls == 0
    assert summary["generated_this_invocation"] == 0
    assert summary["resumed_record_count"] == 1
    assert paths.baseline_run.read_bytes() == before


def test_model_identity_and_boolean_think_are_loaded_from_config() -> None:
    mapping = yaml.safe_load((MODULE_ROOT / "config" / "generation.yaml").read_text(encoding="utf-8"))
    mapping["ollama"]["model_tag"] = "local-test-model:latest"
    mapping["ollama"]["model_digest"] = "c" * 64
    mapping["ollama"]["think"] = False

    config = GenerationConfig.from_mapping(mapping)

    assert config.model_tag == "local-test-model:latest"
    assert config.model_digest == "c" * 64
    assert config.think is False
