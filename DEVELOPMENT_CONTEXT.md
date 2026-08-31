# SQLMend-RAG development context

> Handover documents are developed internally and are not intended for end users.
>
> Last verified: 2026-08-30 (UTC+8)
>
> Current stage: The knowledge base, machine-proposed development annotation, frozen retrieval baseline and formal retrieval v1 have all been verified; 500 formal wrappers, offline evaluation, testing, manifest and validation of Phase 10 Generation Baseline / Generation v1 have also been completed. Generation's main quality goals passed, but the overall acceptance truth flag was `FAIL` (Generation v1 structured validity 96.4%, judge calls 249/250); the UI and final human held-out dataset remained unfulfilled.

## 1. Purpose of this document

This document is the project entry point for subsequent developers and automated agents, and is used to answer the following questions:

- What exactly is required of the course and which stages are still incomplete;
- What is each directory in the warehouse responsible for, and which directories cannot be modified;
- What is the nature of the current data and what results can or cannot be claimed;
- How the implemented retrieval system works, how it is reconstructed and verified;
- What is the current real blocker, and in what order should the next step be advanced;
- Which evidence must be regenerated after modifying a certain type of file.

It does not replace the following files:

- Root directory `README.md`: current module, running entry and known acceptance status for users; it still needs to be updated with UI and manual evaluation when the final course is delivered.
- Automatically generated manifests, validation reports and test reports: they are machine-verifiable factual evidence.
- Final course report: course questions, experimental analysis, screenshots and submission materials should be organized separately.

If this article conflicts with the current file bytes or a rerun validator, the validator and the current bytes shall prevail, and this article shall be updated; do not manually modify the output to make the report match this article.

## 2. Priorities and sources of truth

### 2.1 Requirement Priority

1. Course `Assignment.pdf` is the highest standard. The source path is `.\requirements\Assignment.pdf`; the current file SHA-256 is `B61A21DC5ED61B94EB584B7A50694C9304152246192BF4C40A753E17C6A1C2BB`. Collaborators must fetch the same PDF from a controlled share and verify the hash; they cannot substitute a file with the same name but different content.
2. The user’s explicit decision takes effect when it does not violate the PDF.
3. The special specifications or prompts of the current stage only constrain this stage.
4. Warehouse module documentation and engineering practices.
5. This article and README.

Special decisions currently confirmed: The existing 250 pieces of Codex-generated data and their Top-30 machine judgments can be used for development, debugging, and baseline regression; they are not artificial gold and cannot replace the final artificial held-out data required by PDF. The Annotation v2 experiment has been abandoned, and the current main version is still v1; the version history is only maintained in `annotation/VERSION_HISTORY.md`.

The "no implementation of UI, generators, or final artifact sets" in the Phase 5-6 specification is only an implementation boundary at that time and does not mean that these course requirements are cancelled.

### 2.2 Current fact priority

1. The current file bytes, and the validator, manifest, and test results rerun on these bytes.
2. Freeze configuration, schema, source data and source code.
3. Automatically generated reports.
4. Verification snapshots in this article.
5. README or verbal summary.

Variable numbers are saved in this article only for summary and authoritative paths. After each rerun, you should check the corresponding manifest/report before updating this article.

## 3. Summary of hard course requirements

Coursework accounts for 35% of the total grade, and the main scoring is divided into 20 points for knowledge base construction, 40 points for retrieval, and 40 points for downstream tasks and generation. Groups are 5 or 6 people. The system cannot be just a collage of existing services or a single call to the hosted RAG API; it must be able to interpret and implement its own offline knowledge base, retrieval and evidence-based generation phases.

Submission deadline is 2026-11-07 23:59 SGT via Blackboard. Only the first submission will be counted; the original PDF stipulates that `5% points` will be deducted for each rounded-off day. Applications that overlap by more than 30% with projects from the same year or previous years will be disqualified.

The knowledge base requires at least 10,000 documents/passages and 100,000 words. The team still needs to collect and annotate the held-out test set themselves; it must not be duplicated and should be as balanced as possible. The search contains at least sparse, dense and hybrid, SQL `LIKE` cannot be used instead of text search.

Course Questions and Minimum Delivery Requirements:

| Question | What must be covered | Current status |
|---|---|---|
| Q1 | Corpus source, collection, cleaning, chunking, storage; application and sample query; number of documents/chunk/word/type | The knowledge base project has been completed, but the final report has not yet been written |
| Q2 | Simple and friendly UI; 5 queries, results and query speed | `PANNED` |
| Q3 | sparse, dense, hybrid; retrieval innovation; rank-aware indicators and cases such as Precision@K, Recall@K, MRR, nDCG, etc. | Fixed baseline and formal retrieval v1 have been implemented; five systems ablation, slicing, case, latency, manifest and validation have all passed, and the conclusion is only applicable to machine development evaluation |
| Q4 | Generation/classification method selection and preprocessing; self-establish at least 1,000 manually held-out records without duplication and as balanced as possible; IAA at least 80% (3 annotators are recommended, 2 are also acceptable); task indicators, RAG indicators and performance indicators | 250 machine development controls, task/RAG/latency indicators for Generation v1 have been completed; 1,000+ manual held-out, IAA and final testing are still `PLANNED` |
| Q5 | Downstream innovation; if there are multiple innovations, individual and combined ablation must be done; explain specific problems and cases | `PLANNED` |

The final submission is a PDF named after the group number, for example `10.pdf`. The first page must list the names and student IDs of all team members; the text answers Q1-Q5; and two accessible zip package links must be provided:

1. Data package: knowledge base, query and retrieval results, evaluation set, answer generation/classification results, and data required for Q3/Q5;
2. Source code package: all source code and dependencies, and includes end-user README that explains how to compile and run.

The course also requires an offline presentation during Week 13, and the final report should contain clear pictures. Generative tasks must have answers supported by user-inspectable retrieval evidence. Answer-level RAG evaluations cover at least faithfulness, answer relevance, and context relevance/precision; they also discuss latency, throughput, cost, and scalability.

## 4. Current project overview

| Module or Delivery | Status | Conclusion |
|---|---|---|
| `construction/` | `VERIFIED_COMPLETE` | 12,000 chunks of five-dialect knowledge base; 24/24 verified and 90/90 tested |
| `annotation/codex/` | `VERIFIED_COMPLETE_FOR_DEVELOPMENT_ONLY` | Current main version v1, revision 1.1.0; 250 queries, 23,452 machine judgments, all three official Top-30 are covered; not artificial gold or final test set |
| `retrieval/baseline/` project | `VERIFIED_COMPLETE` | BM25, zero-sample E5, two-way RRF, audit, test, performance and release access control have been implemented and bound to the current source code snapshot |
| Formal retrieval assessment completeness | `PASS` | `Judged@5/10/20/30` for BM25, dense, hybrid are all 1.0, the complete assessment artifact has been released atomically |
| Retrieval quality | `PASS_FOR_MACHINE_DEVELOPMENT_EVAL` | hybrid leads in all four main indicators and passes the established gate; the conclusion only applies to the current machine development set |
| `retrieval/retrieval-v1/` | `VERIFIED_COMPLETE_FOR_DEVELOPMENT_ONLY` | Five independent systems, Dialect/Version awareness, lexical reranker, full pool, 60 tests, manifest and 12/12 validation checks all passed; no modifications to frozen baseline |
| `generation/baseline/` | `FORMAL_BASELINE_COMPLETE_FOR_DEVELOPMENT_ONLY` | Closed-Book Baseline 250 formal wrappers; Generation Contract Success 250/250, Task Success 50.8% |
| `generation/generation-v1/` | `COMPLETE_WITH_FAILED_ENGINEERING_GATES_FOR_DEVELOPMENT_ONLY` | Retrieval-v1 RAG 250 formal wrappers and pairing evaluations; Task Success 68.0%, relative to Baseline +17.2pp; structured validity 96.4% and judge calls 249/250 leading to Phase success=false |
| Final manual held-out data | `PLANNED` | Still requires at least 1,000 manual records and IAA >= 80% |
| UI with 5 demo queries | `PLANNED` | Not yet implemented |
| Final course report and user README | `PARTIAL` | Root README synchronized with current project status; final course report and UI/human assessment instructions still to be completed |

The release status object of baseline is currently finalized:

```text
release=retrieval-baseline
engineering_status=PASS
evaluation_integrity_status=PASS
retrieval_quality_status=PASS
annotation_reproduction_status=PARTIAL
overall_success=true
```

Release status object for current retrieval v1:

```text
release=retrieval-v1
engineering_status=PASS
evaluation_integrity_status=PASS
retrieval_quality_status=PASS
Judged@30=1.0 for all five formal systems
overall_success=true
source_tree_sha256=d55c547ee2c3972012e174a58ccf7bd0f33a57091deb0147cccb2aeeef0e76a9
protected_before_after_current_identical=true
```

The final system is relatively frozen. Hybrid's machine-proposed development results: graded nDCG@10 `+0.038587`, MRR@10_rel2 `+0.062806`, pooled Recall@10_rel2 `+0.059467`; dialect-sensitive Wrong-Dialect@5 relative decrease `63.68%`, version-sensitive Wrong-Version@5 A relative decrease of `66.67%`. All Phase 7/8/9 with final gate pass. See `retrieval/retrieval-v1/evaluation/` and `reports/retrieval_v1_report.md` for authoritative values; they must not be called artificial gold or held-out test results.

Evidence binding for current checkout:

```text
current_checkout_retrieval_evidence=VERIFIED
current_source_tree_sha256=02bf56a20642d5563097c01f0232cba37b358b9929b5f0cb2a43f2da20d0c3c8
formal_run_bytes_unchanged=true
required_validation=test -> finalize -> validate
```

[retrieval manifest](retrieval/baseline/manifest.json) and [retrieval validation](retrieval/baseline/reports/validation_report.json) are authoritative sources of current status. This article only retains the summary; if the status or number conflicts, the validator rerun shall prevail.

Release status object for current Generation v1:

```text
release=generation-v1
generation_wrappers=500
generation_contract_success=Baseline 250/250; Generation v1 241/250
task_success=Baseline 50.8%; Generation v1 68.0%; delta +17.2pp
engineering_status=FAIL
evaluation_integrity_status=PASS
quality_status=PASS
phase_success=false
tests=64 passed
independent_artifact_checks=7/7 PASS
protected_before_after_current_identical=true
```

`Generation Contract Success` only means that the transport, JSON, schema and citation contract are successful, but does not mean that the SQL is repaired correctly. `Task Success` means that root cause, SQL repair, dialect compatibility and version compatibility are true at the same time. Authoritative sources are [Generation report](generation/generation-v1/reports/generation_v1_report.md), [overall metrics](generation/generation-v1/evaluation/overall_metrics.json), [validation](generation/generation-v1/reports/validation_report.json), and [manifest](generation/generation-v1/manifest.json).

## 5. Git time point description

Currently observed:

- branch: `generation-v1`
- HEAD: `b21690aef1a66f88aab3c26e9f1537177e97479c`
- retrieval first commit: `0cca1db2c667e56bfa2693cf642efac287ada4b3`
- Machine development annotation submission: `f6afc4023e44218e89d4ad9ce6ad37b4350d391e`

The current Generation v1 module and root document updates are still in the working tree and have not been replaced by Git commits by this article. The manifest of each module records the provenance at the time of generation; do not manually modify its commit or worktree fields, and can only rebind them through formal processes.

`DEVELOPMENT_CONTEXT.md` lives outside the retrieval source snapshot, and updating this article alone will not change that snapshot again.

## 6. Warehouse directory and ownership

```text
SQLMend-RAG/
├─ construction/ # Knowledge base collection, cleaning, deduplication, chunking, statistics, verification; freezing input
├─ annotation/ # Codex machine development set, provenance and VERSION_HISTORY; current main version v1
├─ retrieval/ # General directory of retrieval methods
│ ├─ baseline/ # Freeze retrieval baselines, runs, evaluation access and reports
│ └─ retrieval-v1/ # Freeze the official Retrieval v1, five-system comparison and Final interface
├─ generation/
│ ├─ baseline/ # Phase 10 Closed-Book Baseline: 250 wrappers and independent manifest
│ └─ generation-v1/ # Retrieval-v1 RAG: 250 wrappers, paired offline evaluation and proof of acceptance
├─ tmp/ # Local temporary directory; not a source of fact or delivery contract
├─ DEVELOPMENT_CONTEXT.md # This file: Internal development handover entrance
└─ README.md # User portal; synchronized current module and Phase 10 status
```

### 6.1 Protected Directory

`construction/`, the current master version `annotation/codex/`, `retrieval/baseline/` and `retrieval/retrieval-v1/` are read-only, byte-level protected inputs for Generation v1. This round of data and Retrieval v1 maintenance has ended and the snapshot has been re-anchored; unless the user explicitly starts versioned maintenance again, they must not:

- Add, delete, rename or modify any files;
- Run Python commands that may generate `__pycache__`;
- Perform cross-warehouse cache cleaning or formatter;
- Override original machine qrels, candidate pool or provenance.

Formal before/after audits are:

```text
protected_file_count=8709
protected_tree_sha256=59884bd7f68d02e0bc98594e940c5e89bc6d19a8f9fc3b4c7307bed011ccb4e6
protected_paths_unchanged=true
```

All Python commands are recommended to be set first:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
```

### 6.2 Local artifacts that can be reconstructed but not tracked

The following content is excluded by `.gitignore` and will not be brought by fresh clone:

- `retrieval/baseline/indices/bm25/index.pkl` and metadata;
- `retrieval/baseline/indices/dense/embeddings.npy`, chunk mapping, metadata and E5 model cache;
- Historical BGE snapshot in `retrieval/baseline/reproduction/model_cache/`;
- Python bytecode and pytest cache.

Don't think of the "native presence" of these directories as proof of repository reproducibility; rebuild commands and manifest binding are.

## 7. Knowledge base construction: completed facts

For module entry and detailed design, see [construction README](construction/README.md), and for authoritative acceptance, see [construction validation](construction/reports/validation_report.json) and [construction completion report](construction/reports/completion_report.md).

Production corpus can only be used:

```text
path=construction/data/processed/corpus.jsonl
sha256=279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3
```

Don't treat the ignored, rebuildable `corpus_fixed.jsonl` as production corpus.

Key statistics:

| Item | Value |
|---|---:|
| raw documents | 8,284 |
| cleaned documents | 8,189 |
| final chunks | 12,000 |
| total words | 1,663,145 |
| approximate unique word types | 35,646 |
| average chunk words | 138.5954 |
| median chunk words | 131 |
| PostgreSQL / MySQL / SQLite / MariaDB / DuckDB | 2,400 chunks each |
| known version/range coverage | 95.2083% |
| exact duplicates / estimated residual near duplicates | 0 / 0% |
| manual inspection | coherent searchable 100/100; SQL/error applicable items retained 38/38 |
| automated validation / tests | 24/24 PASS; 90/90 PASS |

The pipeline supports HTML, Markdown, XML/SGML, plain text, and MySQL/MariaDB HELP and error directories. Production chunking is a structure-aware strategy with a target of 150 words, a common upper limit of 260, and an overlap of 20; detailed parameters are in `construction/config/chunking.yaml`.

Historical reconstruction reference (**Do not perform in current protected checkout**):

The following commands will overwrite the `construction/` artifacts and may cause caching. They only apply to one-time clean copies, or user-initiated, independently versioned repository maintenance work; `PYTHONDONTWRITEBYTECODE` can only block `.pyc`, not the application from writing files.

```powershell
python -m pip install -e ".\construction[test]"
python -m sqlmend_pipeline.cli build
python -m sqlmend_pipeline.cli validate
python -m pytest construction/tests -q
```

The Knowledge Base phase may be termed complete, but this does not indicate completion of the RAG system or coursework.

## 8. Codex machine development set: strict boundaries

For module description, see [annotation README](annotation/codex/README.md), for identity, see [annotation manifest](annotation/codex/manifest.json), for acceptance and distribution, see [annotation validation](annotation/codex/validation_report.json) and [annotation statistics](annotation/codex/statistics.json).

Manifest clearly documents:

```text
dataset_id=sqlmendrag-codex-dev-250
dataset_version=1.1.0
annotation_main_version=v1
purpose=development_only
split=dev
annotation_origin=codex_machine_proposed
human_verified=false
eligible_for_assignment_final_eval=false
validation_status=PASS
```

The following titles must be used:

> machine-proposed development data
>
> machine-proposed development evaluation

Referred to as `gold`, human-annotated, human-adjudicated, held-out test or final evaluation set is strictly prohibited. Top-30 double-blind machine annotation, third-round machine adjudication, and fixed 50-item Codex independent quality audit are not human verification and cannot count toward the PDF’s human annotation requirements.

### 8.1 Data Identity and Statistics

| item | numeric value or SHA-256 |
|---|---|
| queries | 250; 50 for each of the five dialects |
| query SHA | `2ce81dd27690795266fc5cc813dc1999f8c55d86ed1605fd6e1013213a416fae` |
| candidate pool SHA | `86549c5b1bb59cb1557c747db37c66b77a0812c8a8f9ff02dd2d75c0be87a60f` |
| qrels source SHA | `bc672f2767762d253e8c9dc239d37d00bdb88a547c0c80585788c8c9021e8d3f` |
| qrels | 23,452 |
| relevance 0 / 1 / 2 | 20,154 / 2,839 / 459 |
| Official Top-30 union | 14,232 pairs; three-way `Judged@30=1.0` |
| Double-blind agreement / disagreement | 13,326 / 906; exact agreement 93.63%; Cohen's kappa 0.523 |
| dialect-sensitive | 174 |
| version-sensitive | 53 |
| documented-error cases | 69 |
| plausible-but-wrong cases | 214 |
| execution verified / documentation only | 78 / 172 |
| independent Codex audit | 50/50 PASS; still not human |

Each of the ten error categories has 25 entries. All 250 are passed under the declared validation method, but documentation-only is not equivalent to actually running the validation.

### 8.2 Relevance semantics

- `0`: It has been judged that the problem cannot be supported;
- `1`: Partially useful or providing background;
- `2`: directly supports diagnosis, repair or compatibility conclusions;
- Non-existent pair in qrels: `unjudged`, must not be silently converted to relevance 0.

The current main qrels also retains the formal out-of-range judgment of the original v1, and completely covers the frozen BM25, dense, and hybrid three-way official Top-30 union. Explicit case-evidence tags from original v1 are retained; formally scoped heuristic tags are replaced by A/B double-blind consensus or third-round blind adjudication. See [Top-30 blind refresh provenance](annotation/codex/provenance/top30_blind_refresh.json) for specific ranges, transfer counts and hashes.

Data maintenance verification command (**Do not execute in normal retrieval development**):

This validator is not a read-only check; it overrides execution evidence, quality audit, statistics, validation report, and manifest. This round of v1 revision 1.1.0 has passed 25/25 checks. Later only run on a one-time clean copy or a maintenance version of the data explicitly authorized by the user; upon completion the protected audit must be re-anchored and the retrieval finalization chain re-run.

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python annotation/codex/validate_annotations.py --root .
```

Normally do not rebuild this protected development set. Annotation version records are written uniformly into [VERSION_HISTORY](annotation/VERSION_HISTORY.md); when final manual data is needed, a new independent directory, schema and manifest should be created.

## 9. Formal search baseline design

For the technical entrance, see [retrieval README](retrieval/baseline/README.md). `retrieval/baseline/` is an independent module. It is currently only responsible for fixed baseline and is not responsible for UI, generation, SQL repair, reranker, query rewriting, HyDE or explicit dialect/version adjustment.

### 9.1 Data flow and isolation

```text
[retrieval path]
frozen corpus + frozen dev queries
             |
             +-> strict user-field serializer -> BM25 -> BM25 top-30 ---+
             |                                                           +-> RRF -> hybrid top-30
             +-> passage rendering -------------> E5  -> dense top-30 --+

[offline evaluation path]
machine-proposed base qrels + external supplemental judgments
                         |
                         +-> effective qrels ----------------------------+
                                                                         |
BM25 top-30 --------------------------------------------------------------+
dense top-30 -------------------------------------------------------------+-> pool audit
hybrid top-30 ------------------------------------------------------------+
                                                                         |
                              +------------------------------------------+----------------------------------+
                              |                                                                             |
                     any top-30 unjudged                                                        all Judged@30 = 1
                              |                                                                             |
                  BLOCKED sentinel only                                                       atomic metric bundle
                              +------------------------------------------+----------------------------------+
                                                                         |
                                                  reports -> manifest -> validation fixed point
```

Qrels only enters offline evaluation and never enters BM25, E5, RRF or online answer paths; RRF only reads the official rankings of BM25 and dense.

Historical annotation retriever reproduction is a bypass provenance audit. It can read annotation-only query fields for the purpose of reproducing historical processes, but its runs never enter official BM25, E5 or RRF.

### 9.2 Strict query serializer

`sqlmend-query-v1` only allows:

- `dialect`
- `version`
- `user_problem`
- `sql`
- Actual observed `error_message`, `error_code`, `sqlstate`, `error_symbol`

The following fields must not enter formal searches: `expected_behavior`, setup/schema/seed, error category, root cause, reference fix, evidence, source link, case flags, verification, qrels, or candidate ranks.

Serialized output:

```text
retrieval/baseline/serialized_queries/dev_250_queries.jsonl
sha256=e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c
```

BM25 and dense must share the same serialized text.

### 9.3 Three sets of frozen baseline

| System | Fixed Design |
|---|---|
| BM25 | `rank_bm25.BM25Okapi==0.2.2`; `k1=1.5`, `b=0.75`, top 30; lowercase; no stemming/stopword removal; SQL-aware tokenizer |
| Dense | `intfloat/e5-base-v2`, revision `f52bf8ec8c7124536f0efb74aca902b2995e5bcd`; 768 dimensions; exact prefix `"query: "` / `"passage: "` (including trailing space); CPU 14 threads; dynamic-int8; max 256 tokens; L2-normalized float32; exact inner product |
| Hybrid | Only integrates formal BM25/dense top 30; RRF `k=60`; output 30; tie-break is RRF score, best component rank, `chunk_id` |

Each formal run must cover exactly 250 queries, and each query has exactly 30 results; the rank is continuous, the chunk is unique, the score is limited, and the chunk belongs to the frozen corpus.

The current official run hashes (annotation maintains consistent bytes before and after):

```text
BM25   e72361668fc3338abac657a04c598eb36983e8a8201e506e34084d474e268f98
Dense  eeada87a6e1457f91a577e8c6d7a3d60cb59854523a4e31a4fff81b023513cdd
Hybrid 05a907f5ab05c3e09aad872d8523db74fd61c77bf34a4108e55c7c9fc667a468
```

The three-way repeated official operations are all byte consistent. Do not modify the baseline YAML in place for parameter adjustment; when developing the official retrieval v1, you should create an independent v1 configuration, system ID, artifact name, and verification contract.

The frozen run tags of the current baseline are still `bm25_formal_v1`, `dense_formal_v1` and `hybrid_rrf_formal_v1`. These strings are compatibility identifiers for older artifacts and have been bound by byte hashes with the annotation v1 provenance; the `v1` in them no longer represents the retrieval release version. To avoid breaking auditability, this naming correction does not rewrite these historical tags in-place; official retrieval v1 must use a new system ID that explicitly contains dialect/version-aware identity.

### 9.4 Source code responsibilities

| Module | Main Responsibilities |
|---|---|
| `paths.py` | Discover the repository root and centrally define all input/output paths |
| `hashing.py` | File, canonical JSON, directory tree, protected paths and retrieval source snapshot hashes |
| `corpus.py` | Freeze corpus validation, sorting and passage rendering |
| `queries.py` | Query whitelisting, serialization and leak isolation |
| `tokenization.py` | SQL-aware lexical tokenizer |
| `bm25.py` | BM25 indexing, binding and deterministic search |
| `dense.py` | pinned E5, dynamic-int8 encoding, embedding binding and exact search |
| `rrf.py` | Two-channel fixed RRF and component ranks |
| `trec.py` | canonical six-column TREC run reading, writing and verification |
| `qrels.py` | JSONL/TREC qrels and supplemental merge |
| `pool_audit.py` | Judged@K, unjudged semantics and pool expansion requests |
| `metrics.py` | nDCG, MRR, pooled Recall, Precision, HitRate, Judged@K |
| `slices.py` | Construct dialect/error/flag slices only by explicit fields |
| `bootstrap.py` | query-level bootstrap, paired comparison, CI |
| `latency.py` | latency/QPS/index size/runtime environment |
| `reproduction.py` | Historical annotation BM25/BGE/RRF independent reproduction |
| `reporting.py` | failure analysis, provenance, manifest and human readable reports |
| `validation.py` | Perform independent release validation on existing bytes and contracts; do not execute the model |
| `cli.py` | Command orchestration, exit codes and finalize fixed-point convergence |

Adding a fourth official retriever does not require registering a plug-in; it requires at least simultaneous modification of `paths.py`, `cli.py`, `pool_audit.py`, `validation.py`, `reporting.py` and the three-system explicit contract under test.

## 10. Official Top-30 Development Evaluation: Complete

Currently, the official runs of BM25, dense, and hybrid are fixed and have not changed due to label maintenance. Their Top-30 union has a total of 14,232 query/chunk pairs, all of which have now entered the annotation v1 main qrels: the original 10,003 missing pairs have been completed, and all cutoffs in the three paths are unjudged.

Authoritative documents:

- [judged coverage](retrieval/baseline/evaluation/judged_coverage.json)
- [pool summary](retrieval/baseline/pool_expansion/pool_expansion_summary.json)
- [overall metrics](retrieval/baseline/evaluation/overall_metrics.json)
- [pairwise differences](retrieval/baseline/evaluation/pairwise_differences.json)
- [annotation sensitivity](annotation/codex/reports/top30_annotation_sensitivity.json)

Completeness:

| System | Judged@5 | Judged@10 | Judged@20 | Judged@30 | top-30 Unjudged number of occurrences |
|---|---:|---:|---:|---:|---:|
| BM25 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| Dense | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| Hybrid | 1.0 | 1.0 | 1.0 | 1.0 | 0 |

Main quality indicators:

| System | graded nDCG@10 | MRR@10 rel2 | pooled Recall@10 rel2 | HitRate@5 rel2 |
|---|---:|---:|---:|---:|
| BM25 | 0.2649 | 0.3806 | 0.4232 | 0.480 |
| Dense | 0.2398 | 0.3614 | 0.3870 | 0.456 |
| Hybrid | **0.3070** | **0.4319** | **0.5002** | **0.544** |

The four paired bootstrap 95% CIs of Hybrid compared to BM25 and dense are all higher than 0. Therefore, "hybrid is the strongest system in the current fixed baseline" is established based on this set of machine development annotations. The CI of the pairwise difference between BM25 and dense contains 0, so we cannot claim that BM25 is definitely better than dense.

A, B and final verdict version qrels all choose hybrid on the four main indicators; the maximum absolute fluctuation of the main indicator is 0.024, and the maximum fluctuation in the system of nDCG is 0.00832. This shows that the current system ranking is almost unaffected by A/B single-shot machine judgment fluctuations, but it cannot measure the model bias shared by the two-round machine labeling. All conclusions still belong only to **machine-proposed development evaluation**.

Any Recall can only be written as **pooled Recall** because the denominator comes from a finite pool rather than exhausting 12,000 chunks. After adding a retriever, if its Top-30 introduces undetermined pairs, the evaluation will be re-BLOCKED; missing qrels must not be regarded as relevance 0.

## 11. History annotation retriever provenance

The system used in the historical annotation stage is different from the current baseline: historical BM25 is `k1=1.2`, historical dense is `BAAI/bge-small-en-v1.5`, and then historical RRF is done. The current baseline is `k1=1.5` BM25 + pinned E5 + two-way RRF.

The current independent reproduction results are:

| Historical system | exact Top-30 sequence | exact Top-30 set | mean Top-30 overlap | status |
|---|---:|---:|---:|---|
| BM25 | 250/250 | 250/250 | 1.0000 | `PASS` |
| BGE dense | 148/250 | 247/250 | 0.9996 | `PARTIAL` |
| historical RRF | 216/250 | 247/250 | 0.9996 | `PARTIAL` |

Therefore, empirical ranking reproduction and total provenance are conservatively recorded as `PARTIAL`. Major limitations include:

- Historical binding does not prove the exact source code bytes of the builder in memory at that time;
- All transitive dependencies of historical ONNX/tokenizer/runtime are not fully locked;
- There is minimal numerical drift in the dense score under current ONNX/runtime, and the historical neural tie behavior does not have an explicit `chunk_id` tie-breaker.

See [annotation reproduction report](retrieval/baseline/reproduction/reproduction_report.json) for authoritative details. This `PARTIAL` does not prevent the formal baseline, because the formal search does not read historical candidate ranks, nor does it use qrels or annotation evidence in the search; the formal three-way run itself will still be byte consistent if it is run repeatedly. Modifying `reproduction.py` or related input may invalidate the cache and trigger hours of recalculation.

## 12. Current baseline testing, performance and native snapshots

### 12.1 Retrieval test evidence (current checkout: `PASS`)

Authoritative file: [test results](retrieval/baseline/reports/test_results.json).

```text
95 tests PASS in 49.79 s
Python 3.12.7
source_file_count=41
source_tree_sha256=cc89618c684b849b256c4a74ee71c11efb9d9ed36217a19e9d046e026d0f8552
source_stable_during_tests=true
evidence_applies_to_current_checkout=true
```

Formal test evidence must be generated via the CLI `test`; manual pytest is only suitable for development diagnostics and cannot replace `test_results.json`.

### 12.2 Performance Snapshot

Authoritative document: [latency report](retrieval/baseline/evaluation/latency.json). Current environment is Windows 11, CPU-only, 20 logical CPUs, ~34 GB RAM. Different hardware should not be directly compared.

| System | Mean | P95 | QPS |
|---|---:|---:|---:|
| BM25 warm | 214.59 ms | 293.11 ms | 4.66 |
| Dense total warm | 45.57 ms | 55.04 ms | 21.95 |
| Hybrid total warm | 260.55 ms | 348.00 ms | 3.84 |
| RRF fusion only | 0.396 ms | 0.502 ms | 2,524.96 |

Other one-time costs:

```text
BM25 cold start=0.556 s
Dense cold start=33.627 s
BM25 index build=1.630 s
Dense corpus encoding=1141.272 s
Dense model load/download=9.585 s
```

The cold-start scope of `benchmark` includes index/model load and frozen corpus/config binding checks, but does not include process startup. Do not run heavy CPU tasks in parallel when measuring performance.

## 13. Retrieval installation, reconstruction and exit code

Requires Python 3.11+. `retrieval/baseline/pyproject.toml` and `retrieval/baseline/requirements.txt` have fixed direct runtime/test dependencies, but the warehouse does not have a complete lockfile; the build system (such as `setuptools>=69`, `wheel`) and transitive dependencies are not all locked accurately.

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

`python -m sqlmend_retrieval.cli all` executes the same dependency chain, but the first run will download the model, build E5 embeddings, and may redo historical BGE reproduction, which takes a long time.

On the current full pool, `finalize` and `validate` should return 0 if the project, assessment integrity and quality gates all pass. Do not manually modify the source hash in the report; any retrieval source changes still require a formal `test -> finalize -> validate` refresh chain.

If running from outside the repository, `--root` must be placed before the subcommand:

```powershell
python -m sqlmend_retrieval.cli --root C:\path\to\SQLMend-RAG verify-inputs
```

Exit code semantics:

- `evaluate` returns 0 only to indicate that the evaluation process is completed correctly; whether it can be published is still based on the generation status and validator;
- If the Top-30 of any formal run is unjudged, `evaluate` will generate `BLOCKED` sentinel, `finalize`, `validate` and `all` will return non-zero;
- When the pool is complete but the quality gate is not reached, the project can be `PASS` and the retrieval quality is `FAIL`;
- Project `FAIL` and evaluation `BLOCKED` must be handled separately.

After the qrels or official run changes, rerun from `check-pool`, and then run `evaluate`, necessary `benchmark`, `test`, after audit, `finalize`, `validate` in sequence.

## 14. Quality access control after Pool is complete

Only three-way `Judged@30=1.0` will be released atomically:

- `overall_metrics.json`
- `per_query_metrics.csv`
- `slice_metrics.csv`
- `confidence_intervals.json`
- `pairwise_differences.json`
- `complementarity_report.json`

The quality goals for a fixed baseline are:

- hybrid graded nDCG@10 is at least 0.01 higher than the best single system;
- hybrid pooled Recall@10_rel2 is no less than the best single system minus 0.01;
- hybrid HitRate@5_rel2 is not lower than the best single system minus 0.01;
- Unexplained dialect slice regression above 0.05 is not allowed.

Quality `FAIL` is a measurement conclusion and does not equal an engineering implementation error. Failures must not be hidden by changing qrels, queries, slice definitions, models, or RRF parameters.

## 15. Subsequent development route

Subsequent work is divided into two non-confusing data tracks:

1. **Machine development regression set**: The current annotation v1 revision 1.1.0 has completely covered the fixed baseline Top-30, which can be used for the development comparison of formal retrieval v1; new candidates brought by the new system must be supplemented and versioned first, and missing standards cannot be regarded as 0;
2. **Final manual held-out data**: The team separately collects and annotates at least 1,000 records with no duplication and as balanced as possible; three annotators are recommended, two are acceptable, IAA >= 80%; original annotations, annotators, disagreements, adjustment and manifest are saved; parameters are not allowed to be adjusted repeatedly.

These jobs are not all serial dependencies. The current status and possible advancement direction are:

1. `VERIFIED_COMPLETE_FOR_DEVELOPMENT_ONLY` **Official retrieval v1**: The frozen baseline comparison is retained, and the results of the five systems have been released; any future new retriever that expands the candidate pool must still complete the blind supplementary judgment of the same caliber first;
2. `COMPLETE_WITH_FAILED_ENGINEERING_GATES_FOR_DEVELOPMENT_ONLY` **Generation Baseline/Generation v1**: 500 formal wrappers, offline comparisons and all real failures of the two systems have been sealed; the quality target passed, but Phase success=false, it must not be rewritten as overall pass;
3. `PANNED_NEXT` **human evaluation protocol**: freezes the held-out split, schema, guide, sampling, balancing, annotator and adjudication processes before any relevant parameter adjustment;
4. `PANNED` **UI/product scaffolding**: Build a simple UI based on the frozen Retrieval v1 interface and Generation v1 output schema.

Then proceed according to their respective dependencies:

1. Keep the three version axes of retrieval v1, generation v1 and annotation v1 independent, and do not confuse them with the abandoned annotation v2; future innovations must use the new system ID/config/artifact and continue to retain the baseline/v1 comparison;
2. Freeze and execute the final manual held-out protocol; the current 250 pieces of machine data and the same model offline judge cannot replace manual gold, IAA or final testing;
3. Implement a simple UI. Load and keep hot search indexes and models when the service starts, and do not execute the CLI on a request-by-request basis;
4. Complete task indicators, faithfulness, answer relevance, context precision/relevance, as well as latency, throughput, cost, and scalability evaluation on the final artificial set in one go;
5. Prepare 5 representative queries, results, sources and query speeds;
6. Update the end-user README and prepare Q1-Q5 reports, screenshots, two compressed package links and Week 13 demonstration;
7. If the 9 invalid-JSON failures or judge failures of Generation v1 are specifically fixed in the future, a new generation release must be released and this real Baseline must be retained, and the existing 500 wrappers must not be overwritten.

The existing 250 pieces of Codex machine data can be used as development data for prompt design, regression checking, and offline training with clearly documented provenance; it must not serve as final test data, nor must it contaminate the final held-out split.

Production inference and final testing must not read qrels, reference answers, candidate-pool ranks, annotation evidence, or held-out labels to affect searches or answers. Offline training, fine-tuning, or instruction-tuning can use clearly split non-test training data, but the split and provenance must be preserved and strictly isolated from the final held-out set.

## 16. Change Impact Matrix

| Modified content | Must be rerun or updated |
|---|---|
| This article only | No need to rerun retrieval; just submit this article |
| Root `README.md` | Not in retrieval source snapshot; verified by end user experience |
| `retrieval/baseline/README.md` | `test -> finalize -> validate` |
| retrieval source code, test, config, requirements, pyproject | related build/run/eval; formal `test`; after audit; `finalize -> validate` |
| query serializer or allowed fields | serialized queries, two retriever runs, RRF, pool, evaluation, test, finalize, validate |
| corpus | This is a new data version; protected files cannot be modified in place. New version and rebuild all indexes, runs, qrels binding, evaluation and reporting |
| annotation qrels, candidate pool, schema or provenance | Modify only in explicit data maintenance versions; update `annotation/VERSION_HISTORY.md`, run annotation validator, re-anchor protected audit, and rerun the evaluation and finalization chain from `check-pool` |
| supplemental qrels | Rerun the full evaluation and finalization chain starting from `check-pool` |
| New retriever | New system ID/config/artifact; update pool/evaluation/reporting/validation/tests; retain baseline control |
| RRF constant or tie-break | New hybrid version; rebuild hybrid, pool, evaluation, tests, reports; must not overwrite baseline |
| Historical reproduction implementation or input | Re-audit; may trigger BGE recalculation for several hours |
| Generation v1 README, config, schema, source code, test or prompt | Existing manifest and formal evidence are invalid; use new release or press `all --clean` to completely rebuild 500 wrappers, judge, test, finalize, validate, do not modify the report by hand |
| Generation v1 official runs, judgments, metrics, reports or manifests | No manual modification is allowed; rebuild through the corresponding complete chain of Generation v1 CLI, and keep the real failure record |
| UI | New modules, independent dependencies and tests; updated article, final README and course report |

## 17. Don’ts and common pitfalls

- Do not modify any bytes of `construction/` or `annotation/codex/`, including cache, unless the user explicitly initiates versioned data maintenance.
- Don't treat missing qrel as relevance 0.
- Don't call 250 pieces of development data, 50 pieces of Codex audit, or mixed effective qrels a manual gold/held-out test.
- Do not publish Recall/nDCG/MRR on the incomplete pool, or use these numbers to tune the model; judged coverage must be rechecked after adding a retriever.
- Do not let official retrievers read reference fixes, evidence, qrels, candidate ranks, or case flags.
- Do not treat historical annotation retrievers as official baselines.
- No manual editing of run, TREC qrels, metrics, reports or manifests; rebuild via corresponding CLI.
- Do not load BM25 pickles from untrusted sources; only load indexes generated by this project and passed hash binding.
- Do not silently replace exact dense search with ANN; ANN should be used as the new system and record recall/latency/index identity.
- Don't reduce generators to a single call to the hosted RAG API.
- Do not perform repeated parameter adjustments on the final artificial held-out data.
- Do not declare the entire AI6127 job complete.

## 18. Authoritative Evidence Index

### Knowledge-base construction

- [Construction README](construction/README.md)
- [Construction validation](construction/reports/validation_report.json)
- [Construction statistics](construction/reports/corpus_statistics.json)
- [Construction completion report](construction/reports/completion_report.md)

### Machine development annotations

- [Annotation version history](annotation/VERSION_HISTORY.md)
- [Annotation README](annotation/codex/README.md)
- [Annotation manifest](annotation/codex/manifest.json)
- [Annotation validation](annotation/codex/validation_report.json)
- [Annotation statistics](annotation/codex/statistics.json)
- [Annotation provenance](annotation/codex/provenance/)
- [Top-30 blind refresh provenance](annotation/codex/provenance/top30_blind_refresh.json)
- [Annotation sensitivity](annotation/codex/reports/top30_annotation_sensitivity.json)

### Formal retrieval baseline

- [Retrieval README](retrieval/baseline/README.md)
- [Retrieval manifest](retrieval/baseline/manifest.json)
- [Retrieval validation](retrieval/baseline/reports/validation_report.json)
- [Retrieval completion report](retrieval/baseline/reports/completion_report.md)
- [Baseline report](retrieval/baseline/reports/baseline_report.md)
- [Failure analysis](retrieval/baseline/reports/failure_analysis.md)
- [Provenance audit](retrieval/baseline/reports/provenance_audit.md)
- [Judged coverage](retrieval/baseline/evaluation/judged_coverage.json)
- [Pool summary](retrieval/baseline/pool_expansion/pool_expansion_summary.json)
- [Pool expansion requests](retrieval/baseline/pool_expansion/pool_expansion_required.jsonl)
- [Overall metrics](retrieval/baseline/evaluation/overall_metrics.json)
- [Confidence intervals](retrieval/baseline/evaluation/confidence_intervals.json)
- [Pairwise differences](retrieval/baseline/evaluation/pairwise_differences.json)
- [Complementarity](retrieval/baseline/evaluation/complementarity_report.json)
- [Latency](retrieval/baseline/evaluation/latency.json)
- [Test evidence](retrieval/baseline/reports/test_results.json)

### Retrieval v1

- [Retrieval v1 README](retrieval/retrieval-v1/README.md)
- [Retrieval v1 manifest](retrieval/retrieval-v1/manifest.json)
- [Retrieval v1 validation](retrieval/retrieval-v1/reports/validation_report.json)
- [Five-system report and cases](retrieval/retrieval-v1/reports/retrieval_v1_report.md)
- [Acceptance gates](retrieval/retrieval-v1/evaluation/acceptance.json)
- [Five-system comparison](retrieval/retrieval-v1/evaluation/comparison_results.json)
- [Judged coverage](retrieval/retrieval-v1/evaluation/judged_coverage.json)
- [Pool expansion summary](retrieval/retrieval-v1/pool_expansion/pool_expansion_summary.json)
- [Pool expansion requests](retrieval/retrieval-v1/pool_expansion/pool_expansion_required.jsonl)
- [Latency](retrieval/retrieval-v1/reports/latency.json)
- [Test evidence](retrieval/retrieval-v1/reports/test_results.json)
- [Protected before/after audits](retrieval/retrieval-v1/reports/protected_paths_after.json)

### Generation v1

- [Generation v1 README](generation/generation-v1/README.md)
- [Generation v1 report](generation/generation-v1/reports/generation_v1_report.md)
- [Generation v1 manifest](generation/generation-v1/manifest.json)
- [Generation v1 validation](generation/generation-v1/reports/validation_report.json)
- [Acceptance gates](generation/generation-v1/evaluation/acceptance.json)
- [Overall metrics](generation/generation-v1/evaluation/overall_metrics.json)
- [Paired per-query comparison](generation/generation-v1/evaluation/per_query_comparison.jsonl)
- [Generation Baseline README](generation/baseline/README.md)
- [Baseline formal run](generation/baseline/runs/baseline_closed_book_dev250.jsonl)
- [Baseline manifest](generation/baseline/manifest.json)
- [Generation v1 formal run](generation/generation-v1/runs/generation_v1_rag_dev250.jsonl)
- [Offline judge journal](generation/generation-v1/evaluation/judgments.jsonl)
- [Naming migration provenance](generation/generation-v1/provenance/system_naming_migration.json)
- [Test evidence](generation/generation-v1/reports/test_results.json)
- [Protected before/after/current audit](generation/generation-v1/reports/protected_paths_current.json)

## 19. Maintenance Agreement for this document

Update this article every time there are changes to stage status, frozen inputs, interface contracts, primary blockers, or class interpretations. Ordinary internal refactoring does not need to be updated if it does not change the facts that developers need to know.

Update steps:

1. Record `Last verified`, current branch/HEAD and related artifact generation time;
2. Rerun the tests and validators of the affected modules; unverified facts are marked as `UNVERIFIED` or `STALE`;
3. Update "Current Project Overview", data identity, status object, blocker, next step and evidence path;
4. Dynamic figures only retain summaries and do not copy large sections of automatic reports;
5. Do not rewrite important decisions silently and append decision log below;
6. Confirm that the root README remains user-facing and this article remains developer-facing.

Suggested future-work status words: `PANNED`, `IN_PROGRESS`, `BLOCKED`, `VERIFIED_COMPLETE`.

## 20. Decision log

| Date | Decision | Causes and Effects |
|---|---|---|
| 2026-08-29 | `Assignment.pdf` is higher than stage prompts and development convenience | Any conflicts are subject to course requirements; stage boundaries do not cancel final UI, generation and manual evaluation requirements |
| 2026-08-30 | 250 Codex data and its Top-30 judgments are only used for machine-proposed development data | Double-blind machine annotation and machine judgment reduce the fluctuation of single judgment, but cannot be deducted from 1,000+ manual records or held-out test |
| 2026-08-30 | annotation The current main version is v1, dataset revision 1.1.0; the v2 experiment is abandoned | This round of explicitly authorized data maintenance directly updates the main v1; the history is recorded in `annotation/VERSION_HISTORY.md`, and will be refrozen after the maintenance is completed |
| 2026-08-30 | missing qrel is always unjudged, not relevance 0 | The current fixed three-way Top-30 is complete; if a new retriever is added in the future, if unjudged candidates are introduced, indicator release must be blocked again |
| 2026-08-30 | The current retrieval system is named baseline; the official system for dialect and version awareness is named retrieval v1 | baseline is fixed to BM25 + pinned E5 + two-channel RRF; subsequent innovations must not cover baseline |
| 2026-08-29 | Root `README.md` is reserved for end-user documentation; root `DEVELOPMENT_CONTEXT.md` maintains internal state | Avoid mixing handover details, temporary blockers and user installation documentation |
| 2026-08-30 | The current retrieval baseline formal evidence is rebinded and passed | source tree SHA is `104d6f59...`; 95 tests, protected after audit, `finalize -> validate` together constitute the current evidence |
| 2026-08-30 | Retrieval v1 uses soft metadata bonuses and deterministic field-aware lexical reranker | No hard deletion of cross-dialect or old documents; version conflicts are only based on corpus metadata/clear text boundaries; the reranker only reads legal online fields and passages, and does not use development tags |
| 2026-08-30 | Retrieval v1 five-system release through formal clean reconstruction | Five-system `Judged@30=1.0`, pool expansion is 0, 60 tests, 12/12 validation checks, protected bytes unchanged; the current results are only called machine-proposed development evaluation |
| 2026-08-30 | Phase 10 switched to local `qwen3.5:4b`, accurate digest `2a654d98...e4eefd`, both systems `think=false`; Generation v1 fixed Retrieval v1 Final Top-5 | `gpt-oss-20b` does not provide a formal option to completely turn off reasoning, and users choose to turn off thinking for the purpose of efficiency. Qwen; Baseline / Generation v1 maintains the same model, prompt, schema, decoding and retry policy except whether to receive evidence |
| 2026-08-30 | Generation v1 retains true `quality=PASS`, `engineering=FAIL`, `phase_success=false` | Generation v1 Task Success 68.0% is 17.2pp higher than Baseline 50.8%, but structured validity 96.4% does not reach 98%, offline judge calls 249/250; does not modify reference labels, does not hide failures, does not cover 500 official wrappers |
| 2026-08-30 | The generation system is officially named Generation Baseline and Generation v1, and archived to `generation/baseline/`, `generation/generation-v1/` respectively | Only named metadata is migrated; 500 answers, failures, attempt, latency, model provenance and judge decisions remain unchanged. Historical arm names are only retained in `provenance/legacy/` and migration ledger |
