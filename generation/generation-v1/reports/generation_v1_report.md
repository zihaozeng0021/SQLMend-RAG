# Phase 10: Generation Baseline and Generation v1

Schema: `sqlmend-generation-report-v1`. This report is a **machine-proposed development evaluation**;
The current 250 records and offline reference/qrels are machine-proposed development data.
Not artificial gold, nor the final held-out test results. The failure wrapper is always left in the denominator.

## Experimentation and Completeness Boundaries

- Pairing query: 250; official result wrapper (with explicit failure record): 500.
- Baseline: `baseline`, does not accept retrieval evidence; Baseline's RAG indicator is `N/A`.
- Generation v1: `generation_v1`, only use frozen Retrieval v1 this time Top-5 evidence.
- Offline judge: `qwen3.5:4b`, digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`, `think=false` (thinking disabled); one anonymous A/B logical call per query, parity inversion, up to 3 attempts.
- Run seal: Baseline `70e4372ee2414be316ebd084b95503d01d87ecfa6ec49b916da13c64370f0927`; Generation v1 `0af9cc7c8dbbcecdeaa87cf73ff51c22cb13c0016e07614060787e3a1434c66c`. Both runs are archived before reference/qrels is opened for the first time.
- Online generation output is not written back by reference, annotation evidence or qrels; reference fields only enter this offline evaluation.
- Generation `status=success` only means that the call finally passes the JSON/schema/citation contract, but does not mean that the SQL problem has been corrected; the semantic correctness is only measured by the offline indicators below.

## Formal generation execution

| System | Official wrapper | Generation Contract Success | Explicit failure | retries | Recover after retries |
|---|---:|---:|---:|---:|---:|
| Baseline | 250 | 250 | 0 | 2 | 2 |
| Generation v1 | 250 | 241 | 9 | 56 | 30 |

Attempts/retries are calculated independently by the `attempts` array retained by each formal wrapper; Generation Contract Success does not mean that the SQL semantics are correct.

## Offline judge execution (project access control)

**FAIL: judge call success 249/250, failure 1, retry 2. **
All 250 records must have a successful judge result; only a judgment record but a judge failure does not meet the project access control.

## Complete indicator table

| Metrics | Baseline Closed-Book | Generation v1 Retrieval-v1 RAG | Generation v1 - Baseline |
|---|---:|---:|---:|
| Generation Contract Success Rate | 100.00% | 96.40% | -3.60pp |
| Task Success Rate | 50.80% | 68.00% | +17.20pp |
| Root Cause Accuracy | 82.40% | 89.60% | +7.20pp |
| SQL Repair Correctness | 52.40% | 68.40% | +16.00pp |
| Dialect Compatibility | 80.00% | 90.80% | +10.80pp |
| Version Compatibility | 79.60% | 90.80% | +11.20pp |
| Structured Output Validity | 100.00% | 96.40% | -3.60pp |
| Answer Relevance | 0.8194 | 0.8910 | +0.0716 |
| Citation Validity | N/A | 100.00% | N/A |
| Citation Coverage | N/A | 0.6416 | N/A |
| Faithfulness | N/A | 0.7726 | N/A |
| Context Precision (qrels rel>=1) | N/A | 0.2792 | N/A |
| Context Query Hit Rate (qrels rel>=1) | N/A | 72.40% | N/A |

Task Success only counts 1 when root cause, SQL fix, dialect compatibility, and version compatibility are all true at the same time.
Generation v1 absolute change from Baseline is **+17.20 percentage points**; primary goal (at least +10pp): **reached**.

## Paired per-query comparison

See [per_query_comparison.jsonl](../evaluation/per_query_comparison.jsonl) for the complete 250 rows of matching results.
This file retains the generation status of the two systems, four task judgments, structured validity, latency, and judge retry query by query.
and citation/context audit for Generation v1; no deletion failure cases.

| Matching results | Number of queries |
|---|---:|
| Generation v1 improvements (Baseline Task fail → Generation v1 Task Success) | 71 |
| Generation v1 becomes worse (Baseline Task Success → Generation v1 Task fail) | 28 |
| Both Task Success | 99 |
| Both Task fail | 52 |

## The most obvious case of improvement in Generation v1

There are 71 cases that actually meet the criteria of Baseline Task fail → Generation v1 Task Success; if there are less than 3 cases, clear placeholder lines will be used to retain the report structure, and tie or regression will not be passed off as improvement.

| Query | Paired outcome | Baseline → Generation v1 task | Context | Judge Summary |
|---|---|---:|---:|---|
| `DEV0008` | generation_v1_improved | 0 → 1 | P=0.20, hit=true | Baseline: B incorrectly claims the original SQL is valid in PostgreSQL and fails to provide the required FROM/WHERE syntax, contradicting the reference fix and evidence.; Generation v1: A correctly identifies the root c… |
| `DEV0013` | generation_v1_improved | 0 → 1 | P=0.20, hit=true | Baseline: Answer A incorrectly identifies the root cause as a deprecated operator '<->' and suggests replacing it with '<@', which is semantically incorrect for tsquery matching. It fails to recognize that the original … |
| `DEV0023` | generation_v1_improved | 0 → 1 | P=0.40, hit=true | Baseline: Answer A fails to identify the root cause (nested aggregates in SELECT) and proposes a syntactically invalid fix that does not compute the required average of per-account sums.; Generation v1: Answer B correct… |

## Cases where Generation v1 did not improve or performed worse

| Query | Paired outcome | Baseline → Generation v1 task | Context | Judge Summary |
|---|---|---:|---:|---|
| `DEV0061` | generation_v1_regressed | 1 → 0 | P=0.20, hit=true | Baseline: Correctly identified INTERSECT as unsupported in 8.0.30 and provided a semantically equivalent fix using IN subquery, but failed to cite the specific version boundary evidence.; Generation v1: Incorrectly clai… |
| `DEV0083` | generation_v1_regressed | 1 → 0 | P=0.00, hit=false | Baseline: A correctly identifies the NULL poison in NOT IN and provides a valid fix, but fails to cite the retrieved evidence.; Generation v1: formal generation wrapper records a failed model call |
| `DEV0103` | generation_v1_regressed | 1 → 0 | P=0.00, hit=false | Baseline: Correctly identified the root cause and provided a semantically equivalent fix for SQLite, but failed to cite any retrieved evidence despite having access to relevant SQLite documentation.; Generation v1: form… |

## Reasons why RAG is valid and invalid

- Separate statistics for Generation failures: 0 for Baseline, 9 for Generation v1; 1 for offline judge failure. The following context/evidence-utilization count only analyzes the 240 cases where both judge and Generation v1 generation were successful, and will not misattribute call failures to retrieval or evidence utilization.
- Among the queries with qrels rel>=1 context hit, 60 were converted to Task Success, and 115 had no net improvement. Hit-related passage is a necessary help, but there is no guarantee that the model will take advantage of it.
- The Top-5 of 65 queries have no rel>=1 hits; such failures are more likely to come from irrelevant or insufficient retrieval context.
- 192 Generation v1 answers have faithfulness ≥ 0.8, and 48 answers are lower than 0.8; when the relevant context already exists but faithfulness is still low, the problem is closer to evidence utilization or model capability.
- 84 answers have no fictitious citation (validity=1) but coverage<0.8; this shows that citation validity alone cannot prove that key diagnosis and repair are covered by evidence.
- Citation Validity assumes vacuous 1.0 for zero citations (no fictitious passage ID); it does not indicate the presence of evidence and must be interpreted along with Citation Coverage, generation/judge failure.
- The judge reason and context/citation audit of the paired case are saved in the per-query artifact, which can further distinguish the retrieval context, prompt, model SQL capability and evidence utilization.
- Interpretation limitations: generation and offline judge use the same Qwen model, and there may be related self-judge bias; the reference is also a machine-proposed development reference, not artificial gold. These scores are therefore suitable for baseline control and failure analysis and should not be represented as independent human adjudication.

## Generation latency

latency is the end-to-end generation wall time recorded by the official wrapper; judge latency is not mixed into this table.

| System | Mean (ms) | P50 (ms) | P95 (ms) |
|---|---:|---:|---:|
| Baseline | 20359.111 | 19139.369 | 25354.586 |
| Generation v1 | 32508.251 | 28171.236 | 62102.867 |
| Generation v1 - Baseline | +12149.139 | +9031.867 | +36748.281 |

## 500 official results wrapper and provenance

- 250 wrapper for Baseline: `generation/baseline/runs/baseline_closed_book_dev250.jsonl`.
- 250 wrappers for Generation v1: `generation/generation-v1/runs/generation_v1_rag_dev250.jsonl`.
- Generation v1 actual Top-5 evidence: `generation/generation-v1/prepared_inputs/generation_v1_evidence_top5.jsonl`.
- Anonymous pairing judge journal: `generation/generation-v1/evaluation/judgments.jsonl`.
- Generation seal: `generation/generation-v1/evaluation/generation_seal.json`.
- Naming migration ledger: `generation/generation-v1/provenance/system_naming_migration.json`.

Each answer wrapper comes with input provenance, prompt SHA, exact model provenance, unified retry attempts and wall latency;
Therefore, each of the 500 official result wrappers can be traced back to their own unlabeled inputs and model calls; they generate `answer=null` for failure items, but still retain the full failure and provenance.

## Acceptance

- Engineering: **FAIL**.
- Evaluation integrity: **PASS**.
- Quality target: **PASS**.
- Phase success: **FAIL**.

Any failed gate will not trigger reference label modification, failed case deletion, or result overwriting.

## Rebuild and evaluate from a clean environment

Execute in the warehouse root directory:

```powershell
python -m venv .venv-generation-v1
.\.venv-generation-v1\Scripts\python.exe -m pip install --upgrade pip
.\.venv-generation-v1\Scripts\python.exe -m pip install -r generation\generation-v1\requirements.txt
.\.venv-generation-v1\Scripts\python.exe -m pip install -e generation\generation-v1 --no-deps
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Resolve-Path 'generation\generation-v1\src')
.\.venv-generation-v1\Scripts\python.exe -m sqlmend_generation_v1.cli --root . all --clean
```
