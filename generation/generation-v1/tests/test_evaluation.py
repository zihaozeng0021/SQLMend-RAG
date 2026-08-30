from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import sqlmend_generation_v1.evaluation as evaluation
from sqlmend_generation_v1.evaluation import (
    EXPECTED_QUERY_COUNT,
    BASELINE_SYSTEM_ID,
    GENERATION_V1_SYSTEM_ID,
    run_offline_evaluation,
)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _side() -> dict[str, Any]:
    return {
        "root_cause_correct": True,
        "sql_repair_correct": True,
        "dialect_compatible": True,
        "version_compatible": True,
        "answer_relevance": 0.9,
        "faithfulness": 0.8,
        "citation_coverage": 0.8,
        "reason": "The diagnosis and compatible repair match the reference.",
    }


class FakeJudge:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.prompts.append(kwargs["prompt"])
        assert kwargs["model"] == "qwen3.5:4b"
        assert kwargs["think"] is False
        assert kwargs["options"]["seed"] == 20260831
        return {"A": _side(), "B": _side()}


def _wrapper(query_id: str, system_id: str, *, failed: bool = False) -> dict[str, Any]:
    number = int(query_id.removeprefix("DEV"))
    evidence_ids = [f"{query_id}-p{rank}" for rank in range(1, 6)]
    serialized_query = f"Dialect: sqlite\n\nVersion: 3.46\n\nQuestion:\nFix {query_id}\n\nSQL:\nSELECT 1;"
    answer = None
    if not failed:
        answer = {
            "diagnosis": "diagnosis",
            "root_cause": "root cause",
            "corrected_sql": "SELECT 1;",
            "explanation": "explanation",
            "dialect_version_compatibility": "compatible",
            "confidence": 0.9,
            "citations": [evidence_ids[0]] if system_id == GENERATION_V1_SYSTEM_ID else [],
        }
    return {
        "schema_version": "sqlmend-generation-record-v1",
        "experiment_id": "phase10-dev250",
        "query_id": query_id,
        "system_id": system_id,
        "status": "failed" if failed else "success",
        "answer": answer,
        "structured_output_valid": not failed,
        "contract_valid": not failed,
        "validation_errors": [] if not failed else ["model call failed"],
        "failure": {"type": "transport"} if failed else None,
        "input_provenance": {
            "serialized_query_sha256": hashlib.sha256(
                serialized_query.encode("utf-8")
            ).hexdigest(),
            "evidence_sha256": "e" * 64 if system_id == GENERATION_V1_SYSTEM_ID else None,
            "evidence_passage_ids": evidence_ids if system_id == GENERATION_V1_SYSTEM_ID else [],
            "prompt_sha256": "p" * 64,
        },
        "generation_provenance": {
            "model_tag": "qwen3.5:4b",
            "model_digest": "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd",
            "think": False,
            "attempt_count": 1,
            "retry_count": 0,
            "attempts": [
                {
                    "attempt_number": 1,
                    "status": "failed" if failed else "success",
                }
            ],
        },
        "latency": {"wall_ms": float(1000 + number)},
    }


def _build_fixture(root: Path, *, one_baseline_failure: bool = True) -> None:
    release = root / "generation" / "generation-v1"
    config = release / "config/generation.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """schema_version: sqlmend-generation-config-v1
ollama:
  base_url: http://127.0.0.1:11434
  model_tag: qwen3.5:4b
  model_digest: 2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd
  think: false
  timeout_seconds: 300
  options:
    temperature: 0.0
    seed: 20260830
    num_ctx: 16384
    num_predict: 1024
    top_k: 40
    top_p: 0.9
    repeat_penalty: 1.0
retry_policy:
  max_attempts: 3
offline_judge:
  inherit_generation_model: true
  seed: 20260831
  temperature: 0.0
  top_p: 1.0
  top_k: 40
  num_ctx: 16384
  num_predict: 1200
  repeat_penalty: 1.0
  max_attempts: 3
  timeout_seconds: 300
""",
        encoding="utf-8",
    )
    baseline_rows: list[dict[str, Any]] = []
    generation_v1_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    safe_query_rows: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    qrel_lines: list[str] = []
    for number in range(1, EXPECTED_QUERY_COUNT + 1):
        query_id = f"DEV{number:04d}"
        baseline_rows.append(
            _wrapper(query_id, BASELINE_SYSTEM_ID, failed=one_baseline_failure and number == 1)
        )
        generation_v1_rows.append(_wrapper(query_id, GENERATION_V1_SYSTEM_ID))
        serialized_query = f"Dialect: sqlite\n\nVersion: 3.46\n\nQuestion:\nFix {query_id}\n\nSQL:\nSELECT 1;"
        safe_query_rows.append(
            {
                "schema_version": "sqlmend-online-query-v1",
                "query_id": query_id,
                "source_fields_used": ["dialect", "version", "user_problem", "sql"],
                "serialized_text": serialized_query,
                "serialized_text_sha256": hashlib.sha256(
                    serialized_query.encode("utf-8")
                ).hexdigest(),
                "serializer_version": "sqlmend-query-v1",
            }
        )
        passages = []
        for rank in range(1, 6):
            passage_id = f"{query_id}-p{rank}"
            passages.append(
                {
                    "passage_id": passage_id,
                    "rank": rank,
                    "score": 1.0 / rank,
                    "dialect": "sqlite",
                    "version": "3.46",
                    "source_name": "official docs",
                    "text": f"Evidence {rank} for {query_id}",
                }
            )
            qrel_lines.append(f"{query_id} 0 {passage_id} 1\n")
        evidence_rows.append(
            {
                "query_id": query_id,
                "passages": passages,
                "evidence_sha256": "e" * 64,
            }
        )
        references.append(
            {
                "query_id": query_id,
                "root_cause": "root cause",
                "reference_fix_sql": "SELECT 1;",
                "reference_explanation": "explanation",
                "expected_behavior": "one row",
                "dialect": "sqlite",
                "version": "3.46",
                "schema_context": None,
                "verification": {"status": "passed"},
                # Forbidden/irrelevant raw fields are projected away by loader.
                "evidence": [{"chunk_id": "must-not-enter-online-generation"}],
            }
        )
    _write_jsonl(root / "generation/baseline/runs/baseline_closed_book_dev250.jsonl", baseline_rows)
    _write_jsonl(release / "runs/generation_v1_rag_dev250.jsonl", generation_v1_rows)
    _write_jsonl(release / "prepared_inputs/generation_v1_evidence_top5.jsonl", evidence_rows)
    _write_jsonl(release / "prepared_inputs/online_queries.jsonl", safe_query_rows)
    _write_jsonl(root / "annotation/codex/dev_250.jsonl", references)
    qrels = root / "retrieval/baseline/qrels/qrels_effective_dev250.trec"
    qrels.parent.mkdir(parents=True, exist_ok=True)
    qrels.write_text("".join(qrel_lines), encoding="utf-8")


def test_evaluate_seals_before_references_and_keeps_all_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _build_fixture(root)
    seal_path = root / "generation/generation-v1/evaluation/generation_seal.json"
    real_loader = evaluation._load_references
    observed_initial_seal: dict[str, Any] = {}

    def guarded_loader(path: Path) -> dict[str, dict[str, Any]]:
        assert seal_path.is_file(), "reference opened before run seals were written"
        observed_initial_seal.update(json.loads(seal_path.read_text(encoding="utf-8")))
        assert observed_initial_seal["reference_access"][
            "seal_written_before_reference_access"
        ] is False
        return real_loader(path)

    monkeypatch.setattr(evaluation, "_load_references", guarded_loader)
    judge = FakeJudge()
    overall = run_offline_evaluation(root, client=judge, resume=False)

    assert len(judge.prompts) == EXPECTED_QUERY_COUNT
    assert all("baseline" not in prompt for prompt in judge.prompts)
    assert all("generation_v1" not in prompt for prompt in judge.prompts)
    assert overall["formal_answer_count"] == 500
    assert overall["formal_result_wrapper_count"] == 500
    assert overall["systems"][BASELINE_SYSTEM_ID]["failure_count"] == 1
    assert overall["systems"][BASELINE_SYSTEM_ID]["task_success_rate"] == pytest.approx(249 / 250)
    assert overall["systems"][GENERATION_V1_SYSTEM_ID]["task_success_rate"] == 1.0
    assert overall["systems"][GENERATION_V1_SYSTEM_ID]["citation_validity"] == 1.0
    assert overall["systems"][GENERATION_V1_SYSTEM_ID]["context_precision"] == 1.0
    assert overall["systems"][BASELINE_SYSTEM_ID]["faithfulness"] == "N/A"
    assert overall["judge"]["counterbalance"]["a_baseline_count"] == 125
    assert overall["judge"]["counterbalance"]["a_generation_v1_count"] == 125
    assert overall["judge"]["completed_count"] == 250
    assert overall["judge"]["judge_call_success_count"] == 250
    assert overall["judge"]["failed_count"] == 0
    assert overall["acceptance"]["engineering"]["checks"][
        "all_250_judgments_succeeded"
    ] is True
    assert overall["acceptance"]["engineering"]["checks"][
        "all_250_judge_calls_succeeded"
    ] is True

    final_seal = json.loads(seal_path.read_text(encoding="utf-8"))
    assert final_seal["reference_access"]["seal_written_before_reference_access"] is True
    assert len(final_seal["offline_evaluation_inputs"]["context_sha256"]) == 64
    for system in (BASELINE_SYSTEM_ID, GENERATION_V1_SYSTEM_ID):
        run_path = root / final_seal["runs"][system]["path"]
        expected_sha = hashlib.sha256(run_path.read_bytes()).hexdigest()
        assert final_seal["runs"][system]["sha256"] == expected_sha
        assert final_seal["runs"][system]["record_count"] == 250

    evaluation_dir = root / "generation/generation-v1/evaluation"
    assert len((evaluation_dir / "judgments.jsonl").read_text().splitlines()) == 250
    first_judgment = json.loads(
        (evaluation_dir / "judgments.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert first_judgment["evaluation_input_sha256"] == final_seal[
        "offline_evaluation_inputs"
    ]["sha256"]
    assert first_judgment["evaluation_context_sha256"] == final_seal[
        "offline_evaluation_inputs"
    ]["context_sha256"]
    assert len((evaluation_dir / "per_query_comparison.jsonl").read_text().splitlines()) == 250
    assert (evaluation_dir / "overall_metrics.json").is_file()
    assert (evaluation_dir / "acceptance.json").is_file()
    assert (root / "generation/generation-v1/reports/generation_v1_report.md").is_file()


def test_resume_does_not_rejudge_completed_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _build_fixture(root, one_baseline_failure=False)
    first = FakeJudge()
    run_offline_evaluation(root, client=first, resume=False)
    assert len(first.prompts) == 250

    class ExplodingJudge:
        def preflight(self, *_: Any) -> Any:
            raise AssertionError("complete journal must not require Ollama preflight")

        def __call__(self, **_: Any) -> Any:
            raise AssertionError("completed resumed query must not be judged again")

    second = run_offline_evaluation(root, client=ExplodingJudge(), resume=True)
    assert second["judge"]["completed_count"] == 250
    judgments = root / "generation/generation-v1/evaluation/judgments.jsonl"
    assert len(judgments.read_text(encoding="utf-8").splitlines()) == 250

    original_prompt = evaluation._judge_prompt

    def changed_prompt(**kwargs: Any) -> str:
        return original_prompt(**kwargs) + "\nchanged evaluator prompt"

    monkeypatch.setattr(evaluation, "_judge_prompt", changed_prompt)
    with pytest.raises(ValueError, match="prompt SHA mismatch"):
        run_offline_evaluation(root, client=ExplodingJudge(), resume=True)
    monkeypatch.setattr(evaluation, "_judge_prompt", original_prompt)

    original_schema = evaluation.JUDGE_RESPONSE_SCHEMA
    changed_schema = {**original_schema, "description": "changed response schema"}
    monkeypatch.setattr(evaluation, "JUDGE_RESPONSE_SCHEMA", changed_schema)
    with pytest.raises(ValueError, match="response schema SHA mismatch"):
        run_offline_evaluation(root, client=ExplodingJudge(), resume=True)
    monkeypatch.setattr(evaluation, "JUDGE_RESPONSE_SCHEMA", original_schema)

    references_path = root / "annotation/codex/dev_250.jsonl"
    references = [
        json.loads(line)
        for line in references_path.read_text(encoding="utf-8").splitlines()
    ]
    references[0]["reference_explanation"] = "changed after the first evaluation"
    _write_jsonl(references_path, references)
    with pytest.raises(ValueError, match="offline input SHA mismatch"):
        run_offline_evaluation(root, client=ExplodingJudge(), resume=True)


def test_incomplete_resume_still_requires_preflight_and_only_judges_missing_query(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _build_fixture(root, one_baseline_failure=False)
    run_offline_evaluation(root, client=FakeJudge(), resume=False)
    judgments_path = root / "generation/generation-v1/evaluation/judgments.jsonl"
    rows = judgments_path.read_text(encoding="utf-8").splitlines()
    judgments_path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    class TrackingJudge(FakeJudge):
        def __init__(self) -> None:
            super().__init__()
            self.preflight_calls = 0

        def preflight(self, expected_tag: str, expected_digest: str) -> dict[str, Any]:
            self.preflight_calls += 1
            return {
                "model_tag": expected_tag,
                "model_digest": expected_digest,
                "ollama_version": "fixture",
            }

    judge = TrackingJudge()
    overall = run_offline_evaluation(root, client=judge, resume=True)
    assert judge.preflight_calls == 1
    assert len(judge.prompts) == 1
    assert overall["judge"]["completed_count"] == 250


def _resume_judgment(
    *,
    status: str,
    attempts: list[Any],
    attempt_count: int,
    retry_count: int,
) -> dict[str, Any]:
    return {
        "query_id": "DEV0001",
        "status": status,
        "run_sha256": {BASELINE_SYSTEM_ID: "baseline-sha", GENERATION_V1_SYSTEM_ID: "generation-v1-sha"},
        "evaluation_input_sha256": {"fixture": "input-sha"},
        "evaluation_context_sha256": "context-sha",
        "policy_sha256": "policy-sha",
        "attempt_count": attempt_count,
        "retry_count": retry_count,
        "attempts": attempts,
    }


def _load_resume_fixture(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    _write_jsonl(path, [row])
    return evaluation._load_resumable_judgments(
        path,
        {BASELINE_SYSTEM_ID: "baseline-sha", GENERATION_V1_SYSTEM_ID: "generation-v1-sha"},
        {"fixture": "input-sha"},
        "context-sha",
        "policy-sha",
        3,
    )["DEV0001"]


def test_resume_loader_recomputes_attempt_and_retry_counts(tmp_path: Path) -> None:
    row = _resume_judgment(
        status="success",
        attempts=[
            {"attempt": 1, "status": "failed"},
            {"attempt": 2, "status": "success"},
        ],
        attempt_count=2,
        retry_count=1,
    )
    loaded = _load_resume_fixture(tmp_path / "judgments.jsonl", row)
    assert loaded["attempt_count"] == len(loaded["attempts"]) == 2
    assert loaded["retry_count"] == len(loaded["attempts"]) - 1 == 1


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            _resume_judgment(
                status="success", attempts=[], attempt_count=0, retry_count=0
            ),
            "1..3",
        ),
        (
            _resume_judgment(
                status="success", attempts=[None], attempt_count=1, retry_count=0
            ),
            "must be an object",
        ),
        (
            _resume_judgment(
                status="success",
                attempts=[{"attempt": 2, "status": "success"}],
                attempt_count=1,
                retry_count=0,
            ),
            "sequence mismatch",
        ),
        (
            _resume_judgment(
                status="success",
                attempts=[{"attempt": 1, "status": "unknown"}],
                attempt_count=1,
                retry_count=0,
            ),
            "attempt status is invalid",
        ),
        (
            _resume_judgment(
                status="success",
                attempts=[
                    {"attempt": 1, "status": "success"},
                    {"attempt": 2, "status": "success"},
                ],
                attempt_count=2,
                retry_count=1,
            ),
            "non-final successful attempt",
        ),
        (
            _resume_judgment(
                status="success",
                attempts=[{"attempt": 1, "status": "failed"}],
                attempt_count=1,
                retry_count=0,
            ),
            "final attempt status mismatch",
        ),
        (
            _resume_judgment(
                status="success",
                attempts=[{"attempt": 1, "status": "success"}],
                attempt_count=2,
                retry_count=0,
            ),
            "attempt_count differs",
        ),
        (
            _resume_judgment(
                status="success",
                attempts=[{"attempt": 1, "status": "success"}],
                attempt_count=1,
                retry_count=1,
            ),
            "retry_count differs",
        ),
    ],
)
def test_resume_loader_rejects_malformed_attempt_derivations(
    tmp_path: Path,
    row: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _load_resume_fixture(tmp_path / "judgments.jsonl", row)


def test_failed_resume_judgment_is_terminal_only_after_retry_exhaustion(
    tmp_path: Path,
) -> None:
    exhausted = _resume_judgment(
        status="failed",
        attempts=[
            {"attempt": number, "status": "failed"} for number in range(1, 4)
        ],
        attempt_count=3,
        retry_count=2,
    )
    loaded = _load_resume_fixture(tmp_path / "judgments.jsonl", exhausted)
    assert loaded["status"] == "failed"
    assert loaded["attempt_count"] == 3

    not_exhausted = _resume_judgment(
        status="failed",
        attempts=[{"attempt": 1, "status": "failed"}],
        attempt_count=1,
        retry_count=0,
    )
    with pytest.raises(ValueError, match="did not exhaust 3 attempts"):
        _load_resume_fixture(tmp_path / "not_exhausted.jsonl", not_exhausted)


def test_judge_success_is_an_engineering_acceptance_gate() -> None:
    overall = {
        "judge": {
            "logical_query_count": 250,
            "completed_count": 249,
            "failed_count": 1,
        },
        "systems": {
            BASELINE_SYSTEM_ID: {
                "formal_result_count": 250,
                "structured_output_validity": 1.0,
                "citation_validity": "N/A",
                "dialect_compatibility": 1.0,
                "version_compatibility": 1.0,
                "root_cause_accuracy": 1.0,
                "sql_repair_correctness": 1.0,
            },
            GENERATION_V1_SYSTEM_ID: {
                "formal_result_count": 250,
                "structured_output_validity": 1.0,
                "citation_validity": 1.0,
                "dialect_compatibility": 1.0,
                "version_compatibility": 1.0,
                "root_cause_accuracy": 1.0,
                "sql_repair_correctness": 1.0,
            },
        },
        "paired": {"task_success_absolute_delta": 0.1},
    }
    acceptance = evaluation._build_acceptance(overall, [])
    assert acceptance["engineering"]["checks"][
        "all_250_judgments_succeeded"
    ] is False
    assert acceptance["engineering"]["checks"][
        "all_250_judge_calls_succeeded"
    ] is False
    assert acceptance["engineering"]["status"] == "FAIL"
    assert acceptance["artifact_validation_status"] == "FAIL"


def test_invalid_run_blocks_before_reference_or_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    _build_fixture(root)
    baseline_path = root / "generation/baseline/runs/baseline_closed_book_dev250.jsonl"
    rows = [json.loads(line) for line in baseline_path.read_text().splitlines()]
    rows.pop()
    _write_jsonl(baseline_path, rows)

    called = False

    def forbidden_reference_load(_: Path) -> dict[str, dict[str, Any]]:
        nonlocal called
        called = True
        raise AssertionError("reference loader must not run")

    monkeypatch.setattr(evaluation, "_load_references", forbidden_reference_load)
    with pytest.raises(ValueError, match="exactly 250"):
        run_offline_evaluation(root, client=FakeJudge(), resume=False)
    assert called is False
    assert not (
        root / "generation/generation-v1/evaluation/generation_seal.json"
    ).exists()
