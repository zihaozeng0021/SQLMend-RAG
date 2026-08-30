# SQLMend-RAG Generation v1

Generation v1 is the Phase 10, machine-proposed development comparison between
two SQL debugging systems:

- `g0_closed_book`: Ollama `qwen3.5:4b` receives only the frozen,
  user-observable serialized query.
- `g1_retrieval_v1_rag`: the same model, prompt, output schema, reasoning
  effort, decoding parameters, and retry policy additionally receive the
  frozen Retrieval-v1 Final system's actual Top-5 passages.

The 250 cases and all reference judgments in this release are
**machine-proposed development data**. They are not human gold data and are
not the assignment's final held-out test set.

## Frozen experiment contract

The online path consumes only these byte-bound inputs:

| Input | Path | SHA-256 |
|---|---|---|
| Safe queries | `retrieval/retrieval-v1/serialized_queries/dev_250_queries.jsonl` | `e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c` |
| Retrieval-v1 Final run | `retrieval/retrieval-v1/runs/hybrid_rrf_dialect_version_lexical_rerank_dev250.trec` | `774d2d1c90e8e8d58479130a9e016e8a4699cd9ff4b8f72dbf95a3b6f49be566` |
| Frozen corpus | `construction/data/processed/corpus.jsonl` | `279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3` |

The generator does not open `annotation/codex/dev_250.jsonl`, qrels,
candidate-pool labels, annotation evidence, or Retrieval-v1 evaluation files.
It first prepares a safe query artifact and a separate G1 evidence bundle.
The G0 command never opens that evidence bundle. Reference and qrels files are
loaded only by the offline evaluator after both 250-row formal runs have been
completed and sealed by SHA-256.

Top-K is fixed at 5 before generation. Retrieval v1 reports its direct-hit and
compatibility measures at 5, and Top-5 materially reduces prompt length and
irrelevant context compared with Top-10. The post-generation evaluation still
reports the true machine-judged Context Precision@5; no per-query labels are
used to select passages.

The exact local model is:

```text
tag=qwen3.5:4b
digest=2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd
architecture=qwen35
parameters=4.7B
quantization=Q4_K_M
reasoning=false
```

The model is invoked with Ollama `think=false`; a local smoke test confirmed
that no thinking field is produced. Generation uses `temperature=0`,
`seed=20260830`, `num_ctx=16384`,
`num_predict=1024`, `top_k=40`, `top_p=0.9`, and
`repeat_penalty=1.0`. Requests are non-streaming and use the same strict JSON
schema for both systems. The retry policy permits at most three identical
contract attempts for transport or HTTP failures, and fixed non-answer-bearing
corrective retries for JSON/schema or citation-contract failures. Every
attempt and final failure is retained; no case may be dropped.

`status=success` in a formal generation wrapper means only that Ollama
ultimately returned an answer satisfying the JSON/schema and citation
contracts. It does **not** mean the diagnosis or repair is correct. Semantic
success is computed later by the sealed offline evaluation; `Task Success`
requires a correct root cause, an acceptable SQL repair, and both dialect and
version compatibility.

Machine-readable count semantics are explicit and legacy names are retained
only for compatibility:

| Field | Meaning |
|---|---|
| `success_count` / `generation_contract_success_count` | Formal generation wrappers that passed JSON/schema/citation contracts |
| `generation_attempt_count` / `generation_retry_count` | Attempts and retries independently recomputed from every wrapper's retained `attempts` array |
| `structured_output_validity` | Final model responses that were valid JSON and matched the answer schema; citation validity is separate |
| `both_succeeded_count` / `both_task_success_count` | Queries where both systems achieved offline Task Success |
| judge `completed_count` / `judge_call_success_count` | Judge calls that returned valid decisions, not answers judged correct |
| `formal_answer_count` / `formal_result_wrapper_count` | All formal wrappers, including explicit failures whose `answer` is null |

CLI `status=PASS` is scoped to command completion. The final `validate` result
separately reports engineering status, quality status, and overall Phase 10
success.

## Output contract

Both systems return the same strict object:

- `diagnosis`
- `root_cause`
- `corrected_sql`
- `explanation`
- dialect compatibility status and explanation
- version compatibility status and explanation
- `confidence`
- `insufficient_evidence`
- `citations`

G0 citations must be empty. G1 citations must be a subset of that query's five
actually supplied passage IDs. Passage text and safe source metadata are kept
in `prepared_inputs/g1_evidence_top5.jsonl`; every formal answer records its
input, prompt, schema, request, model, response, attempt, token, and latency
provenance.

## Rebuild and run

From the repository root on Python 3.11+:

```powershell
python -m venv .venv-generation-v1
.\.venv-generation-v1\Scripts\python.exe -m pip install --upgrade pip
.\.venv-generation-v1\Scripts\python.exe -m pip install -r generation\generation-v1\requirements.txt
.\.venv-generation-v1\Scripts\python.exe -m pip install -e generation\generation-v1 --no-deps
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv-generation-v1\Scripts\python.exe -m sqlmend_generation_v1.cli --root . all --clean
```

The equivalent auditable sequence is:

```powershell
python -m sqlmend_generation_v1.cli --root . clean
python -m sqlmend_generation_v1.cli --root . audit-protected-paths --phase before
python -m sqlmend_generation_v1.cli --root . verify-inputs
python -m sqlmend_generation_v1.cli --root . inspect-model
python -m sqlmend_generation_v1.cli --root . prepare
python -m sqlmend_generation_v1.cli --root . generate --system g0_closed_book
python -m sqlmend_generation_v1.cli --root . generate --system g1_retrieval_v1_rag
python -m sqlmend_generation_v1.cli --root . evaluate
python -m sqlmend_generation_v1.cli --root . report
python -m sqlmend_generation_v1.cli --root . test
python -m sqlmend_generation_v1.cli --root . audit-protected-paths --phase after
python -m sqlmend_generation_v1.cli --root . audit-protected-paths --phase current
python -m sqlmend_generation_v1.cli --root . finalize
python -m sqlmend_generation_v1.cli --root . validate
```

Generation and judging checkpoint one canonical JSONL record at a time and
resume by query ID. `clean` is intentionally destructive only within the
allowlisted generated directories under `generation/generation-v1/`; retain a
copy of formal results before using it outside a rebuild.

## Evaluation definitions

All primary rates use all 250 cases as their denominator. A failed generation
scores zero on semantic answer metrics; it is never removed. Structured Output
Validity is the narrower deterministic JSON/schema-shape rate, while
Generation Contract Success additionally requires the system-specific citation
contract. The offline semantic judge sees anonymous, counterbalanced answers
and the same reference packet for both systems. It accepts semantically
equivalent SQL rather than requiring string identity. Task Success is
recomputed as the conjunction of correct root cause, acceptable SQL repair,
dialect compatibility, and version compatibility.

Structured Output Validity and citation membership are deterministic.
Context Precision@5 uses the frozen machine qrels only after generation.
Faithfulness and Citation Coverage are judged only for G1 against the five
passages actually provided. Those two measures and context precision are
reported as `N/A` for G0. Mean, P50, and P95 use client monotonic wall latency
including all retries; Ollama server durations and token counts are retained
separately.

The report preserves the measured result even if the target of a 10 percentage
point absolute Task Success improvement is not met. It never changes reference
labels, removes failed cases, or rewrites model answers to pass a gate.
The combined `all` command still writes complete artifacts before returning a
non-zero exit code when engineering, integrity, or quality acceptance fails.
