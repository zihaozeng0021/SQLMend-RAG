# 阶段 5–6 候选状态报告——尚未完成

数据性质：**machine-proposed development evaluation**。

本报告只覆盖正式检索基线，不代表 AI6127 整体课程作业已经完成。当前 release 是 `retrieval-baseline-v1-candidate`；只要 engineering 失败或 evaluation integrity 未 PASS，标题与状态都必须明确写作“尚未完成”。

## 创建的精确文件

以下清单递归枚举项目自有代码、配置、隐藏占位文件（包括 `.gitignore`/`.gitkeep`）与契约产物。下载模型缓存、Python bytecode/`__pycache__`、pytest cache 明确排除；旧版 annotation reproduction 命名残留不进入正式清单。正式 dense 模型快照由 manifest 的目录 tree hash 整体绑定。

- `.gitignore` — `CREATED`
- `README.md` — `CREATED`
- `config/bm25_baseline.yaml` — `CREATED`
- `config/dense_baseline.yaml` — `CREATED`
- `config/evaluation.yaml` — `CREATED`
- `config/hybrid_rrf_baseline.yaml` — `CREATED`
- `config/query_serializer.yaml` — `CREATED`
- `evaluation/complementarity_report.json` — `NOT_PUBLISHED (BLOCKED)`
- `evaluation/confidence_intervals.json` — `NOT_PUBLISHED (BLOCKED)`
- `evaluation/judged_coverage.json` — `CREATED`
- `evaluation/latency.json` — `CREATED`
- `evaluation/overall_metrics.json` — `CREATED`
- `evaluation/pairwise_differences.json` — `NOT_PUBLISHED (BLOCKED)`
- `evaluation/per_query_metrics.csv` — `NOT_PUBLISHED (BLOCKED)`
- `evaluation/run_determinism.json` — `CREATED`
- `evaluation/slice_metrics.csv` — `NOT_PUBLISHED (BLOCKED)`
- `indices/bm25/.gitkeep` — `CREATED`
- `indices/bm25/index.pkl` — `CREATED`
- `indices/bm25/metadata.json` — `CREATED`
- `indices/dense/.gitkeep` — `CREATED`
- `indices/dense/chunk_ids.json` — `CREATED`
- `indices/dense/embeddings.npy` — `CREATED`
- `indices/dense/metadata.json` — `CREATED`
- `manifest.json` — `CREATED_BY_FINALIZE`
- `pool_expansion/pool_expansion_required.jsonl` — `CREATED`
- `pool_expansion/pool_expansion_summary.json` — `CREATED`
- `pyproject.toml` — `CREATED`
- `qrels/pool_expansion_judgments.jsonl` — `OPTIONAL_EXTERNAL_INPUT_NOT_PRESENT`
- `qrels/qrels_effective_dev250.trec` — `CREATED`
- `qrels/qrels_machine_proposed_dev250.trec` — `CREATED`
- `reports/baseline_report.md` — `CREATED_BY_FINALIZE`
- `reports/completion_report.md` — `CREATED_BY_FINALIZE`
- `reports/effective_qrels.json` — `CREATED`
- `reports/failure_analysis.md` — `CREATED`
- `reports/input_validation.json` — `CREATED`
- `reports/protected_paths_report.json` — `CREATED`
- `reports/provenance_audit.md` — `CREATED`
- `reports/test_results.json` — `CREATED`
- `reports/validation_report.json` — `CREATED`
- `reproduction/bm25_annotation_reproduced.trec` — `CREATED`
- `reproduction/dense_annotation_reproduced.trec` — `CREATED`
- `reproduction/hybrid_annotation_reproduced.trec` — `CREATED`
- `reproduction/reproduction_report.json` — `CREATED`
- `requirements.txt` — `CREATED`
- `runs/bm25_formal_dev250.trec` — `CREATED`
- `runs/dense_formal_dev250.trec` — `CREATED`
- `runs/hybrid_rrf_formal_dev250.provenance.jsonl` — `CREATED`
- `runs/hybrid_rrf_formal_dev250.trec` — `CREATED`
- `serialized_queries/dev_250_queries.jsonl` — `CREATED`
- `src/sqlmend_retrieval/__init__.py` — `CREATED`
- `src/sqlmend_retrieval/bm25.py` — `CREATED`
- `src/sqlmend_retrieval/bootstrap.py` — `CREATED`
- `src/sqlmend_retrieval/cli.py` — `CREATED`
- `src/sqlmend_retrieval/corpus.py` — `CREATED`
- `src/sqlmend_retrieval/dense.py` — `CREATED`
- `src/sqlmend_retrieval/hashing.py` — `CREATED`
- `src/sqlmend_retrieval/latency.py` — `CREATED`
- `src/sqlmend_retrieval/metrics.py` — `CREATED`
- `src/sqlmend_retrieval/paths.py` — `CREATED`
- `src/sqlmend_retrieval/pool_audit.py` — `CREATED`
- `src/sqlmend_retrieval/qrels.py` — `CREATED`
- `src/sqlmend_retrieval/queries.py` — `CREATED`
- `src/sqlmend_retrieval/reporting.py` — `CREATED`
- `src/sqlmend_retrieval/reproduction.py` — `CREATED`
- `src/sqlmend_retrieval/rrf.py` — `CREATED`
- `src/sqlmend_retrieval/schemas.py` — `CREATED`
- `src/sqlmend_retrieval/slices.py` — `CREATED`
- `src/sqlmend_retrieval/tokenization.py` — `CREATED`
- `src/sqlmend_retrieval/trec.py` — `CREATED`
- `src/sqlmend_retrieval/validation.py` — `CREATED`
- `tests/conftest.py` — `CREATED`
- `tests/test_corpus_queries.py` — `CREATED`
- `tests/test_hashing_audit.py` — `CREATED`
- `tests/test_metrics.py` — `CREATED`
- `tests/test_pool_audit.py` — `CREATED`
- `tests/test_reporting.py` — `CREATED`
- `tests/test_reproduction.py` — `CREATED`
- `tests/test_rrf.py` — `CREATED`
- `tests/test_slices_bootstrap.py` — `CREATED`
- `tests/test_tokenization_bm25_dense.py` — `CREATED`
- `tests/test_trec_qrels.py` — `CREATED`
- `tests/test_validation.py` — `CREATED`
- `indices/dense/model_cache/` — `EXCLUDED_DOWNLOADED_MODEL_CACHE; aggregate identity is dense_model_snapshot_sha256 in manifest.json`
- `reproduction/model_cache/` — `EXCLUDED_DOWNLOADED_MODEL_CACHE; identity is recorded by the annotation reproduction provenance`
- `**/__pycache__/ and *.py[co]` — `EXCLUDED_BYTECODE_CACHE`
- `**/.pytest_cache/` — `EXCLUDED_TEST_CACHE`

## 执行的精确命令

从仓库根目录、已安装 `retrieval` editable package 的环境依次运行：

1. `python -m sqlmend_retrieval.cli audit-protected-paths --phase before`
2. `python -m sqlmend_retrieval.cli verify-inputs`
3. `python -m sqlmend_retrieval.cli serialize-queries`
4. `python -m sqlmend_retrieval.cli audit-annotation-retrievers`
5. `python -m sqlmend_retrieval.cli build-bm25`
6. `python -m sqlmend_retrieval.cli build-dense`
7. `python -m sqlmend_retrieval.cli run-bm25`
8. `python -m sqlmend_retrieval.cli run-dense`
9. `python -m sqlmend_retrieval.cli run-hybrid`
10. `python -m sqlmend_retrieval.cli check-pool`
11. `python -m sqlmend_retrieval.cli evaluate`
12. `python -m sqlmend_retrieval.cli benchmark`
13. `python -m sqlmend_retrieval.cli test`
14. `python -m sqlmend_retrieval.cli audit-protected-paths --phase after`
15. `python -m sqlmend_retrieval.cli finalize`
16. `python -m sqlmend_retrieval.cli validate`

`test` 子命令内部执行并记录 `python -m pytest retrieval/tests -q -p no:cacheprovider`，且比较测试前后 source tree；单独运行 pytest 只适合开发诊断，不能替代 `reports/test_results.json`。在 pool 未补齐时，`evaluate` 写入 BLOCKED sentinel 并返回 0；`finalize`、`validate`（以及因 `finalize` 阻塞而失败的 `all`）返回非零，这是预期阻塞信号，不是发布成功。

## Corpus、query 与 qrel 验证

```json
{
  "corpus": {
    "approximate_unique_word_types": 35646,
    "chunks": 12000,
    "ordering": "ascending chunk_id",
    "path": "construction/data/processed/corpus.jsonl",
    "sha256": "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3",
    "words": 1663145
  },
  "qrels": {
    "effective_count": 13449,
    "effective_sha256": "04f78adf95ef09821780f74c81db3379870ad0fe65033944cc1877acde1f5a81",
    "label_counts": {
      "0": 9216,
      "1": 3931,
      "2": 302
    },
    "protected_source_count": 13449,
    "protected_source_sha256": "bcc0ef136a7ef06409ddf9a8e9d811ebe39671e4c4ad24e5c67e15b4463a47c6",
    "supplemental_count": 0
  },
  "queries": {
    "count": 250,
    "path": "annotation/codex/dev_250.jsonl",
    "serialized_sha256": "e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c",
    "sha256": "2ce81dd27690795266fc5cc813dc1999f8c55d86ed1605fd6e1013213a416fae"
  }
}
```

查询数：`250`；受保护 qrel 数：`13449`；effective qrel 数：`13449`。Supplemental judgments 只允许进入独立文件，不修改受保护输入。

## 受保护目录前后验证

```json
{
  "after_file_count": 8481,
  "after_tree_sha256": "0d53bc19626850bd469eda2350d117a9fcfb2e5758dd84b9c92a7a60fa15bd26",
  "before_file_count": 8481,
  "before_tree_sha256": "0d53bc19626850bd469eda2350d117a9fcfb2e5758dd84b9c92a7a60fa15bd26",
  "protected_paths_unchanged": true
}
```

## Annotation-reproduction 状态

状态：`PARTIAL`；empirical ranking 状态：`PASS`；provenance completeness：`PARTIAL`。详细系统级比较、配置与缺失项见 `reports/provenance_audit.md`。

## 正式 BM25、dense 与 hybrid 配置

```json
{
  "bm25": {
    "algorithm": "BM25Okapi",
    "b": 0.75,
    "document_template": "sqlmend-passage-v1",
    "k1": 1.5,
    "lowercase": true,
    "retriever_id": "bm25_formal_v1",
    "stemming": false,
    "stopword_removal": false,
    "tokenizer_version": "sqlmend-lexical-v1",
    "top_k": 30
  },
  "dense": {
    "batch_size": 64,
    "cpu_threads": 14,
    "device": "cpu",
    "document_prefix": "passage: ",
    "document_template": "sqlmend-passage-v1",
    "dtype": "float32",
    "max_input_length": 256,
    "model_id": "intfloat/e5-base-v2",
    "model_inference_precision": "dynamic_int8_cpu",
    "model_revision": "f52bf8ec8c7124536f0efb74aca902b2995e5bcd",
    "normalize_embeddings": true,
    "pooling": "mean",
    "query_prefix": "query: ",
    "random_seed": 42,
    "retriever_id": "dense_formal_v1",
    "search": "exact_matrix_multiplication",
    "similarity": "inner_product",
    "top_k": 30
  },
  "evaluation": {
    "bootstrap_samples": 10000,
    "broad_relevance_minimum": 1,
    "confidence_level": 0.95,
    "direct_evidence_relevance": 2,
    "evaluation_label": "machine-proposed development evaluation",
    "graded_ndcg_cutoff": 10,
    "judged_cutoffs": [
      5,
      10,
      20,
      30
    ],
    "random_seed": 42,
    "recall_label": "pooled Recall",
    "required_judged_at_30": 1.0
  },
  "hybrid": {
    "algorithm": "reciprocal_rank_fusion",
    "fusion_depth": 30,
    "output_depth": 30,
    "retriever_id": "hybrid_rrf_formal_v1",
    "rrf_k": 60,
    "tie_breaking": [
      "descending_rrf_score",
      "ascending_best_component_rank",
      "ascending_chunk_id"
    ]
  }
}
```

## Run 与 index hashes

```json
{
  "bm25_index_sha256": "1018016821310c928c013df4c50e370008aa1256319d800084c511d9c3275c7b",
  "bm25_run_sha256": "e72361668fc3338abac657a04c598eb36983e8a8201e506e34084d474e268f98",
  "dense_index_sha256": "6ceaaca0135ec60746a5bb78dedb4e282f35cd239fc8127fae19150b1c93eb91",
  "dense_model_snapshot_sha256": "c1b2a43dea4b23376eab103357f7bfae49745e6a5f83cb5926902737a054ec7b",
  "dense_run_sha256": "eeada87a6e1457f91a577e8c6d7a3d60cb59854523a4e31a4fff81b023513cdd",
  "hybrid_run_sha256": "05a907f5ab05c3e09aad872d8523db74fd61c77bf34a4108e55c7c9fc667a468",
  "repeated_run_hashes": {
    "bm25": {
      "byte_identical": true,
      "first_sha256": "e72361668fc3338abac657a04c598eb36983e8a8201e506e34084d474e268f98",
      "second_sha256": "e72361668fc3338abac657a04c598eb36983e8a8201e506e34084d474e268f98"
    },
    "dense": {
      "byte_identical": true,
      "first_sha256": "eeada87a6e1457f91a577e8c6d7a3d60cb59854523a4e31a4fff81b023513cdd",
      "second_sha256": "eeada87a6e1457f91a577e8c6d7a3d60cb59854523a4e31a4fff81b023513cdd"
    },
    "hybrid": {
      "byte_identical": true,
      "first_sha256": "05a907f5ab05c3e09aad872d8523db74fd61c77bf34a4108e55c7c9fc667a468",
      "second_sha256": "05a907f5ab05c3e09aad872d8523db74fd61c77bf34a4108e55c7c9fc667a468"
    }
  }
}
```

## Metric summary、slice summary、CI、pairwise 与 complementarity

所有 Recall 名称均为 **pooled Recall**。

## Overall metrics

`NOT_PUBLISHED (BLOCKED)`。`evaluation/overall_metrics.json` 只保存阻塞哨兵，不包含检索质量数值。

## Slice metrics

`NOT_PUBLISHED (BLOCKED)`。`evaluation/slice_metrics.csv` 必须不存在，避免把不完整 pool 当成完整评估。

## Confidence intervals

`NOT_PUBLISHED (BLOCKED)`。未运行 paired bootstrap。

## Pairwise comparisons

`NOT_PUBLISHED (BLOCKED)`。未发布 BM25/dense/hybrid 配对差异。

## Complementarity

`NOT_PUBLISHED (BLOCKED)`。正式互补性指标等待 top-30 全部判定。失败分析中的排名观察不等同于此指标。

## Quality-target summary

```json
{
  "hybrid_HitRate@5_rel2_minimum": "best(BM25,dense)-0.01",
  "hybrid_graded_nDCG@10_minimum": "best(BM25,dense)+0.01",
  "hybrid_pooled_Recall@10_rel2_minimum": "best(BM25,dense)-0.01",
  "interpretation": "NOT_EVALUATED because the judgment pool is incomplete",
  "maximum_unexplained_dialect_graded_nDCG@10_regression": 0.05,
  "status": "NOT_EVALUATED"
}
```

## Performance summary

```json
{
  "build_time_and_index_size": {
    "bm25_index_build_seconds": 1.6304979999986244,
    "bm25_index_size_bytes": 12207753,
    "dense_corpus_encoding_seconds": 1141.2718451000037,
    "dense_embedding_index_size_bytes": 37331893,
    "dense_index_build_seconds": 0.06879269999626558,
    "dense_model_cache_size_bytes": 438967710,
    "dense_model_load_or_download_seconds": 9.584548999999242
  },
  "cold_start_seconds": {
    "bm25_seconds": 0.5555454999994254,
    "dense_seconds": 33.62684099999751
  },
  "environment": {
    "clock": "time.perf_counter monotonic high-resolution clock",
    "corpus_chunks": 12000,
    "cpu": "Intel64 Family 6 Model 154 Stepping 3, GenuineIntel",
    "device_used_for_official_run": "cpu",
    "embedding_dimension": 768,
    "gpu": null,
    "logical_cpu_count": 20,
    "operating_system": "Windows-11-10.0.26200-SP0",
    "package_versions": {
      "PyYAML": "6.0.1",
      "huggingface-hub": "0.36.2",
      "numpy": "1.26.4",
      "psutil": "5.9.0",
      "rank-bm25": "0.2.2",
      "sentence-transformers": "5.1.0",
      "torch": "2.13.0",
      "transformers": "4.57.6"
    },
    "physical_cpu_count": 14,
    "python_version": "3.12.7",
    "ram_bytes": 34070847488
  },
  "query_count": 250,
  "repetitions": 1,
  "warm_latency": {
    "bm25": {
      "maximum_ms": 351.766800005862,
      "mean_ms": 214.58680199994706,
      "median_ms": 208.21770000111428,
      "p95_ms": 293.10784000190324,
      "queries_per_second": 4.660118845520829,
      "sample_count": 250
    },
    "dense_exact_vector_search": {
      "maximum_ms": 11.738300003344193,
      "mean_ms": 8.627349599759327,
      "median_ms": 8.580850000726059,
      "p95_ms": 10.030545001063729,
      "queries_per_second": 115.91045296551985,
      "sample_count": 250
    },
    "dense_query_encoding": {
      "maximum_ms": 85.36549999553245,
      "mean_ms": 36.93923159976839,
      "median_ms": 35.743700002058176,
      "p95_ms": 46.124600000985076,
      "queries_per_second": 27.071488947979905,
      "sample_count": 250
    },
    "dense_total": {
      "maximum_ms": 97.10379999887664,
      "mean_ms": 45.56658119952772,
      "median_ms": 44.78075000588433,
      "p95_ms": 55.03689999750349,
      "queries_per_second": 21.945908024593354,
      "sample_count": 250
    },
    "hybrid_rrf_fusion": {
      "maximum_ms": 1.0942000008071773,
      "mean_ms": 0.39604559980216436,
      "median_ms": 0.3897499991580844,
      "p95_ms": 0.5020049989980178,
      "queries_per_second": 2524.9617733400582,
      "sample_count": 250
    },
    "hybrid_total": {
      "maximum_ms": 409.8511000047438,
      "mean_ms": 260.54942879927694,
      "median_ms": 253.56854999336065,
      "p95_ms": 347.99810999902536,
      "queries_per_second": 3.838043340215433,
      "sample_count": 250
    }
  },
  "warmup_queries": 3
}
```

## Pool-expansion 状态

```json
{
  "cutoffs": [
    5,
    10,
    20,
    30
  ],
  "evaluation_integrity_status": "BLOCKED",
  "evaluation_label": "machine-proposed development evaluation",
  "machine_proposed_development_only": true,
  "overall": {
    "Judged@10": 0.518,
    "Judged@20": 0.41506666666666664,
    "Judged@30": 0.3480888888888889,
    "Judged@5": 0.6122666666666666,
    "query_count_per_system": 250,
    "system_count": 3
  },
  "per_system": {
    "bm25_formal": {
      "Judged@10": 0.6024,
      "Judged@20": 0.47,
      "Judged@30": 0.3841333333333333,
      "Judged@5": 0.7152,
      "query_count": 250
    },
    "dense_formal": {
      "Judged@10": 0.3752,
      "Judged@20": 0.3054,
      "Judged@30": 0.2592,
      "Judged@5": 0.4608,
      "query_count": 250
    },
    "hybrid_rrf_formal": {
      "Judged@10": 0.5764,
      "Judged@20": 0.4698,
      "Judged@30": 0.4009333333333333,
      "Judged@5": 0.6608,
      "query_count": 250
    }
  },
  "pool_expansion_record_count": 10003,
  "pool_expansion_required": true,
  "required_Judged@30": 1.0,
  "schema_version": "sqlmend-pool-audit-v1",
  "unjudged_top30_occurrence_count": 14668
}
```

唯一请求写在 `pool_expansion/pool_expansion_required.jsonl`。外部补判写入 `qrels/pool_expansion_judgments.jsonl` 后，流水线只合并当前正式 top-30 union 内、且不与冻结 base qrels 冲突的 query/chunk 对；流水线不会创建或覆盖该人工文件。

## Test evidence

```json
{
  "command": [
    "E:\\MyProgramFiles\\anaconda\\python.exe",
    "-m",
    "pytest",
    "retrieval/tests",
    "-q",
    "-p",
    "no:cacheprovider"
  ],
  "returncode": 0,
  "source_stable_during_tests": true,
  "source_tree_sha256": "d804a637d0c64b2f79170f929d1e9520f37060b13e0edefc797728f438572562",
  "source_tree_sha256_after": "d804a637d0c64b2f79170f929d1e9520f37060b13e0edefc797728f438572562",
  "status": "PASS"
}
```

## 所有未通过检查

```json
[
  {
    "check_id": "evaluation.judged_at_30",
    "explanation": "At least one formal top-30 result is unjudged; evaluation publication is blocked.",
    "recommended_remediation": "Request judgments using pool_expansion_required.jsonl; never map missing qrels to zero.",
    "status": "BLOCKED"
  },
  {
    "check_id": "evaluation.pool_summary",
    "explanation": "Pool expansion is required before formal evaluation metrics may be published.",
    "recommended_remediation": "Complete the requested judgments externally and rerun the pool audit.",
    "status": "BLOCKED"
  }
]
```

`BLOCKED` 表示缺少判断而不能发布指标；它不应被误写为 relevance 0，也不等同于工程实现 FAIL。工程 FAIL 必须先修复；质量 FAIL 只能如实报告，不能在同一开发集上改 qrels、queries、模型或 RRF 来隐藏。

## 所有限制与下一推荐阶段

- 250 条查询与 13,449 条基础 qrels 是 machine-proposed development data，不是最终人工 held-out test。
- 不完整 judgment pool 和历史 pooling bias 阻止可靠的质量比较；先完成独立人工补判，再重跑整个评估与验证链。
- annotation retriever 复现若为 PARTIAL/NOT_REPRODUCIBLE，不能推断未复现系统与历史排名一致。
- 本阶段没有方言/版本感知、reranker、query rewriting、HyDE、SQL 修复、grounded generator 或答案级评估。
- PDF 仍要求简单 UI、5 条界面演示查询、grounded generator、答案级 RAG 指标、至少 1,000 条人工标注，以及标注者一致性至少 80%。

下一步不是直接进入 Stage 7：先补齐当前正式 top-30 judgments，使 evaluation integrity PASS 并冻结有效 baseline；随后才建议 Stage 7 dialect-aware retrieval。课程作业的 UI、生成与最终人工测试集仍须继续完成。

## 最终 status object

```json
{
  "annotation_reproduction_status": "PARTIAL",
  "engineering_status": "PASS",
  "evaluation_integrity_status": "BLOCKED",
  "machine_proposed_development_only": true,
  "pool_expansion_required": true,
  "ready_for_stage_7_dialect_aware_retrieval": false,
  "release": "retrieval-baseline-v1-candidate",
  "retrieval_quality_status": "NOT_EVALUATED"
}
```
