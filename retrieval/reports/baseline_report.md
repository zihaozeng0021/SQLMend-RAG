# SQLMend-RAG 正式基线候选状态报告——尚未完成

数据性质：**machine-proposed development evaluation**。250 条查询和当前 qrels 是机器提出的开发数据，不是 gold、人工标注或 held-out test，也不替代课程要求的 1,000+ 条人工标注。

## 最终状态

- release：`retrieval-baseline-v1-candidate`
- engineering：`PASS`
- evaluation integrity：`BLOCKED`
- retrieval quality：`NOT_EVALUATED`
- annotation reproduction：`PARTIAL`

## 冻结输入身份

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

## 正式配置

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

BM25 与 dense 共用 `sqlmend-query-v1` 严格白名单序列化。Dense 模型精确 revision 是 `f52bf8ec8c7124536f0efb74aca902b2995e5bcd`；检索为 CPU 上的 L2-normalized float32 exact inner product，不使用 ANN。Hybrid 只读取两套正式 top-30 run，并按固定 RRF k=60 融合。

## Run 与 index 身份

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

## Pool completeness

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

```json
{
  "evaluation_integrity_status": "BLOCKED",
  "evaluation_label": "machine-proposed development evaluation",
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
  "unjudged_documents_are_not_relevance_zero": true
}
```

缺失 `(query_id, chunk_id)` judgment 表示未判定，绝不等同于 relevance 0。所有 Recall 指标的严格名称是 **pooled Recall**，分母来自有限 judgment pool，不是 corpus-exhaustive recall。

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

## Quality targets

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

## Latency、throughput、build time 与 index size

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

## 限制与后续工作

- 当前开发标签由 Codex 机器提出，存在循环性与标注误差风险，必须由后续人工标注替换或独立复核。
- 历史 pool 由 BM25、BGE dense 与 source-linked evidence 构造，存在 pooling bias；正式 E5/BM25 的 pool 外结果是预期风险，不可按 0 惩罚。
- 当前 pool expansion required=`True`；补判前不发布 overall、slice、CI、pairwise 或 complementarity 指标，也不据此调参。
- annotation reproduction=`PARTIAL`；逐系统证据和缺失项见 `reports/provenance_audit.md`。
- 本阶段是检索基线，不含方言/版本加权、过滤、reranker、query rewriting、HyDE、SQL 修复或生成。
- AI6127 PDF 的简单 UI、5 条界面演示查询、grounded generator、答案级 RAG 指标、至少 1,000 条人工标注 held-out 数据及标注者一致性至少 80% 仍未完成。

推荐先完成外部补判并冻结有效评估；只有 engineering 与 evaluation integrity 都 PASS 后，才考虑 Stage 7 dialect-aware retrieval。PDF 的 UI、生成与人工测试要求仍是后续独立工作。
