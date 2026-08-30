"""Leakage-safe offline evaluation for Phase 10 generation runs.

The public entry point, :func:`run_offline_evaluation`, deliberately seals both
250-row formal generation runs before it first opens either the development
references or effective qrels.  Reference-bearing helpers live only in this
offline module; online preparation/generation code must not import it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib import request

import yaml

from .metrics import (
    aggregate_system_metrics,
    citation_ids,
    citation_validity,
    context_retrieval_metrics,
    paired_summary,
    task_success,
)


EVALUATION_LABEL = "machine-proposed development evaluation"
EXPECTED_QUERY_COUNT = 250
G0_SYSTEM_ID = "g0_closed_book"
G1_SYSTEM_ID = "g1_retrieval_v1_rag"
JUDGE_MODEL = "qwen3.5:4b"
JUDGE_MODEL_DIGEST = "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd"

RUN_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "experiment_id",
        "query_id",
        "system_id",
        "status",
        "answer",
        "structured_output_valid",
        "contract_valid",
        "validation_errors",
        "failure",
        "input_provenance",
        "generation_provenance",
        "latency",
    }
)
REFERENCE_FIELDS = (
    "root_cause",
    "reference_fix_sql",
    "reference_explanation",
    "expected_behavior",
    "dialect",
    "version",
    "schema_context",
    "verification",
)


JUDGE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["A", "B"],
    "properties": {
        label: {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "root_cause_correct",
                "sql_repair_correct",
                "dialect_compatible",
                "version_compatible",
                "answer_relevance",
                "faithfulness",
                "citation_coverage",
                "reason",
            ],
            "properties": {
                "root_cause_correct": {"type": "boolean"},
                "sql_repair_correct": {"type": "boolean"},
                "dialect_compatible": {"type": "boolean"},
                "version_compatible": {"type": "boolean"},
                "answer_relevance": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "faithfulness": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "citation_coverage": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "reason": {"type": "string"},
            },
        }
        for label in ("A", "B")
    },
}


@dataclass(frozen=True, slots=True)
class JudgePolicy:
    """Config-derived local-judge identity and frozen evaluation settings."""

    model: str
    model_digest: str
    base_url: str
    timeout_seconds: float
    think: bool
    thinking_disabled: bool
    temperature: float
    seed: int
    num_ctx: int
    num_predict: int
    top_k: int
    top_p: float
    repeat_penalty: float
    max_attempts: int
    retry_backoff_seconds: tuple[float, ...] = (0.25, 0.50)


JUDGE_POLICY_DEFAULTS = {
    "model": JUDGE_MODEL,
    "model_digest": JUDGE_MODEL_DIGEST,
    "base_url": "http://127.0.0.1:11434",
    "timeout_seconds": 300.0,
    "think": False,
    "thinking_disabled": True,
    "temperature": 0.0,
    "seed": 20260831,
    "num_ctx": 16384,
    "num_predict": 1200,
    "top_k": 40,
    "top_p": 1.0,
    "repeat_penalty": 1.0,
    "max_attempts": 3,
}


@dataclass(frozen=True, slots=True)
class OfflineEvaluationPaths:
    """Resolved paths consumed and produced by the offline evaluator."""

    repo_root: Path
    release_dir: Path
    config: Path
    g0_run: Path
    g1_run: Path
    g1_evidence: Path
    prepared_queries: Path
    references: Path
    qrels: Path
    evaluation_dir: Path
    reports_dir: Path
    seal: Path
    judgments: Path
    per_query: Path
    overall: Path
    acceptance: Path
    report: Path

    @classmethod
    def resolve(cls, paths: Any) -> "OfflineEvaluationPaths":
        if isinstance(paths, cls):
            return paths

        supplied_path = Path(paths).resolve() if isinstance(paths, (str, os.PathLike)) else None
        explicit_root = _path_member(paths, "repo_root", "root")
        explicit_release = _path_member(
            paths,
            "release_dir",
            "generation_v1_dir",
            "generation_dir",
            "base_dir",
        )

        if explicit_release is not None:
            release = explicit_release.resolve()
        elif supplied_path is not None and supplied_path.name == "generation-v1":
            release = supplied_path
        elif supplied_path is not None:
            release = supplied_path / "generation" / "generation-v1"
        elif explicit_root is not None:
            root_candidate = explicit_root.resolve()
            release = (
                root_candidate
                if root_candidate.name == "generation-v1"
                else root_candidate / "generation" / "generation-v1"
            )
        else:
            release = Path("generation/generation-v1").resolve()

        if explicit_root is not None and explicit_root.name != "generation-v1":
            repo_root = explicit_root.resolve()
        elif release.parent.name == "generation":
            repo_root = release.parent.parent.resolve()
        else:
            repo_root = Path.cwd().resolve()

        evaluation_dir = _path_member(paths, "evaluation_dir") or (release / "evaluation")
        reports_dir = _path_member(paths, "reports_dir") or (release / "reports")

        def member(default: Path, *names: str) -> Path:
            return (_path_member(paths, *names) or default).resolve()

        return cls(
            repo_root=repo_root,
            release_dir=release,
            config=member(
                release / "config" / "generation.yaml", "config", "config_file"
            ),
            g0_run=member(
                release / "runs" / "g0_closed_book_dev250.jsonl",
                "g0_run",
                "g0_run_path",
                "closed_book_run",
            ),
            g1_run=member(
                release / "runs" / "g1_retrieval_v1_rag_dev250.jsonl",
                "g1_run",
                "g1_run_path",
                "rag_run",
            ),
            g1_evidence=member(
                release / "prepared_inputs" / "g1_evidence_top5.jsonl",
                "g1_evidence",
                "g1_evidence_path",
                "prepared_evidence",
            ),
            prepared_queries=member(
                release / "prepared_inputs" / "online_queries.jsonl",
                "prepared_queries",
                "online_queries",
            ),
            references=member(
                repo_root / "annotation" / "codex" / "dev_250.jsonl",
                "references",
                "reference_path",
                "annotation_path",
                "development_cases",
            ),
            qrels=member(
                repo_root
                / "retrieval"
                / "baseline"
                / "qrels"
                / "qrels_effective_dev250.trec",
                "qrels",
                "qrels_path",
            ),
            evaluation_dir=evaluation_dir.resolve(),
            reports_dir=reports_dir.resolve(),
            seal=member(
                evaluation_dir / "generation_seal.json", "seal", "generation_seal"
            ),
            judgments=member(
                evaluation_dir / "judgments.jsonl", "judgments", "judgments_path"
            ),
            per_query=member(
                evaluation_dir / "per_query_comparison.jsonl",
                "per_query",
                "per_query_path",
            ),
            overall=member(
                evaluation_dir / "overall_metrics.json", "overall", "overall_metrics"
            ),
            acceptance=member(
                evaluation_dir / "acceptance.json", "acceptance", "acceptance_path"
            ),
            report=member(
                reports_dir / "generation_v1_report.md", "report", "report_path"
            ),
        )


def _load_judge_policy(path: Path) -> JudgePolicy:
    """Derive judge identity from the formal generation config.

    ``offline_judge`` may override judge-only decoding values, but it may not
    change model identity or thinking mode relative to ``ollama``.  When the
    optional section is absent, generation decoding is reused with the formally
    decided judge seed override.
    """

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("generation config must be a mapping")
    ollama = value.get("ollama")
    retry = value.get("retry_policy")
    judge = value.get("offline_judge", {})
    if not isinstance(ollama, Mapping) or not isinstance(retry, Mapping):
        raise ValueError("generation config is missing ollama/retry_policy")
    if not isinstance(judge, Mapping):
        raise ValueError("offline_judge config must be a mapping")
    generation_options = ollama.get("options")
    judge_options = judge.get("options", {})
    if not isinstance(generation_options, Mapping) or not isinstance(judge_options, Mapping):
        raise ValueError("judge/generation options must be mappings")
    if not judge or judge.get("inherit_generation_model") is not True:
        raise ValueError("offline judge must explicitly inherit the generation model")

    model = judge.get("model_tag", ollama.get("model_tag"))
    digest = judge.get("model_digest", ollama.get("model_digest"))
    think = judge.get("think", ollama.get("think"))
    if model != ollama.get("model_tag") or digest != ollama.get("model_digest"):
        raise ValueError("offline judge must share generation model tag and digest")
    if think != ollama.get("think"):
        raise ValueError("offline judge must share generation thinking mode")
    if model != JUDGE_MODEL or digest != JUDGE_MODEL_DIGEST:
        raise ValueError("unexpected frozen Phase 10 judge model identity")
    if think is not False:
        raise ValueError("Phase 10 thinking must be disabled with boolean false")

    def option(name: str, default: Any) -> Any:
        aliases = (name, "temp") if name == "temperature" else (name,)
        for alias in aliases:
            if alias in judge_options:
                return judge_options[alias]
            if alias in judge:
                return judge[alias]
        return generation_options.get(name, default)

    seed = int(option("seed", 20260831))
    # The judge seed intentionally differs from generation while every other
    # model identity/thinking invariant remains paired.
    if "seed" not in judge_options and "seed" not in judge:
        seed = 20260831
    if seed != 20260831:
        raise ValueError("offline judge seed must remain 20260831")
    max_attempts = int(judge.get("max_attempts", retry.get("max_attempts", 3)))
    if max_attempts != 3:
        raise ValueError("offline judge retry policy must remain three attempts")
    policy = JudgePolicy(
        model=str(model),
        model_digest=str(digest),
        base_url=str(judge.get("base_url", ollama.get("base_url"))).rstrip("/"),
        timeout_seconds=float(
            judge.get(
                "timeout_seconds",
                judge.get("timeout", ollama.get("timeout_seconds", 300.0)),
            )
        ),
        think=False,
        thinking_disabled=True,
        temperature=float(option("temperature", 0.0)),
        seed=seed,
        num_ctx=int(option("num_ctx", 16384)),
        num_predict=int(option("num_predict", 1200)),
        top_k=int(option("top_k", 40)),
        top_p=float(option("top_p", 1.0)),
        repeat_penalty=float(option("repeat_penalty", 1.0)),
        max_attempts=max_attempts,
    )
    if policy.temperature != 0.0:
        raise ValueError("offline judge temperature must remain 0")
    if min(policy.num_ctx, policy.num_predict, policy.top_k) <= 0:
        raise ValueError("offline judge integer options must be positive")
    if not 0.0 <= policy.top_p <= 1.0 or policy.repeat_penalty <= 0.0:
        raise ValueError("invalid offline judge decoding options")
    return policy


class OllamaJudgeClient:
    """Minimal stdlib Ollama client used when no injected client is supplied."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def __call__(
        self,
        *,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        options: Mapping[str, Any],
        think: bool,
        **_: Any,
    ) -> Mapping[str, Any]:
        payload = {
            "model": model,
            "stream": False,
            "think": think,
            "format": schema,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict offline evaluator. Return only the JSON "
                        "object required by the supplied schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "options": dict(options),
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/api/chat",
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        result = json.loads(body)
        if not isinstance(result, Mapping):
            raise ValueError("Ollama judge response must be an object")
        return result

    def preflight(self, expected_tag: str, expected_digest: str) -> dict[str, Any]:
        def get(path: str) -> Mapping[str, Any]:
            http_request = request.Request(f"{self.base_url}{path}", method="GET")
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError(f"Ollama {path} response must be an object")
            return value

        version_value = get("/api/version")
        tags_value = get("/api/tags")
        version = version_value.get("version")
        models = tags_value.get("models")
        if not isinstance(version, str) or not isinstance(models, list):
            raise ValueError("Ollama preflight omitted version/models")
        matches = [
            item
            for item in models
            if isinstance(item, Mapping)
            and (item.get("name") == expected_tag or item.get("model") == expected_tag)
        ]
        if len(matches) != 1 or matches[0].get("digest") != expected_digest:
            raise ValueError("Ollama judge model identity mismatch")
        return {
            "model_tag": expected_tag,
            "model_digest": expected_digest,
            "ollama_version": version,
        }


def run_offline_evaluation(
    paths: Any,
    client: Any = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Evaluate the sealed G0/G1 formal runs and publish Phase 10 artifacts.

    Ordering is a release invariant: both run files are fully parsed and
    validated, the G1 evidence/provenance contract is checked, and
    ``generation_seal.json`` is durably written *before* the reference or qrels
    paths are opened for the first time.
    """

    resolved = OfflineEvaluationPaths.resolve(paths)
    resolved.evaluation_dir.mkdir(parents=True, exist_ok=True)
    resolved.reports_dir.mkdir(parents=True, exist_ok=True)
    judge_policy = _load_judge_policy(resolved.config)
    judge_config_sha256 = _sha256_file(resolved.config)

    # Online-safe artifacts only up to and including the durable seal write.
    g0_rows = _load_formal_run(resolved.g0_run, G0_SYSTEM_ID)
    g1_rows = _load_formal_run(resolved.g1_run, G1_SYSTEM_ID)
    query_ids = _validate_paired_runs(g0_rows, g1_rows)
    evidence = _load_g1_evidence(resolved.g1_evidence, query_ids)
    safe_queries = _load_safe_queries(resolved.prepared_queries, query_ids)
    _validate_input_provenance(g0_rows, g1_rows, evidence, safe_queries)
    _validate_generation_identity(g0_rows, g1_rows, judge_policy)

    seal = _build_generation_seal(resolved, g0_rows, g1_rows, query_ids)
    _write_json(resolved.seal, seal)

    # This timestamp is captured only after the first seal write has completed.
    references_opened_at = _utc_now()
    references = _load_references(resolved.references)
    qrels_opened_at = _utc_now()
    qrels = _load_qrels(resolved.qrels)
    if set(references) != set(query_ids):
        raise ValueError("reference query IDs do not exactly match the sealed runs")

    seal["reference_access"] = {
        "seal_written_before_reference_access": True,
        "references_opened_at_utc": references_opened_at,
        "qrels_opened_at_utc": qrels_opened_at,
        "references_path": _display_path(resolved.references, resolved.repo_root),
        "references_sha256": _sha256_file(resolved.references),
        "qrels_path": _display_path(resolved.qrels, resolved.repo_root),
        "qrels_sha256": _sha256_file(resolved.qrels),
    }
    evaluation_input_sha256 = {
        "development_references_file": seal["reference_access"]["references_sha256"],
        "effective_qrels_file": seal["reference_access"]["qrels_sha256"],
        "prepared_queries_file": _sha256_file(resolved.prepared_queries),
        "g1_evidence_file": _sha256_file(resolved.g1_evidence),
    }
    evaluation_context_sha256 = _canonical_sha256(evaluation_input_sha256)
    # A resumed judgment is valid only for this exact offline prompt/metric
    # context.  The mapping contains hashes, never reference field contents.
    seal["offline_evaluation_inputs"] = {
        "sha256": evaluation_input_sha256,
        "context_sha256": evaluation_context_sha256,
    }
    seal["event_sequence"].extend(
        [
            {"event": "offline_references_opened", "at_utc": references_opened_at},
            {"event": "offline_qrels_opened", "at_utc": qrels_opened_at},
        ]
    )
    _write_json(resolved.seal, seal)

    run_sha = {
        "g0": seal["runs"]["g0"]["sha256"],
        "g1": seal["runs"]["g1"]["sha256"],
    }
    g0_by_id = {row["query_id"]: row for row in g0_rows}
    g1_by_id = {row["query_id"]: row for row in g1_rows}
    policy_hash = _canonical_sha256(asdict(judge_policy))
    resumed = (
        _load_resumable_judgments(
            resolved.judgments,
            run_sha,
            evaluation_input_sha256,
            evaluation_context_sha256,
            policy_hash,
            judge_policy.max_attempts,
        )
        if resume
        else {}
    )
    if not resume and resolved.judgments.exists():
        resolved.judgments.unlink()

    judgments: dict[str, dict[str, Any]] = dict(resumed)
    unknown_resumed = sorted(set(judgments) - set(query_ids))
    if unknown_resumed:
        raise ValueError(f"judgment journal has unknown queries: {unknown_resumed[:5]}")

    # Validate every reused prompt against the current prompt builder before a
    # completed journal is allowed to bypass Ollama.  Failed rows have already
    # exhausted the uniform retry policy (enforced by the loader) and are
    # terminal formal failure records; resume never grants extra attempts.
    for ordinal, query_id in enumerate(query_ids, start=1):
        if query_id not in judgments:
            continue
        assignment = _counterbalanced_assignment(ordinal)
        prompt = _judge_prompt(
            query_id=query_id,
            safe_query=safe_queries[query_id],
            reference=references[query_id],
            evidence=evidence[query_id],
            g0_wrapper=g0_by_id[query_id],
            g1_wrapper=g1_by_id[query_id],
            assignment=assignment,
        )
        _validate_resumed_assignment(
            judgments[query_id],
            ordinal,
            run_sha,
            policy_hash,
            evaluation_input_sha256,
            evaluation_context_sha256,
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )

    journal_complete = set(judgments) == set(query_ids)
    if journal_complete:
        _validate_optional_overall_response_schema(resolved.overall)
        model_identity = _model_identity_from_resumed(judgments, judge_policy)
        judge: Any = None
    else:
        judge = client if client is not None else OllamaJudgeClient(
            judge_policy.base_url, timeout_seconds=judge_policy.timeout_seconds
        )
        model_identity = _judge_preflight(judge, judge_policy)

    for ordinal, query_id in enumerate(query_ids, start=1):
        if query_id in judgments:
            continue
        assignment = _counterbalanced_assignment(ordinal)
        prompt = _judge_prompt(
            query_id=query_id,
            safe_query=safe_queries[query_id],
            reference=references[query_id],
            evidence=evidence[query_id],
            g0_wrapper=g0_by_id[query_id],
            g1_wrapper=g1_by_id[query_id],
            assignment=assignment,
        )
        anonymous, attempts = _judge_with_retry(judge, prompt, judge_policy)
        status = "success" if anonymous is not None else "failed"
        if anonymous is None:
            anonymous = {"A": _failed_side(), "B": _failed_side()}
        by_system = {
            assignment["A"]: anonymous["A"],
            assignment["B"]: anonymous["B"],
        }
        row = {
            "schema_version": "sqlmend-generation-judgment-v1",
            "query_id": query_id,
            "ordinal": ordinal,
            "assignment": assignment,
            "counterbalance": "odd:g0=A;even:g1=A",
            "status": status,
            "model": judge_policy.model,
            "model_tag": judge_policy.model,
            "model_digest": judge_policy.model_digest,
            "think": judge_policy.think,
            "thinking_disabled": judge_policy.thinking_disabled,
            "model_identity": model_identity,
            "policy_sha256": policy_hash,
            "policy_source_config_sha256": judge_config_sha256,
            "run_sha256": run_sha,
            "evaluation_input_sha256": evaluation_input_sha256,
            "evaluation_context_sha256": evaluation_context_sha256,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "attempt_count": len(attempts),
            "retry_count": max(0, len(attempts) - 1),
            "attempts": attempts,
            "decision": {
                "g0": _decision_for_wrapper(by_system[G0_SYSTEM_ID], g0_by_id[query_id]),
                "g1": _decision_for_wrapper(by_system[G1_SYSTEM_ID], g1_by_id[query_id]),
            },
        }
        _append_jsonl(resolved.judgments, row)
        judgments[query_id] = row

    if set(judgments) != set(query_ids):  # pragma: no cover - defensive guard
        raise RuntimeError("judgment journal is incomplete")
    # Compact the resumable journal into canonical query order. This also makes
    # the finalized journal observably newer than the updated seal on a resumed
    # evaluation, without issuing any additional judge call.
    _write_jsonl(resolved.judgments, [judgments[query_id] for query_id in query_ids])

    comparison_rows = [
        _comparison_row(
            query_id=query_id,
            ordinal=ordinal,
            g0=g0_by_id[query_id],
            g1=g1_by_id[query_id],
            evidence=evidence[query_id],
            qrels=qrels.get(query_id, {}),
            judgment=judgments[query_id],
        )
        for ordinal, query_id in enumerate(query_ids, start=1)
    ]
    _write_jsonl(resolved.per_query, comparison_rows)

    g0_metrics = aggregate_system_metrics(
        [row["g0"] for row in comparison_rows], rag_system=False
    )
    g1_metrics = aggregate_system_metrics(
        [row["g1"] for row in comparison_rows], rag_system=True
    )
    paired = paired_summary(comparison_rows)
    paired["success_target"] = {
        "required_absolute_delta": 0.10,
        "required_percentage_points": 10.0,
        "achieved": paired["task_success_absolute_delta"] >= 0.10,
    }

    judge_failures = sum(row["status"] != "success" for row in judgments.values())
    total_retries = sum(int(row["retry_count"]) for row in judgments.values())
    overall: dict[str, Any] = {
        "schema_version": "sqlmend-generation-evaluation-v1",
        "evaluation_label": EVALUATION_LABEL,
        "data_status": (
            "machine-proposed development data; not human gold and not a final "
            "held-out test set"
        ),
        "created_at_utc": _utc_now(),
        "query_count": EXPECTED_QUERY_COUNT,
        "formal_answer_count": EXPECTED_QUERY_COUNT * 2,
        "formal_result_wrapper_count": EXPECTED_QUERY_COUNT * 2,
        "formal_answer_count_semantics": (
            "formal_result_wrappers_including_explicit_failure_records"
        ),
        "generation_seals": seal["runs"],
        "seal_path": _display_path(resolved.seal, resolved.repo_root),
        "judge": {
            **asdict(judge_policy),
            "model_tag": judge_policy.model,
            "think": judge_policy.think,
            "thinking_disabled": judge_policy.thinking_disabled,
            "model_identity": model_identity,
            "policy_sha256": policy_hash,
            "config_path": _display_path(resolved.config, resolved.repo_root),
            "config_sha256": judge_config_sha256,
            "evaluation_input_sha256": evaluation_input_sha256,
            "evaluation_context_sha256": evaluation_context_sha256,
            "logical_query_count": EXPECTED_QUERY_COUNT,
            "completed_count": EXPECTED_QUERY_COUNT - judge_failures,
            "failed_count": judge_failures,
            "completed_count_semantics": "judge_call_success",
            "judge_call_success_count": EXPECTED_QUERY_COUNT - judge_failures,
            "judge_call_failure_count": judge_failures,
            "retry_count": total_retries,
            "calls_per_query": 1,
            "counterbalance": {
                "odd": "A=g0,B=g1",
                "even": "A=g1,B=g0",
                "a_g0_count": sum(
                    row["assignment"]["A"] == G0_SYSTEM_ID for row in judgments.values()
                ),
                "a_g1_count": sum(
                    row["assignment"]["A"] == G1_SYSTEM_ID for row in judgments.values()
                ),
            },
            "response_schema_sha256": _canonical_sha256(JUDGE_RESPONSE_SCHEMA),
        },
        "systems": {
            "g0": {"system_id": G0_SYSTEM_ID, **g0_metrics},
            "g1": {"system_id": G1_SYSTEM_ID, **g1_metrics},
        },
        "paired": paired,
        "artifacts": {
            "g0_answers": _display_path(resolved.g0_run, resolved.repo_root),
            "g1_answers": _display_path(resolved.g1_run, resolved.repo_root),
            "g1_evidence": _display_path(resolved.g1_evidence, resolved.repo_root),
            "judgments": _display_path(resolved.judgments, resolved.repo_root),
            "per_query_comparison": _display_path(resolved.per_query, resolved.repo_root),
            "overall_metrics": _display_path(resolved.overall, resolved.repo_root),
            "acceptance": _display_path(resolved.acceptance, resolved.repo_root),
            "report": _display_path(resolved.report, resolved.repo_root),
        },
    }
    _write_json(resolved.overall, overall)

    acceptance = _build_acceptance(overall, comparison_rows)
    _write_json(resolved.acceptance, acceptance)
    overall["acceptance"] = acceptance
    # Keep the convenience embedded acceptance synchronized with the standalone
    # artifact while retaining a single authoritative metrics structure.
    _write_json(resolved.overall, overall)

    from .reporting import write_generation_report

    write_generation_report(resolved, overall, comparison_rows)
    return overall


def _load_formal_run(path: Path, expected_system_id: str) -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    if len(rows) != EXPECTED_QUERY_COUNT:
        raise ValueError(
            f"{path} must contain exactly {EXPECTED_QUERY_COUNT} formal wrappers; "
            f"found {len(rows)}"
        )
    seen: set[str] = set()
    for line_number, row in enumerate(rows, start=1):
        missing = sorted(RUN_REQUIRED_FIELDS - set(row))
        if missing:
            raise ValueError(f"{path}:{line_number} missing wrapper fields: {missing}")
        query_id = _identifier(row.get("query_id"), "query_id")
        if query_id in seen:
            raise ValueError(f"duplicate query_id in {path}: {query_id}")
        seen.add(query_id)
        if row.get("schema_version") != "sqlmend-generation-record-v1":
            raise ValueError(f"{path}:{line_number} is not a formal generation record")
        _identifier(row.get("experiment_id"), "experiment_id")
        if row.get("system_id") != expected_system_id:
            raise ValueError(
                f"{path}:{line_number} has system_id={row.get('system_id')!r}; "
                f"expected {expected_system_id!r}"
            )
        status = row.get("status")
        if status not in {"success", "failed"}:
            raise ValueError(f"{path}:{line_number} status must be success or failed")
        answer = row.get("answer")
        if answer is not None and not isinstance(answer, Mapping):
            raise ValueError(f"{path}:{line_number} answer must be an object or null")
        if status == "success" and not isinstance(answer, Mapping):
            raise ValueError(f"{path}:{line_number} successful wrapper needs an answer")
        for field in ("structured_output_valid", "contract_valid"):
            if not isinstance(row.get(field), bool):
                raise ValueError(f"{path}:{line_number} {field} must be boolean")
        if not isinstance(row.get("validation_errors"), list):
            raise ValueError(f"{path}:{line_number} validation_errors must be a list")
        if not isinstance(row.get("input_provenance"), Mapping):
            raise ValueError(f"{path}:{line_number} input_provenance must be an object")
        generation_provenance = row.get("generation_provenance")
        if not isinstance(generation_provenance, Mapping):
            raise ValueError(f"{path}:{line_number} generation_provenance must be an object")
        attempts = generation_provenance.get("attempts")
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 3:
            raise ValueError(
                f"{path}:{line_number} generation attempts must contain 1..3 records"
            )
        if not all(isinstance(attempt, Mapping) for attempt in attempts):
            raise ValueError(
                f"{path}:{line_number} generation attempts must be objects"
            )
        if generation_provenance.get("attempt_count") != len(attempts):
            raise ValueError(
                f"{path}:{line_number} generation attempt_count differs from attempts"
            )
        if generation_provenance.get("retry_count") != len(attempts) - 1:
            raise ValueError(
                f"{path}:{line_number} generation retry_count differs from attempts"
            )
        latency = row.get("latency")
        if not isinstance(latency, Mapping):
            raise ValueError(f"{path}:{line_number} latency must be an object")
        _nonnegative_number(latency.get("wall_ms"), "latency.wall_ms")
    return rows


def _validate_paired_runs(
    g0_rows: Sequence[Mapping[str, Any]],
    g1_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    g0_ids = {str(row["query_id"]) for row in g0_rows}
    g1_ids = {str(row["query_id"]) for row in g1_rows}
    if g0_ids != g1_ids:
        raise ValueError(
            "G0/G1 query sets differ: "
            f"only_g0={sorted(g0_ids - g1_ids)[:5]}, "
            f"only_g1={sorted(g1_ids - g0_ids)[:5]}"
        )
    return sorted(g0_ids, key=_query_sort_key)


def _load_g1_evidence(
    path: Path,
    query_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path)
    if len(rows) != EXPECTED_QUERY_COUNT:
        raise ValueError(f"G1 evidence must have {EXPECTED_QUERY_COUNT} rows")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        query_id = _identifier(row.get("query_id"), "evidence query_id")
        if query_id in result:
            raise ValueError(f"duplicate G1 evidence query: {query_id}")
        passages = row.get("passages")
        if not isinstance(passages, list) or len(passages) != 5:
            raise ValueError(f"G1 evidence must contain exactly Top-5 passages: {query_id}")
        ids: list[str] = []
        for expected_rank, passage in enumerate(passages, start=1):
            if not isinstance(passage, Mapping):
                raise ValueError(f"malformed G1 evidence passage: {query_id}")
            passage_id = _identifier(
                passage.get("passage_id", passage.get("chunk_id")), "passage_id"
            )
            if passage_id in ids:
                raise ValueError(f"duplicate passage ID for {query_id}: {passage_id}")
            rank = passage.get("rank")
            if rank is not None and int(rank) != expected_rank:
                raise ValueError(f"G1 evidence ranks are not contiguous for {query_id}")
            ids.append(passage_id)
        normalized = dict(row)
        normalized["passage_ids"] = ids
        result[query_id] = normalized
    if set(result) != set(query_ids):
        raise ValueError("G1 evidence query IDs do not match the sealed run pair")
    return result


def _load_safe_queries(
    path: Path,
    query_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    allowed = {
        "schema_version",
        "query_id",
        "source_fields_used",
        "serialized_text",
        "serialized_text_sha256",
        "serializer_version",
    }
    result: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        if set(row) != allowed or row.get("schema_version") != "sqlmend-online-query-v1":
            raise ValueError("offline judge safe query is outside the online projection contract")
        query_id = _identifier(row.get("query_id"), "safe query_id")
        if query_id in result:
            raise ValueError(f"duplicate safe query: {query_id}")
        serialized = row.get("serialized_text")
        if not isinstance(serialized, str) or not serialized:
            raise ValueError(f"safe query text is missing for {query_id}")
        if hashlib.sha256(serialized.encode("utf-8")).hexdigest() != row.get(
            "serialized_text_sha256"
        ):
            raise ValueError(f"safe query SHA mismatch for {query_id}")
        result[query_id] = row
    if set(result) != set(query_ids):
        raise ValueError("safe query IDs do not match the formal run pair")
    return result


def _validate_input_provenance(
    g0_rows: Sequence[Mapping[str, Any]],
    g1_rows: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Mapping[str, Any]],
    safe_queries: Mapping[str, Mapping[str, Any]],
) -> None:
    for row in g0_rows:
        provenance = row["input_provenance"]
        if provenance.get("serialized_query_sha256") != safe_queries[row["query_id"]].get(
            "serialized_text_sha256"
        ):
            raise ValueError(f"G0 safe query SHA mismatch: {row['query_id']}")
        ids = provenance.get("evidence_passage_ids", [])
        if ids not in (None, []) and tuple(ids) != ():
            raise ValueError(f"G0 must not receive evidence: {row['query_id']}")
    for row in g1_rows:
        query_id = str(row["query_id"])
        provenance = row["input_provenance"]
        if provenance.get("serialized_query_sha256") != safe_queries[query_id].get(
            "serialized_text_sha256"
        ):
            raise ValueError(f"G1 safe query SHA mismatch: {query_id}")
        ids = provenance.get("evidence_passage_ids")
        if not isinstance(ids, list) or ids != evidence[query_id]["passage_ids"]:
            raise ValueError(f"G1 wrapper evidence IDs do not match prepared evidence: {query_id}")
        wrapper_sha = provenance.get("evidence_sha256")
        evidence_sha = evidence[query_id].get("evidence_sha256")
        if wrapper_sha is not None and evidence_sha is not None and wrapper_sha != evidence_sha:
            raise ValueError(f"G1 wrapper evidence SHA mismatch: {query_id}")


def _validate_generation_identity(
    g0_rows: Sequence[Mapping[str, Any]],
    g1_rows: Sequence[Mapping[str, Any]],
    policy: JudgePolicy,
) -> None:
    """Require both sealed systems and judge to share model identity/thinking."""

    for row in (*g0_rows, *g1_rows):
        provenance = row["generation_provenance"]
        query_id = row["query_id"]
        if provenance.get("model_tag") != policy.model:
            raise ValueError(f"generation model tag differs for {query_id}")
        if provenance.get("model_digest") != policy.model_digest:
            raise ValueError(f"generation model digest differs for {query_id}")
        if provenance.get("think") is not False:
            raise ValueError(f"generation thinking is not disabled for {query_id}")


def _judge_preflight(client: Any, policy: JudgePolicy) -> dict[str, Any]:
    method = getattr(client, "preflight", None)
    if not callable(method):
        # Test/dry-run injected clients can omit an external preflight. The
        # configured identity remains explicit rather than being invented.
        return {
            "model_tag": policy.model,
            "model_digest": policy.model_digest,
            "think": False,
            "thinking_disabled": True,
            "ollama_version": None,
            "preflight_verified": False,
        }
    value = method(policy.model, policy.model_digest)
    if isinstance(value, Mapping):
        tag = value.get("model_tag")
        digest = value.get("model_digest")
        version = value.get("ollama_version")
    else:
        tag = getattr(value, "model_tag", None)
        digest = getattr(value, "model_digest", None)
        version = getattr(value, "ollama_version", None)
    if tag != policy.model or digest != policy.model_digest:
        raise ValueError("judge preflight returned a different model identity")
    return {
        "model_tag": tag,
        "model_digest": digest,
        "think": False,
        "thinking_disabled": True,
        "ollama_version": version,
        "preflight_verified": True,
    }


def _build_generation_seal(
    paths: OfflineEvaluationPaths,
    g0_rows: Sequence[Mapping[str, Any]],
    g1_rows: Sequence[Mapping[str, Any]],
    query_ids: Sequence[str],
) -> dict[str, Any]:
    sealed_at = _utc_now()

    def run_entry(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        generation_success_count = sum(
            row["status"] == "success" for row in rows
        )
        generation_failure_count = len(rows) - generation_success_count
        return {
            "path": _display_path(path, paths.repo_root),
            "sha256": _sha256_file(path),
            "byte_size": path.stat().st_size,
            "record_count": len(rows),
            "success_count_semantics": "generation_contract_success",
            "success_count": generation_success_count,
            "failed_count": generation_failure_count,
            "generation_contract_success_count": generation_success_count,
            "generation_contract_failure_count": generation_failure_count,
            "query_ids_sha256": hashlib.sha256(
                ("\n".join(query_ids) + "\n").encode("utf-8")
            ).hexdigest(),
        }

    return {
        "schema_version": "sqlmend-generation-seal-v1",
        "evaluation_label": EVALUATION_LABEL,
        "sealed_at_utc": sealed_at,
        "validation": {
            "expected_records_per_run": EXPECTED_QUERY_COUNT,
            "g0_valid_formal_wrappers": True,
            "g1_valid_formal_wrappers": True,
            "paired_query_ids_identical": True,
            "g0_has_no_evidence": True,
            "g1_evidence_matches_prepared_input": True,
        },
        "runs": {
            "g0": run_entry(paths.g0_run, g0_rows),
            "g1": run_entry(paths.g1_run, g1_rows),
        },
        "reference_access": {
            "seal_written_before_reference_access": False,
            "references_opened_at_utc": None,
            "qrels_opened_at_utc": None,
        },
        "event_sequence": [
            {"event": "formal_runs_validated", "at_utc": sealed_at},
            {"event": "generation_runs_sealed", "at_utc": sealed_at},
        ],
    }


def _load_references(path: Path) -> dict[str, dict[str, Any]]:
    """Load only the explicitly permitted post-generation reference fields."""

    result: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        query_id = _identifier(row.get("query_id"), "reference query_id")
        if query_id in result:
            raise ValueError(f"duplicate reference query_id: {query_id}")
        missing = [field for field in REFERENCE_FIELDS[:6] if field not in row]
        if missing:
            raise ValueError(f"reference {query_id} missing fields: {missing}")
        result[query_id] = {field: row.get(field) for field in REFERENCE_FIELDS}
    return result


def _load_qrels(path: Path) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                continue
            columns = line.split()
            if len(columns) != 4:
                raise ValueError(f"malformed qrels line {path}:{line_number}")
            query_id, _, passage_id, relevance_text = columns
            relevance = _number(relevance_text, "qrels relevance")
            query = result.setdefault(query_id, {})
            if passage_id in query and query[passage_id] != relevance:
                raise ValueError(f"conflicting duplicate qrel: {query_id}/{passage_id}")
            query[passage_id] = relevance
    return result


def _counterbalanced_assignment(ordinal: int) -> dict[str, str]:
    if ordinal % 2 == 1:
        return {"A": G0_SYSTEM_ID, "B": G1_SYSTEM_ID}
    return {"A": G1_SYSTEM_ID, "B": G0_SYSTEM_ID}


def _judge_prompt(
    *,
    query_id: str,
    safe_query: Mapping[str, Any],
    reference: Mapping[str, Any],
    evidence: Mapping[str, Any],
    g0_wrapper: Mapping[str, Any],
    g1_wrapper: Mapping[str, Any],
    assignment: Mapping[str, str],
) -> str:
    wrappers = {G0_SYSTEM_ID: g0_wrapper, G1_SYSTEM_ID: g1_wrapper}
    answers = {
        label: _answer_for_judge(wrappers[system_id])
        for label, system_id in assignment.items()
    }
    passages = []
    for passage in evidence["passages"]:
        if not isinstance(passage, Mapping):  # already validated
            continue
        passages.append(
            {
                "passage_id": passage.get("passage_id", passage.get("chunk_id")),
                "rank": passage.get("rank"),
                "dialect": passage.get("dialect"),
                "version": passage.get("version"),
                "source_name": passage.get("source_name"),
                "source_url": passage.get("source_url"),
                "title": passage.get("title"),
                "section": passage.get("section"),
                "text": passage.get("text"),
            }
        )

    payload = {
        "query_id": query_id,
        "safe_user_input": safe_query["serialized_text"],
        "reference": {field: reference.get(field) for field in REFERENCE_FIELDS},
        "retrieved_evidence": passages,
        "answer_A": answers["A"],
        "answer_B": answers["B"],
    }
    return (
        "Evaluate two anonymous SQL-debugging answers A and B independently. "
        "Do not guess which system produced either answer. Use the offline "
        "reference to judge root-cause correctness, SQL repair correctness, "
        "dialect compatibility, version compatibility, and answer relevance. "
        "Use only retrieved_evidence to judge faithfulness and citation coverage; "
        "score those two fields for both anonymous answers so the comparison stays "
        "symmetric. A failed/missing answer receives false for every boolean and "
        "0 for every score. A repair may differ textually from reference_fix_sql "
        "when it is semantically equivalent and preserves expected_behavior. "
        "Return exactly one JSON object. Do not use Markdown or a code fence. "
        "Do not omit keys and do not add keys. The exact required keys and value "
        "types are: top-level A and B; inside each, root_cause_correct (boolean), "
        "sql_repair_correct (boolean), dialect_compatible (boolean), "
        "version_compatible (boolean), answer_relevance (number 0..1), "
        "faithfulness (number 0..1), citation_coverage (number 0..1), and reason "
        "(string). Valid shape example (replace every value with your judgment): "
        '{"A":{"root_cause_correct":false,"sql_repair_correct":false,'
        '"dialect_compatible":false,"version_compatible":false,'
        '"answer_relevance":0.0,"faithfulness":0.0,"citation_coverage":0.0,'
        '"reason":"brief reason"},"B":{"root_cause_correct":false,'
        '"sql_repair_correct":false,"dialect_compatible":false,'
        '"version_compatible":false,"answer_relevance":0.0,"faithfulness":0.0,'
        '"citation_coverage":0.0,"reason":"brief reason"}}.\n\n'
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _answer_for_judge(wrapper: Mapping[str, Any]) -> Any:
    if wrapper.get("status") == "success" and isinstance(wrapper.get("answer"), Mapping):
        return wrapper["answer"]
    return {
        "generation_status": "failed",
        "failure": wrapper.get("failure"),
        "answer": wrapper.get("answer"),
    }


def _judge_with_retry(
    client: Any,
    prompt: str,
    policy: JudgePolicy,
) -> tuple[dict[str, dict[str, Any]] | None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    for attempt_number in range(1, policy.max_attempts + 1):
        started = time.perf_counter()
        try:
            response = _invoke_client(client, prompt, policy)
            decision = _parse_judge_response(response)
            elapsed = (time.perf_counter() - started) * 1000.0
            attempts.append(
                {
                    "attempt": attempt_number,
                    "status": "success",
                    "wall_ms": elapsed,
                    "response_sha256": _canonical_sha256(decision),
                }
            )
            return decision, attempts
        except Exception as error:  # retries cover transport and schema uniformly
            elapsed = (time.perf_counter() - started) * 1000.0
            attempts.append(
                {
                    "attempt": attempt_number,
                    "status": "failed",
                    "wall_ms": elapsed,
                    "error_type": type(error).__name__,
                    "error": str(error)[:1000],
                }
            )
            if attempt_number < policy.max_attempts:
                delay = policy.retry_backoff_seconds[
                    min(attempt_number - 1, len(policy.retry_backoff_seconds) - 1)
                ]
                time.sleep(delay)
    return None, attempts


def _invoke_client(client: Any, prompt: str, policy: JudgePolicy) -> Any:
    method = None
    for name in ("generate_structured", "judge", "generate", "chat"):
        candidate = getattr(client, name, None)
        if callable(candidate):
            method = candidate
            break
    if method is None:
        if not callable(client):
            raise TypeError("judge client must be callable or expose a generation method")
        method = client

    options = {
        "temperature": policy.temperature,
        "seed": policy.seed,
        "num_ctx": policy.num_ctx,
        "num_predict": policy.num_predict,
        "top_k": policy.top_k,
        "top_p": policy.top_p,
        "repeat_penalty": policy.repeat_penalty,
    }
    available = {
        "prompt": prompt,
        "user_prompt": prompt,
        "messages": [{"role": "user", "content": prompt}],
        "schema": JUDGE_RESPONSE_SCHEMA,
        "format": JUDGE_RESPONSE_SCHEMA,
        "output_schema": JUDGE_RESPONSE_SCHEMA,
        "response_schema": JUDGE_RESPONSE_SCHEMA,
        "model": policy.model,
        "model_tag": policy.model,
        "options": options,
        "generation_options": options,
        "think": policy.think,
    }
    signature = inspect.signature(method)
    has_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if has_kwargs:
        kwargs = {
            "prompt": prompt,
            "schema": JUDGE_RESPONSE_SCHEMA,
            "model": policy.model,
            "options": options,
            "think": policy.think,
        }
    else:
        kwargs = {
            name: available[name]
            for name in signature.parameters
            if name in available
        }
    if kwargs:
        return method(**kwargs)
    return method(prompt)


def _parse_judge_response(response: Any) -> dict[str, dict[str, Any]]:
    payload: Any = response
    if isinstance(response, Mapping) and not {"A", "B"}.issubset(response):
        if isinstance(response.get("message"), Mapping):
            payload = response["message"].get("content")
        elif "response" in response:
            payload = response.get("response")
        elif "content" in response:
            payload = response.get("content")
    if hasattr(payload, "content") and not isinstance(payload, (str, bytes, Mapping)):
        payload = payload.content
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        payload = json.loads(text)
    if not isinstance(payload, Mapping) or set(payload) != {"A", "B"}:
        raise ValueError("judge response must contain exactly A and B")
    return {label: _validate_side(payload[label], label) for label in ("A", "B")}


def _validate_side(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"judge {label} must be an object")
    required = set(JUDGE_RESPONSE_SCHEMA["properties"][label]["required"])
    if set(value) != required:
        raise ValueError(
            f"judge {label} fields differ from schema: "
            f"missing={sorted(required - set(value))}, extra={sorted(set(value) - required)}"
        )
    result = dict(value)
    for field in (
        "root_cause_correct",
        "sql_repair_correct",
        "dialect_compatible",
        "version_compatible",
    ):
        if not isinstance(result[field], bool):
            raise ValueError(f"judge {label}.{field} must be boolean")
    for field in ("answer_relevance", "faithfulness", "citation_coverage"):
        result[field] = _bounded_score(result[field], f"judge {label}.{field}")
    if not isinstance(result["reason"], str):
        raise ValueError(f"judge {label}.reason must be a string")
    result["reason"] = result["reason"].strip()[:2000]
    return result


def _decision_for_wrapper(
    side: Mapping[str, Any],
    wrapper: Mapping[str, Any],
) -> dict[str, Any]:
    if wrapper.get("status") == "success":
        return dict(side)
    failed = _failed_side()
    failed["reason"] = "formal generation wrapper records a failed model call"
    return failed


def _failed_side() -> dict[str, Any]:
    return {
        "root_cause_correct": False,
        "sql_repair_correct": False,
        "dialect_compatible": False,
        "version_compatible": False,
        "answer_relevance": 0.0,
        "faithfulness": 0.0,
        "citation_coverage": 0.0,
        "reason": "judge failed or answer unavailable",
    }


def _comparison_row(
    *,
    query_id: str,
    ordinal: int,
    g0: Mapping[str, Any],
    g1: Mapping[str, Any],
    evidence: Mapping[str, Any],
    qrels: Mapping[str, float],
    judgment: Mapping[str, Any],
) -> dict[str, Any]:
    decision = judgment["decision"]

    def system_view(wrapper: Mapping[str, Any], side: Mapping[str, Any]) -> dict[str, Any]:
        generation_provenance = wrapper.get("generation_provenance")
        if not isinstance(generation_provenance, Mapping):
            raise ValueError("generation wrapper lacks generation_provenance")
        attempts = generation_provenance["attempts"]
        view = {
            "status": wrapper["status"],
            "structured_output_valid": bool(wrapper["structured_output_valid"]),
            "contract_valid": bool(wrapper["contract_valid"]),
            "generation_attempt_count": len(attempts),
            "generation_retry_count": len(attempts) - 1,
            "root_cause_correct": bool(side["root_cause_correct"]),
            "sql_repair_correct": bool(side["sql_repair_correct"]),
            "dialect_compatible": bool(side["dialect_compatible"]),
            "version_compatible": bool(side["version_compatible"]),
            "answer_relevance": float(side["answer_relevance"]),
            "task_success": task_success(side),
            "latency_wall_ms": _nonnegative_number(
                wrapper["latency"].get("wall_ms"), "latency.wall_ms"
            ),
            "judge_reason": side["reason"],
        }
        return view

    g0_view = system_view(g0, decision["g0"])
    g1_view = system_view(g1, decision["g1"])
    g0_view["citation_count"] = len(citation_ids(g0.get("answer")))
    g0_view["citations_empty"] = g0_view["citation_count"] == 0
    g0_view.update(
        {
            "citation_validity": "N/A",
            "citation_coverage": "N/A",
            "faithfulness": "N/A",
            "context_precision": "N/A",
            "context_query_hit": "N/A",
            "context_fully_judged": "N/A",
        }
    )
    deterministic_citations = citation_validity(g1.get("answer"), evidence["passage_ids"])
    retrieval = context_retrieval_metrics(evidence["passage_ids"], qrels)
    g1_view.update(
        {
            "citation_validity": deterministic_citations["score"],
            "citation_coverage": float(decision["g1"]["citation_coverage"]),
            "faithfulness": float(decision["g1"]["faithfulness"]),
            "context_precision": retrieval["context_precision"],
            "context_query_hit": retrieval["context_query_hit"],
            "context_fully_judged": retrieval["fully_judged"],
            "citation_audit": deterministic_citations,
            "context_audit": retrieval,
            "provided_passage_ids": list(evidence["passage_ids"]),
        }
    )
    task_delta = int(g1_view["task_success"]) - int(g0_view["task_success"])
    component_delta = sum(
        int(g1_view[field]) - int(g0_view[field])
        for field in (
            "root_cause_correct",
            "sql_repair_correct",
            "dialect_compatible",
            "version_compatible",
        )
    )
    return {
        "schema_version": "sqlmend-generation-paired-query-v1",
        "query_id": query_id,
        "ordinal": ordinal,
        "judge_status": judgment["status"],
        "judge_attempt_count": judgment["attempt_count"],
        "judge_retry_count": judgment["retry_count"],
        "g0": g0_view,
        "g1": g1_view,
        "paired": {
            "task_success_delta": task_delta,
            "semantic_component_delta": component_delta,
            "answer_relevance_delta": (
                g1_view["answer_relevance"] - g0_view["answer_relevance"]
            ),
            "outcome": (
                "g1_improved"
                if task_delta > 0
                else "g1_regressed"
                if task_delta < 0
                else "tied"
            ),
            "outcome_basis": "offline_task_success",
        },
    }


def _build_acceptance(
    overall: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    g0 = overall["systems"]["g0"]
    g1 = overall["systems"]["g1"]
    paired = overall["paired"]
    engineering_checks = {
        "g0_has_250_formal_results": g0["formal_result_count"] == EXPECTED_QUERY_COUNT,
        "g1_has_250_formal_results": g1["formal_result_count"] == EXPECTED_QUERY_COUNT,
        "all_queries_have_judgment_records": overall["judge"]["logical_query_count"]
        == EXPECTED_QUERY_COUNT,
        "all_250_judgments_succeeded": overall["judge"]["completed_count"]
        == EXPECTED_QUERY_COUNT
        and overall["judge"]["failed_count"] == 0,
        "all_250_judge_calls_succeeded": overall["judge"]["completed_count"]
        == EXPECTED_QUERY_COUNT
        and overall["judge"]["failed_count"] == 0,
        "g0_structured_output_validity_at_least_98pct": g0[
            "structured_output_validity"
        ]
        >= 0.98,
        "g1_structured_output_validity_at_least_98pct": g1[
            "structured_output_validity"
        ]
        >= 0.98,
    }
    integrity_checks = {
        "sealed_before_reference_access": True,
        "paired_query_ids_identical": True,
        "g0_received_no_evidence": True,
        "g1_citation_validity_is_100pct": g1["citation_validity"] == 1.0,
        "g1_context_is_fully_qrels_judged": all(
            row["g1"]["context_fully_judged"] is True for row in rows
        ),
        "g0_citations_are_empty": all(row["g0"]["citations_empty"] for row in rows),
    }
    quality_checks = {
        "g1_task_success_improves_by_at_least_10pp": paired[
            "task_success_absolute_delta"
        ]
        >= 0.10,
        "g1_dialect_compatibility_not_below_g0": g1["dialect_compatibility"]
        >= g0["dialect_compatibility"],
        "g1_version_compatibility_not_below_g0": g1["version_compatibility"]
        >= g0["version_compatibility"],
        "g1_root_cause_accuracy_not_below_g0": g1["root_cause_accuracy"]
        >= g0["root_cause_accuracy"],
        "g1_sql_repair_correctness_not_below_g0": g1["sql_repair_correctness"]
        >= g0["sql_repair_correctness"],
    }
    engineering_pass = all(engineering_checks.values())
    integrity_pass = all(integrity_checks.values())
    quality_pass = all(quality_checks.values())
    return {
        "schema_version": "sqlmend-generation-acceptance-v1",
        "evaluation_label": EVALUATION_LABEL,
        "engineering": {
            "status": "PASS" if engineering_pass else "FAIL",
            "checks": engineering_checks,
        },
        "integrity": {
            "status": "PASS" if integrity_pass else "FAIL",
            "checks": integrity_checks,
        },
        "quality": {
            "status": "PASS" if quality_pass else "FAIL",
            "checks": quality_checks,
        },
        "artifact_validation_status": (
            "PASS" if engineering_pass and integrity_pass else "FAIL"
        ),
        "phase_success": engineering_pass and integrity_pass and quality_pass,
        "quality_failure_does_not_suppress_artifacts": True,
    }


def _load_resumable_judgments(
    path: Path,
    run_sha: Mapping[str, str],
    evaluation_input_sha256: Mapping[str, str],
    evaluation_context_sha256: str,
    policy_hash: str,
    max_attempts: int,
) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        query_id = _identifier(row.get("query_id"), "judgment query_id")
        if query_id in result:
            raise ValueError(f"duplicate query in judgment journal: {query_id}")
        if row.get("run_sha256") != dict(run_sha):
            raise ValueError("judgment journal belongs to different generation run seals")
        if row.get("evaluation_input_sha256") != dict(evaluation_input_sha256):
            raise ValueError("judgment journal offline input SHA mismatch")
        if row.get("evaluation_context_sha256") != evaluation_context_sha256:
            raise ValueError("judgment journal offline context SHA mismatch")
        if row.get("status") not in {"success", "failed"}:
            raise ValueError(f"invalid judgment status for {query_id}")
        if row.get("policy_sha256") != policy_hash:
            raise ValueError("judgment journal policy mismatch")
        result[query_id] = _normalize_resumable_attempts(
            row,
            query_id=query_id,
            max_attempts=max_attempts,
        )
    return result


def _normalize_resumable_attempts(
    row: Mapping[str, Any],
    *,
    query_id: str,
    max_attempts: int,
) -> dict[str, Any]:
    attempts = row.get("attempts")
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= max_attempts:
        raise ValueError(
            f"judgment attempts must contain 1..{max_attempts} records: {query_id}"
        )
    normalized_attempts: list[dict[str, Any]] = []
    for expected_number, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, Mapping):
            raise ValueError(f"judgment attempt must be an object: {query_id}")
        saved_number = attempt.get("attempt")
        if (
            not isinstance(saved_number, int)
            or isinstance(saved_number, bool)
            or saved_number != expected_number
        ):
            raise ValueError(f"judgment attempt sequence mismatch: {query_id}")
        attempt_status = attempt.get("status")
        if attempt_status not in {"success", "failed"}:
            raise ValueError(f"judgment attempt status is invalid: {query_id}")
        if expected_number < len(attempts) and attempt_status != "failed":
            raise ValueError(f"judgment has a non-final successful attempt: {query_id}")
        normalized_attempts.append(dict(attempt))

    status = row.get("status")
    if normalized_attempts[-1]["status"] != status:
        raise ValueError(f"judgment final attempt status mismatch: {query_id}")
    if status == "failed" and len(normalized_attempts) != max_attempts:
        raise ValueError(
            f"failed judgment did not exhaust {max_attempts} attempts: {query_id}"
        )

    attempt_count = len(normalized_attempts)
    retry_count = attempt_count - 1
    saved_attempt_count = row.get("attempt_count")
    if (
        not isinstance(saved_attempt_count, int)
        or isinstance(saved_attempt_count, bool)
        or saved_attempt_count != attempt_count
    ):
        raise ValueError(f"judgment attempt_count differs from attempts: {query_id}")
    saved_retry_count = row.get("retry_count")
    if (
        not isinstance(saved_retry_count, int)
        or isinstance(saved_retry_count, bool)
        or saved_retry_count != retry_count
    ):
        raise ValueError(f"judgment retry_count differs from attempts: {query_id}")

    normalized = dict(row)
    normalized["attempts"] = normalized_attempts
    # Derived counters are always reconstructed from the durable attempt list;
    # the equality checks above prevent silently accepting stale saved values.
    normalized["attempt_count"] = attempt_count
    normalized["retry_count"] = retry_count
    return normalized


def _validate_resumed_assignment(
    row: Mapping[str, Any],
    ordinal: int,
    run_sha: Mapping[str, str],
    policy_hash: str,
    evaluation_input_sha256: Mapping[str, str],
    evaluation_context_sha256: str,
    prompt_sha256: str,
) -> None:
    if row.get("ordinal") != ordinal:
        raise ValueError("resumed judgment ordinal does not match current query ordering")
    if row.get("assignment") != _counterbalanced_assignment(ordinal):
        raise ValueError("resumed judgment counterbalance assignment mismatch")
    if row.get("run_sha256") != dict(run_sha):
        raise ValueError("resumed judgment run SHA mismatch")
    if row.get("policy_sha256") != policy_hash:
        raise ValueError("resumed judgment policy mismatch")
    if row.get("evaluation_input_sha256") != dict(evaluation_input_sha256):
        raise ValueError("resumed judgment offline input SHA mismatch")
    if row.get("evaluation_context_sha256") != evaluation_context_sha256:
        raise ValueError("resumed judgment offline context SHA mismatch")
    if row.get("prompt_sha256") != prompt_sha256:
        raise ValueError("resumed judgment prompt SHA mismatch")


def _validate_optional_overall_response_schema(path: Path) -> None:
    """Bind a completed resume to the saved schema when overall exists.

    The frozen judgment-v1 row contract has no per-row response-schema or
    evaluator-semantics digest.  Adding either as a mandatory field would make
    the currently in-flight journal incompatible.  Prompt SHA, policy SHA, and
    this optional already-published overall schema SHA are therefore the
    strongest backwards-compatible bindings available in v1.
    """

    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("existing overall metrics cannot be read") from error
    if not isinstance(payload, Mapping) or not isinstance(payload.get("judge"), Mapping):
        raise ValueError("existing overall metrics lacks judge provenance")
    recorded = payload["judge"].get("response_schema_sha256")
    if recorded != _canonical_sha256(JUDGE_RESPONSE_SCHEMA):
        raise ValueError("existing overall judge response schema SHA mismatch")


def _model_identity_from_resumed(
    judgments: Mapping[str, Mapping[str, Any]],
    policy: JudgePolicy,
) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    for query_id in sorted(judgments, key=_query_sort_key):
        row = judgments[query_id]
        if (
            row.get("model_tag") != policy.model
            or row.get("model_digest") != policy.model_digest
            or row.get("think") is not False
        ):
            raise ValueError(f"resumed judgment model identity mismatch: {query_id}")
        identity = row.get("model_identity")
        if not isinstance(identity, Mapping):
            raise ValueError(f"resumed judgment lacks model identity: {query_id}")
        normalized = dict(identity)
        if (
            normalized.get("model_tag") != policy.model
            or normalized.get("model_digest") != policy.model_digest
            or normalized.get("think") is not False
            or normalized.get("thinking_disabled") is not True
        ):
            raise ValueError(f"resumed preflight identity mismatch: {query_id}")
        identities.append(normalized)
    if not identities:  # pragma: no cover - complete journal guarantees rows
        raise ValueError("resumed judgment model identity is unavailable")
    # Ollama version/preflight metadata may legitimately differ across a
    # multi-invocation resume; tag, digest, and think were checked per row.
    return identities[0]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                raise ValueError(f"blank JSONL line at {path}:{line_number}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON at {path}:{line_number}: {error}") from error
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
            rows.append(row)
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _path_member(value: Any, *names: str) -> Path | None:
    for name in names:
        candidate: Any = None
        if isinstance(value, Mapping):
            candidate = value.get(name)
        elif value is not None and not isinstance(value, (str, os.PathLike)):
            candidate = getattr(value, name, None)
        if candidate is not None:
            return Path(candidate)
    return None


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} must be a non-empty trimmed string")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not (float("-inf") < result < float("inf")):
        raise ValueError(f"{field} must be finite")
    return result


def _nonnegative_number(value: Any, field: str) -> float:
    result = _number(value, field)
    if result < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return result


def _bounded_score(value: Any, field: str) -> float:
    result = _number(value, field)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be in [0, 1]")
    return result


def _query_sort_key(query_id: str) -> tuple[str, int, str]:
    match = re.match(r"^(.*?)(\d+)$", query_id)
    if match is None:
        return (query_id, -1, query_id)
    return (match.group(1), int(match.group(2)), query_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "EVALUATION_LABEL",
    "EXPECTED_QUERY_COUNT",
    "G0_SYSTEM_ID",
    "G1_SYSTEM_ID",
    "JUDGE_MODEL",
    "JUDGE_MODEL_DIGEST",
    "JUDGE_POLICY_DEFAULTS",
    "JUDGE_RESPONSE_SCHEMA",
    "JudgePolicy",
    "OfflineEvaluationPaths",
    "OllamaJudgeClient",
    "run_offline_evaluation",
]
