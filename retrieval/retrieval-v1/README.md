# SQLMend-RAG Retrieval v1

Retrieval v1 adds dialect awareness, version awareness and a lightweight lexical reranker on top of the frozen BM25, Dense E5 and Hybrid RRF baselines. It is a standalone release; it does not override `construction/`, `annotation/codex/` or `retrieval/baseline/`.

The 250 queries and relevance judgments here can only be called **machine-proposed development data**. They are used for development experiments and regression validation, not artificial gold, nor the final held-out test set.

## Five formal systems

| Abbreviation | System ID | Configuration | Run |
|---|---|---|---|
| Frozen Hybrid | `hybrid_rrf_frozen_control_v1` | `config/systems/frozen_hybrid_control.yaml` | Reference to frozen `retrieval/baseline/runs/hybrid_rrf_formal_dev250.trec` |
| + Dialect | `hybrid_rrf_dialect_aware_v1` | `config/systems/dialect_aware.yaml` | `runs/hybrid_rrf_dialect_aware_dev250.trec` |
| + Version | `hybrid_rrf_version_aware_v1` | `config/systems/version_aware.yaml` | `runs/hybrid_rrf_version_aware_dev250.trec` |
| + Dialect + Version | `hybrid_rrf_dialect_version_aware_v1` | `config/systems/dialect_version_aware.yaml` | `runs/hybrid_rrf_dialect_version_aware_dev250.trec` |
| + Dialect + Version + Reranker | `hybrid_rrf_dialect_version_lexical_rerank_v1` | `config/systems/dialect_version_reranker.yaml` | `runs/hybrid_rrf_dialect_version_lexical_rerank_dev250.trec` |

All new systems take candidates from the RRF union of frozen BM25 Top-30 and Dense Top-30. Each query has 45–60 candidates; metadata awareness and reranker only do soft reranking and do not delete documents across dialects, old versions, or with unknown metadata. The final output depth is fixed at 30.

## Method

Dialect awareness uses query dialect and corpus-owned dialect metadata. Same dialect takes precedence; MySQL/MariaDB as related, but still counts as `Wrong-Dialect@5` as an explicit dialect mismatch; unknown falls between related and explicitly incompatible. No categories will be hard filtered.

Version awareness only uses explicit, conservative matching version boundaries in corpus metadata and passages, and does not infer support range from document time or wording. Priorities are compatible, general, unknown, incompatible; cross-dialect version namespaces are marked `not_applicable`. If an explicit boundary excluding the current version directly names a function, operator, or error flag in a query, it is useful diagnostic evidence, not evidence of an incorrect version.

Reranker calculates deterministic corpus-IDF BM25/exact identity matches for `user_problem`, SQL, actual error field and candidate passage respectively, and then adds them with the Dialect+Version score with a small weight. It does not train models, access the network, or read qrels, reference fixes, root causes, case flags, or candidate labels.

The data boundaries for online sorting are `OnlineQuery`: dialect, version, user problem, SQL, actual error message/code/SQLSTATE/symbol, and the text of the frozen serializer. Original annotation records only appear in safe projection and offline slice evaluation; offline qrels are loaded in `experiment.py` and will not enter `build-runs`.

## Development set results

The following table comes from the five-system Top-30 pool of complete judgments; `Judged@30` are all 1.0. Exact values, full slices, success/failure cases and latency are subject to `reports/retrieval_v1_report.md`, `evaluation/comparison_results.json` and `reports/latency.json`.

| System | nDCG@10 | MRR@10 rel2 | pooled Recall@10 rel2 | HitRate@5 rel2 | Wrong-Dialect@5 | Wrong-Version@5 | Unknown-Version@5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Frozen Hybrid | 0.306983 | 0.431941 | 0.500162 | 0.544 | 0.2704 | 0.0032 | 0.0096 |
| + Dialect | 0.325623 | 0.453219 | 0.519162 | 0.568 | 0.1008 | 0.0040 | 0.0112 |
| + Version | 0.317487 | 0.450606 | 0.508029 | 0.576 | 0.1720 | 0.0016 | 0.0080 |
| + Dialect + Version | 0.329306 | 0.468895 | 0.536695 | 0.588 | 0.0944 | 0.0008 | 0.0072 |
| + Dialect + Version + Reranker | 0.345570 | 0.494748 | 0.559629 | 0.632 | 0.0952 | 0.0008 | 0.0088 |

Compared with Frozen Hybrid, the final system’s nDCG, MRR and pooled recall were improved by `+0.038587`, `+0.062806` and `+0.059467` respectively. On 174 dialect-sensitive queries, `Wrong-Dialect@5` has a relative decrease of 63.68%; on 53 version-sensitive queries, `Wrong-Version@5` has a relative decrease of 66.67%. All Phase 7/8/9 and final acceptance gates passed.

These numbers cannot be extrapolated to human test performance. Whenever any new official Top-30 pair is missing a judgment, `check-pool` writes it to `pool_expansion/pool_expansion_required.jsonl`, the evaluation status becomes `BLOCKED`, and no nDCG, MRR or pooled Recall is issued.

## Rebuild from a clean environment

The following command is run from the repository root directory. Python 3.11+ is available; the current frozen environment is Python 3.12.

```powershell
python -m venv .venv-retrieval-v1
.\.venv-retrieval-v1\Scripts\python.exe -m pip install --upgrade pip
.\.venv-retrieval-v1\Scripts\python.exe -m pip install -r retrieval\retrieval-v1\requirements.txt
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Resolve-Path 'retrieval\retrieval-v1\src')
.\.venv-retrieval-v1\Scripts\python.exe -m sqlmend_retrieval_v1.cli --root . all --clean
```

The step-by-step rebuild and audit sequence is as follows:

```powershell
python -m sqlmend_retrieval_v1.cli --root . audit-protected-paths --phase before
python -m sqlmend_retrieval_v1.cli --root . verify-inputs
python -m sqlmend_retrieval_v1.cli --root . build-runs
python -m sqlmend_retrieval_v1.cli --root . check-pool
python -m sqlmend_retrieval_v1.cli --root . evaluate
python -m sqlmend_retrieval_v1.cli --root . benchmark
python -m sqlmend_retrieval_v1.cli --root . test
python -m sqlmend_retrieval_v1.cli --root . audit-protected-paths --phase after
python -m sqlmend_retrieval_v1.cli --root . finalize
python -m sqlmend_retrieval_v1.cli --root . validate
```

`build-runs` is an unlabeled online path; the commands after `check-pool` belong to the offline evaluation/release path. `clean` only removes the known build directories and manifests under `retrieval/retrieval-v1/`, without touching the source code, tests, configurations, this article or any frozen directories.

## Formal evidence

- `manifest.json`: SHA-256 bindings for input, config, system, runs, provenance, evaluation, reporting and test evidence.
- `reports/validation_report.json`: Independent validation results recalculated from the current file.
- `reports/protected_paths_before.json` / `protected_paths_after.json`: full byte snapshot of three protected directories.
- `evaluation/acceptance.json`: Phase 7/8/9 and final access control.
- `evaluation/judged_coverage.json`: Top-30 judgment completeness of five systems.
- `reports/test_results.json`: source-tree hash and pytest output before and after testing.

No generator, UI or final human held-out dataset is implemented at this stage.
