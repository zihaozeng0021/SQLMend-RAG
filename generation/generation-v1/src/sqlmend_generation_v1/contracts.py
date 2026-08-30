"""Strict online data, configuration, and answer contracts."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from .io import sha256_json, sha256_text


BASELINE_SYSTEM_ID = "baseline"
GENERATION_V1_SYSTEM_ID = "generation_v1"
GENERATION_SYSTEM_IDS = (BASELINE_SYSTEM_ID, GENERATION_V1_SYSTEM_ID)
QUERY_SCHEMA_VERSION = "sqlmend-online-query-v1"
# Historical serialization identifier retained so prepared-evidence and
# formal prompt/input hashes remain byte-bound across the naming migration.
EVIDENCE_SCHEMA_VERSION = "sqlmend-g1-evidence-v1"
GENERATION_RECORD_SCHEMA_VERSION = "sqlmend-generation-record-v1"
SERIALIZER_VERSION = "sqlmend-query-v1"
FINAL_RETRIEVAL_SYSTEM_ID = "hybrid_rrf_dialect_version_lexical_rerank_v1"

ALLOWED_QUERY_SOURCE_FIELDS = frozenset(
    {
        "dialect",
        "version",
        "user_problem",
        "sql",
        "error_message",
        "error_code",
        "sqlstate",
        "error_symbol",
    }
)
ANSWER_FIELDS = frozenset(
    {
        "diagnosis",
        "root_cause",
        "corrected_sql",
        "explanation",
        "dialect_compatibility",
        "version_compatibility",
        "confidence",
        "insufficient_evidence",
        "citations",
    }
)
COMPATIBILITY_FIELDS = frozenset({"status", "explanation"})
COMPATIBILITY_STATUSES = frozenset({"compatible", "incompatible", "unknown"})


def _strict_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(f"{label} keys differ; missing={missing}, extra={extra}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    temperature: float
    seed: int
    num_ctx: int
    num_predict: int
    top_k: int
    top_p: float
    repeat_penalty: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GenerationOptions":
        expected = frozenset(
            {"temperature", "seed", "num_ctx", "num_predict", "top_k", "top_p", "repeat_penalty"}
        )
        _strict_keys(value, expected, "ollama.options")
        result = cls(
            temperature=float(value["temperature"]),
            seed=int(value["seed"]),
            num_ctx=int(value["num_ctx"]),
            num_predict=int(value["num_predict"]),
            top_k=int(value["top_k"]),
            top_p=float(value["top_p"]),
            repeat_penalty=float(value["repeat_penalty"]),
        )
        if result.temperature != 0.0:
            raise ValueError("temperature must remain frozen at 0.0")
        if result.seed != 20260830:
            raise ValueError("seed differs from the frozen Phase 10 seed")
        if (
            result.num_ctx != 16384
            or result.num_predict != 1024
            or result.top_k != 40
            or result.top_p != 0.9
            or result.repeat_penalty != 1.0
        ):
            raise ValueError("decoding options differ from the frozen Phase 10 settings")
        if min(result.num_ctx, result.num_predict, result.top_k) <= 0:
            raise ValueError("integer generation options must be positive")
        if not 0.0 <= result.top_p <= 1.0 or result.repeat_penalty <= 0.0:
            raise ValueError("invalid top_p or repeat_penalty")
        return result

    def to_dict(self) -> dict[str, int | float]:
        return {
            "temperature": self.temperature,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
        }


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    retry_on: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"max_attempts": self.max_attempts, "retry_on": list(self.retry_on)}


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    experiment_id: str
    expected_query_count: int
    retrieval_system_id: str
    top_k: int
    top_k_rationale: str
    base_url: str
    model_tag: str
    model_digest: str
    think: bool | str
    timeout_seconds: float
    options: GenerationOptions
    retry_policy: RetryPolicy

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GenerationConfig":
        if value.get("schema_version") != "sqlmend-generation-config-v1":
            raise ValueError("Unexpected generation config schema_version")
        systems = value.get("systems")
        if not isinstance(systems, Mapping) or set(systems) != set(GENERATION_SYSTEM_IDS):
            raise ValueError("Config must contain exactly baseline and generation_v1 systems")
        if systems[BASELINE_SYSTEM_ID].get("evidence_mode") != "none":
            raise ValueError("baseline evidence mode must be none")
        if systems[GENERATION_V1_SYSTEM_ID].get("evidence_mode") != "retrieval_v1_final_top5":
            raise ValueError("generation_v1 evidence mode must be the frozen Retrieval-v1 Final Top-5")
        retrieval = value.get("retrieval")
        ollama = value.get("ollama")
        retry = value.get("retry_policy")
        if not all(isinstance(item, Mapping) for item in (retrieval, ollama, retry)):
            raise ValueError("retrieval, ollama, and retry_policy must be mappings")
        options_value = ollama.get("options")
        if not isinstance(options_value, Mapping):
            raise ValueError("ollama.options must be a mapping")
        retry_on = retry.get("retry_on")
        if not isinstance(retry_on, list) or not all(isinstance(item, str) for item in retry_on):
            raise ValueError("retry_policy.retry_on must be a string list")
        model_digest = _nonempty_string(ollama.get("model_digest"), "ollama.model_digest")
        if not re.fullmatch(r"[0-9a-f]{64}", model_digest):
            raise ValueError("ollama.model_digest must be a lowercase SHA-256 digest")
        think = ollama.get("think")
        if not isinstance(think, (bool, str)) or isinstance(think, str) and not think:
            raise ValueError("ollama.think must be a boolean or non-empty effort string")
        result = cls(
            experiment_id=_nonempty_string(value.get("experiment_id"), "experiment_id"),
            expected_query_count=int(value.get("expected_query_count")),
            retrieval_system_id=_nonempty_string(retrieval.get("system_id"), "retrieval.system_id"),
            top_k=int(retrieval.get("top_k")),
            top_k_rationale=_nonempty_string(retrieval.get("rationale"), "retrieval.rationale"),
            base_url=_nonempty_string(ollama.get("base_url"), "ollama.base_url").rstrip("/"),
            model_tag=_nonempty_string(ollama.get("model_tag"), "ollama.model_tag"),
            model_digest=model_digest,
            think=think,
            timeout_seconds=float(ollama.get("timeout_seconds")),
            options=GenerationOptions.from_mapping(options_value),
            retry_policy=RetryPolicy(int(retry.get("max_attempts")), tuple(retry_on)),
        )
        if result.expected_query_count <= 0:
            raise ValueError("expected_query_count must be positive")
        if result.retrieval_system_id != FINAL_RETRIEVAL_SYSTEM_ID or result.top_k != 5:
            raise ValueError("Retrieval-v1 Final Top-K must remain fixed at 5")
        if result.timeout_seconds != 300.0:
            raise ValueError("Phase 10 request timeout must remain 300 seconds")
        if result.retry_policy.max_attempts != 3:
            raise ValueError("Phase 10 retry policy must remain three attempts")
        return result


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    query_id: str
    source_fields_used: tuple[str, ...]
    serialized_text: str
    serialized_text_sha256: str
    serializer_version: str = SERIALIZER_VERSION

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "PreparedQuery":
        expected = frozenset(
            {
                "schema_version",
                "query_id",
                "source_fields_used",
                "serialized_text",
                "serialized_text_sha256",
                "serializer_version",
            }
        )
        _strict_keys(value, expected, "prepared query")
        if value.get("schema_version") != QUERY_SCHEMA_VERSION:
            raise ValueError("Unexpected prepared query schema_version")
        fields = value.get("source_fields_used")
        if not isinstance(fields, list) or not fields or not all(isinstance(item, str) for item in fields):
            raise ValueError("source_fields_used must be a non-empty string list")
        if len(fields) != len(set(fields)) or not set(fields).issubset(ALLOWED_QUERY_SOURCE_FIELDS):
            raise ValueError("source_fields_used contains a duplicate or forbidden field")
        serialized_text = _nonempty_string(value.get("serialized_text"), "serialized_text")
        digest = _nonempty_string(value.get("serialized_text_sha256"), "serialized_text_sha256")
        if digest != sha256_text(serialized_text):
            raise ValueError("serialized_text_sha256 mismatch")
        if value.get("serializer_version") != SERIALIZER_VERSION:
            raise ValueError("Unexpected query serializer version")
        return cls(
            query_id=_nonempty_string(value.get("query_id"), "query_id"),
            source_fields_used=tuple(fields),
            serialized_text=serialized_text,
            serialized_text_sha256=digest,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": QUERY_SCHEMA_VERSION,
            "query_id": self.query_id,
            "source_fields_used": list(self.source_fields_used),
            "serialized_text": self.serialized_text,
            "serialized_text_sha256": self.serialized_text_sha256,
            "serializer_version": self.serializer_version,
        }


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    query_id: str
    retrieval_system_id: str
    top_k: int
    run_sha256: str
    passages: tuple[dict[str, Any], ...]
    evidence_sha256: str

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "EvidenceRow":
        expected = frozenset(
            {
                "schema_version",
                "query_id",
                "retrieval_system_id",
                "top_k",
                "run_sha256",
                "passages",
                "evidence_sha256",
            }
        )
        _strict_keys(value, expected, "evidence row")
        if value.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Unexpected evidence schema_version")
        if value.get("retrieval_system_id") != FINAL_RETRIEVAL_SYSTEM_ID or value.get("top_k") != 5:
            raise ValueError("Evidence is not frozen Retrieval-v1 Final Top-5")
        passages = value.get("passages")
        if not isinstance(passages, list) or len(passages) != 5:
            raise ValueError("Every generation_v1 evidence row must contain exactly five passages")
        passage_fields = frozenset(
            {
                "passage_id",
                "rank",
                "score",
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
            }
        )
        ids: list[str] = []
        normalized: list[dict[str, Any]] = []
        for expected_rank, passage in enumerate(passages, start=1):
            if not isinstance(passage, Mapping):
                raise ValueError("Evidence passage must be an object")
            _strict_keys(passage, passage_fields, "evidence passage")
            passage_id = _nonempty_string(passage.get("passage_id"), "passage_id")
            if passage.get("rank") != expected_rank:
                raise ValueError("Evidence passage ranks must be continuous from one")
            score = passage.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
                raise ValueError("Evidence score must be finite")
            _nonempty_string(passage.get("text"), "evidence text")
            for field in passage_fields - {"passage_id", "rank", "score", "text"}:
                field_value = passage.get(field)
                if field_value is not None and not isinstance(field_value, str):
                    raise ValueError(f"Evidence {field} must be a string or null")
            ids.append(passage_id)
            normalized.append(dict(passage))
        if len(ids) != len(set(ids)):
            raise ValueError("Evidence passages must be unique")
        without_hash = {key: value[key] for key in value if key != "evidence_sha256"}
        digest = _nonempty_string(value.get("evidence_sha256"), "evidence_sha256")
        if digest != sha256_json(without_hash):
            raise ValueError("evidence_sha256 mismatch")
        return cls(
            query_id=_nonempty_string(value.get("query_id"), "query_id"),
            retrieval_system_id=FINAL_RETRIEVAL_SYSTEM_ID,
            top_k=5,
            run_sha256=_nonempty_string(value.get("run_sha256"), "run_sha256"),
            passages=tuple(normalized),
            evidence_sha256=digest,
        )

    @property
    def passage_ids(self) -> tuple[str, ...]:
        return tuple(str(passage["passage_id"]) for passage in self.passages)


def validate_answer_shape(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["answer must be a JSON object"]
    observed = set(value)
    if observed != ANSWER_FIELDS:
        missing = sorted(ANSWER_FIELDS - observed)
        extra = sorted(observed - ANSWER_FIELDS)
        errors.append(f"answer keys differ; missing={missing}, extra={extra}")
        return errors
    for field in ("diagnosis", "root_cause", "explanation"):
        if not isinstance(value[field], str) or not value[field].strip():
            errors.append(f"{field} must be a non-empty string")
    corrected_sql = value["corrected_sql"]
    if corrected_sql is not None and not isinstance(corrected_sql, str):
        errors.append("corrected_sql must be a string or null")
    for field in ("dialect_compatibility", "version_compatibility"):
        compatibility = value[field]
        if not isinstance(compatibility, dict) or set(compatibility) != COMPATIBILITY_FIELDS:
            errors.append(f"{field} must contain exactly status and explanation")
            continue
        if compatibility["status"] not in COMPATIBILITY_STATUSES:
            errors.append(f"{field}.status is invalid")
        if not isinstance(compatibility["explanation"], str) or not compatibility["explanation"].strip():
            errors.append(f"{field}.explanation must be a non-empty string")
    confidence = value["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        errors.append("confidence must be a finite number in [0, 1]")
    if not isinstance(value["insufficient_evidence"], bool):
        errors.append("insufficient_evidence must be boolean")
    citations = value["citations"]
    if not isinstance(citations, list) or not all(isinstance(item, str) and item for item in citations):
        errors.append("citations must be a string array")
    elif len(citations) != len(set(citations)):
        errors.append("citations must not contain duplicates")
    return errors


def validate_answer_contract(
    value: Any,
    *,
    system_id: str,
    allowed_citation_ids: tuple[str, ...],
) -> list[str]:
    errors = validate_answer_shape(value)
    if errors or not isinstance(value, dict):
        return errors
    citations = value["citations"]
    allowed = set(allowed_citation_ids)
    if system_id == BASELINE_SYSTEM_ID:
        if allowed:
            errors.append("baseline allowed citation IDs must be empty")
        if citations:
            errors.append("baseline citations must be empty")
    elif system_id == GENERATION_V1_SYSTEM_ID:
        fabricated = sorted(set(citations) - allowed)
        if fabricated:
            errors.append(f"generation_v1 citations are outside provided evidence: {fabricated}")
    else:
        errors.append(f"unknown generation system: {system_id}")
    return errors
