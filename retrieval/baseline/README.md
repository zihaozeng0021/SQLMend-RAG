# SQLMend-RAG formal baseline retrieval module

This directory implements three sets of independent, auditable baselines: BM25 sparse retrieval, zero-sample E5 dense retrieval, and an RRF hybrid that only fuses the first two rankings. The pipeline controls corpus loading, schema verification, query serialization, indexing, precise retrieval, fusion, TREC export, pool audit, evaluation, performance recording and verification by itself; the third-party library only provides algorithm components and will not send corpus or queries to managed retrieval/RAG services.

Version identity note: This directory and its release are only called **retrieval baseline**, not retrieval v1. Later, the formal retrieval system that added dialect and version awareness was named **retrieval v1**. The existing `*_formal_v1` run tag is an old compatible tag that has been bound by byte hash by annotation provenance. The `v1` does not represent the current retrieval release; the baseline cannot be called v1 based on this, nor can these frozen runs be rewritten in place for name changes.

This is not a statement of completion for the overall AI6127 assignment. No dialect/version explicit weighting, metadata filtering, reranker, query rewriting, HyDE, generation, SQL fixes, or UI are implemented at this stage. PDF's final requirements of a simple UI, five interface demonstration queries, grounded generator, answer-level RAG indicators, and at least 1,000 manually annotated held-out data with annotator consistency of no less than 80% must still be completed in subsequent stages.

## Data Identity and Immutability

The formal corpus is fixed to `construction/data/processed/corpus.jsonl`:

- SHA-256: `279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3`
- 12,000 chunks, 2,400 chunks for each of the five dialects
- Processing order: ascending order by `chunk_id`

`construction/` and `annotation/codex/` are both protected directories. The audit command recursively records the binary SHA-256 of each file; any addition, deletion, or byte change of any file before and after work will fail. You can't just use Git status instead of this check.

The current 250 queries and 13,449 qrels are machine-proposed development data. Any releasable results based on them must be written as **machine-proposed development evaluation** and cannot be called gold, human annotation or held-out test, nor can they be used to offset the final 1,000+ human annotation requirement.

## Strict query whitelist

The actual `dev_250.jsonl` schema is conservatively mapped as:

| User-supplied semantics | Actual fields |
|---|---|
| Database dialect | `dialect` |
| Database version | `version` |
| Natural language problem | `user_problem` |
| Raw SQL | `sql` |
| Observed errors | `error_message`, `error_code`, `sqlstate`, `error_symbol` |

`expected_behavior`, schema/setup/seed, error category, root cause, reference fix, evidence, source link, case flags, verification, qrels and other annotation fields will not enter the formal query. Missing fields omit the entire section and do not insert `Unknown`, `N/A` or `None`. BM25 and dense share the `sqlmend-query-v1` serialization result; SQL only standardizes CRLF/CR line breaks and does not rewrite them.

## Three sets of fixed baselines

### BM25

- `rank_bm25.BM25Okapi==0.2.2`
- `k1=1.5`, `b=0.75`, top 30
- lowercase; no stemming, stopword removal, or SQL-specific weighting
- `sqlmend-lexical-v1` retains SQL identifiers, functions, SQLSTATE, error codes, version numbers, qualified names and operators such as `->>`, `->`, `::`, `<=`, `>=`, `<>`, `!=`
- Sorting: score in descending order, then `chunk_id` in ascending order

### Zero-shot dense

- Model: `intfloat/e5-base-v2`
- Fixed revision: `f52bf8ec8c7124536f0efb74aca902b2995e5bcd`
- `query: ` / `passage: ` prefix; mean pooling
- CPU, 14 threads, batch 64, maximum input 256 tokens
- Use fixed dynamic-int8 model inference for acceptable CPU build times; output embeddings are explicitly converted and saved as L2-normalized float32
- cosine is implemented via exact inner product / matrix multiplication, without using ANN
- Sorting: descending order by similarity, then ascending order by `chunk_id`

The model and parameters are frozen before viewing formal metrics on the development set; multiple models will not be tried on the same 250 pieces of data and the best one will be selected. Model download/loading time is recorded separately from corpus encoding and index writing time.

### Hybrid RRF

Only read the official BM25 top 30 and the official dense top 30:

```text
RRF(d) = 1 / (60 + rank_bm25(d)) + 1 / (60 + rank_dense(d))
```

Missing channels do not contribute scores and component rank is saved as `null`. The output sorting is RRF score in descending order, best component rank in ascending order, and `chunk_id` in ascending order. There is no entry for qrels, relevance, source links, or manual documentation into the Fusion API.

## TREC, qrels and pool semantics

All three sets of runs use:

```text
query_id Q0 chunk_id rank score run_tag
```

The score is fixed to 12 decimal places; each query has exactly 30 items, the rank is continuous, the chunk is unique and must belong to the frozen corpus. The qrels transformation preserves all tags 0/1/2, including relevance 0.

The absence of `(query_id, chunk_id)` qrel means "undecided" and is in no way equivalent to relevance 0. All three systems must reach `Judged@30 = 1.000` before they can publish indicators; otherwise, generate `pool_expansion_required.jsonl` and summary, set `evaluation_integrity_status` to `BLOCKED`, and wait for external human or independent machine judgment. The pool expansion file only makes a judgment request and does not automatically generate labels.

Even if the pool is complete, Recall can only be called **pooled Recall**: the denominator comes from the limited judgment pool, not corpus-exhaustive recall. The existing pool is constructed from historical BM25, BGE dense and source-linked evidence, so there is pooling bias; officially E5/BM25 finding documents outside the pool is an expected phenomenon and should not be penalized to 0.

### How to replenish pool judgments

The `retrieval/baseline/pool_expansion/pool_expansion_required.jsonl` generated by `check-pool` is a read-only request manifest with passage snapshots, official system rankings, and component ranks. After the manual or independent annotation process is completed, save the new judgment as `retrieval/baseline/qrels/pool_expansion_judgments.jsonl`. Each line only needs:

```json
{"query_id":"DEV0001","chunk_id":"smr_example","relevance":1}
```

`relevance` still uses 0/1/2 semantics. Do not edit `annotation/codex/qrels_machine_proposed.jsonl`, existing TREC qrels or pool expansion request files, and do not let the pipeline automatically guess labels. The combiner only accepts query/chunk pairs that are in the current three formal top-30 unions and do not appear in the frozen base qrels; conflicts, duplications, unknown chunks, or records outside the pool will fail. The catch-up file is owned by the external annotation process and the pipeline never creates or overwrites it.

After saving the supplementary judgment file, rerun from `check-pool`; the command will generate new `qrels_effective_dev250.trec` and merge metadata. Only if the `Judged@30` of all three systems reaches 1.000, `evaluate` will generate overall, slice, confidence intervals, pairwise comparisons and complementarity. Otherwise these publications must remain absent.

## Installation and full rebuild

Execute from the repository root directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pip install -e retrieval/baseline

python -m sqlmend_retrieval.cli audit-protected-paths --phase before
python -m sqlmend_retrieval.cli verify-inputs
python -m sqlmend_retrieval.cli serialize-queries
python -m sqlmend_retrieval.cli audit-annotation-retrievers

python -m sqlmend_retrieval.cli build-bm25
python -m sqlmend_retrieval.cli build-dense

python -m sqlmend_retrieval.cli run-bm25
python -m sqlmend_retrieval.cli run-dense
python -m sqlmend_retrieval.cli run-hybrid

python -m sqlmend_retrieval.cli check-pool
python -m sqlmend_retrieval.cli evaluate
python -m sqlmend_retrieval.cli benchmark

python -m sqlmend_retrieval.cli test
python -m sqlmend_retrieval.cli audit-protected-paths --phase after
python -m sqlmend_retrieval.cli finalize
python -m sqlmend_retrieval.cli validate
```

Formal test evidence must be generated by the `test` subcommand. It internally executes `python -m pytest retrieval/baseline/tests -q -p no:cacheprovider`, recording stdout/stderr, return code, Python information and source-tree hash before and after testing; running pytest directly can be used for development diagnosis, but cannot replace `retrieval/baseline/reports/test_results.json`.

`finalize` regenerates failure analysis, manifest, baseline/completion reports, and validates the final rewritten product again. When the current pool is not filled, `evaluate` will write to the BLOCKED sentinel normally, while `finalize` and `validate` will explicitly deny publishing with a non-zero status; this is an expected integrity gate and should not be bypassed.

`python -m sqlmend_retrieval.cli all` Execute the full pipeline in the same dependency order, stopping on input, index, run, deterministic or protected directory hard failures. The Dense model is first downloaded and saved in `retrieval/baseline/indices/dense/model_cache/`; the embedding can be reconstructed offline later. To rebuild the index, there is no need to delete the directory or perform undocumented steps, just rerun `build-bm25`, `build-dense`, three `run-*`, pool/evaluation/benchmark/test/after-audit/finalize in sequence; each project's own products will rewrite and rebind the hash according to the fixed configuration.

Key formal artifacts include: `serialized_queries/dev_250_queries.jsonl`, two index directories, three sets of TREC runs, hybrid provenance, base/effective TREC qrels, pool-expansion requests and summary, evaluation directory, four human-readable reports, validation report and the root `manifest.json`. The internal files of the model cache are managed by the upstream snapshot, and the manifest is bound as a whole using the directory tree hash.

## Performance Measurement

`benchmark` first does 3 warm-ups, then runs all 250 queries once, using `time.perf_counter`. It reports respectively:

- Cold start index/model loading;
- BM25 warm latency;
- dense query encoding, exact vector search and total;
- hybrid BM25 component, dense component, RRF fusion and total;
- mean, median/P50, P95, max, QPS;
- build/encoding time, recursive index size and hardware/software environment.

The current cold-start timing explicitly includes index/model loading and frozen corpus and configuration binding verification, but does not include process startup; warm-query statistics exclude these one-time tasks.

Figures for different hardware cannot be directly compared without declaring the environment.

## How to interpret the three states?

- `engineering_status=PASS`: frozen input, isolation, 250×30 runs, chunk/rank/score validity, byte consistency between two runs, tests, reports and protected directories all passed.
- `evaluation_integrity_status=PASS|BLOCKED`: PASS only when the Judged@30 of the three systems are all 1, the evaluation product is complete and the qrels have not changed; BLOCKED is required when there is unjudged top-30.
- `retrieval_quality_status=PASS|FAIL|NOT_EVALUATED`: Measures hybrid nDCG/pooled Recall/HitRate targets on the full pool; NOT_EVALUATED when the pool is blocked. Quality FAIL does not equal engineering implementation errors and cannot be hidden by changing qrels, queries, slices, models, or RRF parameters.

Only if the first two states are PASS, stages 5-6 can be called a complete baseline release; only after that is it recommended to enter Stage 7 dialect-aware retrieval. Even so, the required UI, generation, and manual test sets for the PDF are still work-in-progress for the course project.
