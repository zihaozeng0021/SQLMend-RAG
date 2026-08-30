# SQLMend-RAG 正式基线检索报告

数据性质：**machine-proposed development evaluation**。250 条查询和当前 qrels 是机器提出的开发数据，不是 gold、人工标注或 held-out test，也不替代课程要求的 1,000+ 条人工标注。

## 最终状态

- release：`retrieval-baseline`
- engineering：`PASS`
- evaluation integrity：`PASS`
- retrieval quality：`PASS`
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
    "effective_count": 23452,
    "effective_sha256": "eae4aefdf6c152df36330a00adf29d8a40d2ea42a476ed7df8c0f675d7446e5d",
    "label_counts": {
      "0": 20154,
      "1": 2839,
      "2": 459
    },
    "protected_source_count": 23452,
    "protected_source_sha256": "bc672f2767762d253e8c9dc239d37d00bdb88a547c0c80585788c8c9021e8d3f",
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
  "evaluation_integrity_status": "PASS",
  "evaluation_label": "machine-proposed development evaluation",
  "machine_proposed_development_only": true,
  "overall": {
    "Judged@10": 1.0,
    "Judged@20": 1.0,
    "Judged@30": 1.0,
    "Judged@5": 1.0,
    "query_count_per_system": 250,
    "system_count": 3
  },
  "per_system": {
    "bm25_formal": {
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "query_count": 250
    },
    "dense_formal": {
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "query_count": 250
    },
    "hybrid_rrf_formal": {
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "query_count": 250
    }
  },
  "pool_expansion_record_count": 0,
  "pool_expansion_required": false,
  "required_Judged@30": 1.0,
  "schema_version": "sqlmend-pool-audit-v1",
  "unjudged_top30_occurrence_count": 0
}
```

```json
{
  "evaluation_integrity_status": "PASS",
  "evaluation_label": "machine-proposed development evaluation",
  "per_system": {
    "bm25_formal": {
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "query_count": 250
    },
    "dense_formal": {
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "query_count": 250
    },
    "hybrid_rrf_formal": {
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "query_count": 250
    }
  },
  "unjudged_documents_are_not_relevance_zero": true
}
```

缺失 `(query_id, chunk_id)` judgment 表示未判定，绝不等同于 relevance 0。所有 Recall 指标的严格名称是 **pooled Recall**，分母来自有限 judgment pool，不是 corpus-exhaustive recall。

## Overall metrics

```json
{
  "evaluation_label": "machine-proposed development evaluation",
  "recall_semantics": "pooled Recall",
  "systems": {
    "bm25": {
      "HitRate@10_rel2": 0.556,
      "HitRate@5_rel1plus": 0.6,
      "HitRate@5_rel2": 0.48,
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "MRR@10_rel1plus": 0.4867571428571429,
      "MRR@10_rel2": 0.38055714285714287,
      "Precision@5_rel1plus": 0.21520000000000003,
      "Precision@5_rel2": 0.13040000000000002,
      "graded_nDCG@10": 0.26486744946895774,
      "pooled_Recall@10_rel1plus": 0.13516372536623864,
      "pooled_Recall@10_rel2": 0.4231809523809524,
      "pooled_Recall@20_rel1plus": 0.1732774786448208,
      "pooled_Recall@20_rel2": 0.4860285714285714,
      "pooled_Recall@5_rel1plus": 0.10158456798136574,
      "pooled_Recall@5_rel2": 0.3581142857142857
    },
    "dense": {
      "HitRate@10_rel2": 0.516,
      "HitRate@5_rel1plus": 0.556,
      "HitRate@5_rel2": 0.456,
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "MRR@10_rel1plus": 0.4399190476190476,
      "MRR@10_rel2": 0.3613793650793651,
      "Precision@5_rel1plus": 0.18880000000000002,
      "Precision@5_rel2": 0.12,
      "graded_nDCG@10": 0.2398033932492553,
      "pooled_Recall@10_rel1plus": 0.11463180611297959,
      "pooled_Recall@10_rel2": 0.38701904761904765,
      "pooled_Recall@20_rel1plus": 0.15236590845809186,
      "pooled_Recall@20_rel2": 0.45135238095238095,
      "pooled_Recall@5_rel1plus": 0.08733121346599511,
      "pooled_Recall@5_rel2": 0.32818095238095235
    },
    "hybrid": {
      "HitRate@10_rel2": 0.628,
      "HitRate@5_rel1plus": 0.676,
      "HitRate@5_rel2": 0.544,
      "Judged@10": 1.0,
      "Judged@20": 1.0,
      "Judged@30": 1.0,
      "Judged@5": 1.0,
      "MRR@10_rel1plus": 0.5496634920634921,
      "MRR@10_rel2": 0.43194126984126985,
      "Precision@5_rel1plus": 0.24159999999999998,
      "Precision@5_rel2": 0.1464,
      "graded_nDCG@10": 0.3069832766458411,
      "pooled_Recall@10_rel1plus": 0.1558465607971132,
      "pooled_Recall@10_rel2": 0.5001619047619047,
      "pooled_Recall@20_rel1plus": 0.194326552823729,
      "pooled_Recall@20_rel2": 0.556495238095238,
      "pooled_Recall@5_rel1plus": 0.11127205140034643,
      "pooled_Recall@5_rel2": 0.4022857142857143
    }
  }
}
```

## Slice metrics

```json
{
  "dialect_rows": [
    {
      "HitRate@10_rel2": "0.62",
      "HitRate@5_rel1plus": "0.7",
      "HitRate@5_rel2": "0.6",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.5386666666666666",
      "MRR@10_rel2": "0.44583333333333336",
      "Precision@5_rel1plus": "0.20400000000000001",
      "Precision@5_rel2": "0.132",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.2862185635046772, 'ci95_lower': 0.22233292728470888, 'ci95_upper': 0.3538805049989738, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.44583333333333336, 'ci95_lower': 0.33333333333333337, 'ci95_upper': 0.5641666666666666, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.4846666666666667, 'ci95_lower': 0.36666666666666664, 'ci95_upper': 0.6, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.6, 'ci95_lower': 0.46, 'ci95_upper': 0.74, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.2862185635046772",
      "pooled_Recall@10_rel1plus": "0.14488697115615273",
      "pooled_Recall@10_rel2": "0.4846666666666667",
      "pooled_Recall@20_rel1plus": "0.17743071187124893",
      "pooled_Recall@20_rel2": "0.5513333333333333",
      "pooled_Recall@5_rel1plus": "0.11078696154655233",
      "pooled_Recall@5_rel2": "0.4406666666666667",
      "query_count": "50",
      "retriever": "bm25",
      "slice_name": "dialect",
      "slice_value": "postgresql",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.58",
      "HitRate@5_rel1plus": "0.7",
      "HitRate@5_rel2": "0.54",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.57",
      "MRR@10_rel2": "0.45399999999999996",
      "Precision@5_rel1plus": "0.332",
      "Precision@5_rel2": "0.2",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.34281086292604374, 'ci95_lower': 0.26313926725889425, 'ci95_upper': 0.42360400458385516, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.45399999999999996, 'ci95_lower': 0.32799999999999996, 'ci95_upper': 0.5806666666666667, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.4482380952380952, 'ci95_lower': 0.3273809523809524, 'ci95_upper': 0.5710059523809523, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.54, 'ci95_lower': 0.4, 'ci95_upper': 0.68, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.34281086292604374",
      "pooled_Recall@10_rel1plus": "0.16090962714767953",
      "pooled_Recall@10_rel2": "0.4482380952380952",
      "pooled_Recall@20_rel1plus": "0.2049463728207816",
      "pooled_Recall@20_rel2": "0.5108095238095238",
      "pooled_Recall@5_rel1plus": "0.12198481479024469",
      "pooled_Recall@5_rel2": "0.4049047619047619",
      "query_count": "50",
      "retriever": "bm25",
      "slice_name": "dialect",
      "slice_value": "mysql",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.44",
      "HitRate@5_rel1plus": "0.42",
      "HitRate@5_rel2": "0.36",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.35854761904761906",
      "MRR@10_rel2": "0.2693809523809524",
      "Precision@5_rel1plus": "0.156",
      "Precision@5_rel2": "0.09600000000000002",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.19827943412940013, 'ci95_lower': 0.13282848756225613, 'ci95_upper': 0.26905242923875566, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.2693809523809524, 'ci95_lower': 0.16699761904761906, 'ci95_upper': 0.378525, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.3183333333333333, 'ci95_lower': 0.21333333333333332, 'ci95_upper': 0.43, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.36, 'ci95_lower': 0.24, 'ci95_upper': 0.48, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.19827943412940013",
      "pooled_Recall@10_rel1plus": "0.1157772089974607",
      "pooled_Recall@10_rel2": "0.3183333333333333",
      "pooled_Recall@20_rel1plus": "0.15838020509874057",
      "pooled_Recall@20_rel2": "0.35833333333333334",
      "pooled_Recall@5_rel1plus": "0.08370907423367377",
      "pooled_Recall@5_rel2": "0.255",
      "query_count": "50",
      "retriever": "bm25",
      "slice_name": "dialect",
      "slice_value": "sqlite",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.66",
      "HitRate@5_rel1plus": "0.7",
      "HitRate@5_rel2": "0.54",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.5761031746031746",
      "MRR@10_rel2": "0.4382698412698413",
      "Precision@5_rel1plus": "0.228",
      "Precision@5_rel2": "0.132",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.2924677254592963, 'ci95_lower': 0.2304573337768849, 'ci95_upper': 0.3552925548140117, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.4382698412698413, 'ci95_lower': 0.3182662698412699, 'ci95_upper': 0.5595238095238095, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.475, 'ci95_lower': 0.36166666666666664, 'ci95_upper': 0.59, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.54, 'ci95_lower': 0.4, 'ci95_upper': 0.68, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.2924677254592963",
      "pooled_Recall@10_rel1plus": "0.1671882503680678",
      "pooled_Recall@10_rel2": "0.475",
      "pooled_Recall@20_rel1plus": "0.2090659512112859",
      "pooled_Recall@20_rel2": "0.5433333333333333",
      "pooled_Recall@5_rel1plus": "0.12572438384243656",
      "pooled_Recall@5_rel2": "0.37",
      "query_count": "50",
      "retriever": "bm25",
      "slice_name": "dialect",
      "slice_value": "mariadb",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.48",
      "HitRate@5_rel1plus": "0.48",
      "HitRate@5_rel2": "0.36",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.39046825396825396",
      "MRR@10_rel2": "0.29530158730158734",
      "Precision@5_rel1plus": "0.15600000000000003",
      "Precision@5_rel2": "0.09200000000000001",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.2045606613253712, 'ci95_lower': 0.14547496910212768, 'ci95_upper': 0.26782448119065666, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.29530158730158734, 'ci95_lower': 0.1876343253968254, 'ci95_upper': 0.4134232142857141, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.38966666666666666, 'ci95_lower': 0.271, 'ci95_upper': 0.515, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.36, 'ci95_lower': 0.24, 'ci95_upper': 0.5, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.2045606613253712",
      "pooled_Recall@10_rel1plus": "0.08705656916183231",
      "pooled_Recall@10_rel2": "0.38966666666666666",
      "pooled_Recall@20_rel1plus": "0.11656415222204697",
      "pooled_Recall@20_rel2": "0.4663333333333333",
      "pooled_Recall@5_rel1plus": "0.06571760549392129",
      "pooled_Recall@5_rel2": "0.32",
      "query_count": "50",
      "retriever": "bm25",
      "slice_name": "dialect",
      "slice_value": "duckdb",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.34",
      "HitRate@5_rel1plus": "0.42",
      "HitRate@5_rel2": "0.34",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.3321349206349206",
      "MRR@10_rel2": "0.24233333333333335",
      "Precision@5_rel1plus": "0.136",
      "Precision@5_rel2": "0.08800000000000001",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.17981914326323128, 'ci95_lower': 0.11550597810936643, 'ci95_upper': 0.25135922576087627, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.24233333333333335, 'ci95_lower': 0.1433250000000001, 'ci95_upper': 0.35168333333333307, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.27466666666666667, 'ci95_lower': 0.16666666666666669, 'ci95_upper': 0.3906666666666667, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.34, 'ci95_lower': 0.22, 'ci95_upper': 0.48, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.17981914326323128",
      "pooled_Recall@10_rel1plus": "0.08960675315023141",
      "pooled_Recall@10_rel2": "0.27466666666666667",
      "pooled_Recall@20_rel1plus": "0.11086332990680817",
      "pooled_Recall@20_rel2": "0.32866666666666666",
      "pooled_Recall@5_rel1plus": "0.07080408359756186",
      "pooled_Recall@5_rel2": "0.25066666666666665",
      "query_count": "50",
      "retriever": "dense",
      "slice_name": "dialect",
      "slice_value": "postgresql",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.6",
      "HitRate@5_rel1plus": "0.68",
      "HitRate@5_rel2": "0.54",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.4631031746031746",
      "MRR@10_rel2": "0.35874603174603176",
      "Precision@5_rel1plus": "0.268",
      "Precision@5_rel2": "0.16400000000000003",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.2785999703368758, 'ci95_lower': 0.21059018868394494, 'ci95_upper': 0.3457735898310677, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.35874603174603176, 'ci95_lower': 0.24874404761904764, 'ci95_upper': 0.4710876984126983, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.43076190476190473, 'ci95_lower': 0.314, 'ci95_upper': 0.5477642857142857, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.54, 'ci95_lower': 0.4, 'ci95_upper': 0.68, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.2785999703368758",
      "pooled_Recall@10_rel1plus": "0.14556090642692138",
      "pooled_Recall@10_rel2": "0.43076190476190473",
      "pooled_Recall@20_rel1plus": "0.1994951000845857",
      "pooled_Recall@20_rel2": "0.5494285714285714",
      "pooled_Recall@5_rel1plus": "0.10085296511642587",
      "pooled_Recall@5_rel2": "0.35223809523809524",
      "query_count": "50",
      "retriever": "dense",
      "slice_name": "dialect",
      "slice_value": "mysql",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.24",
      "HitRate@5_rel1plus": "0.24",
      "HitRate@5_rel2": "0.16",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.19955555555555557",
      "MRR@10_rel2": "0.13135714285714287",
      "Precision@5_rel1plus": "0.11199999999999999",
      "Precision@5_rel2": "0.052000000000000005",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.10883343140790482, 'ci95_lower': 0.05624249819822027, 'ci95_upper': 0.16940821005719592, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.13135714285714287, 'ci95_lower': 0.053547023809523815, 'ci95_upper': 0.22236071428571424, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.14833333333333334, 'ci95_lower': 0.07166666666666667, 'ci95_upper': 0.235, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.16, 'ci95_lower': 0.06, 'ci95_upper': 0.26, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.10883343140790482",
      "pooled_Recall@10_rel1plus": "0.06901837255040916",
      "pooled_Recall@10_rel2": "0.14833333333333334",
      "pooled_Recall@20_rel1plus": "0.10005264383468045",
      "pooled_Recall@20_rel2": "0.18",
      "pooled_Recall@5_rel1plus": "0.047137700019278965",
      "pooled_Recall@5_rel2": "0.09833333333333334",
      "query_count": "50",
      "retriever": "dense",
      "slice_name": "dialect",
      "slice_value": "sqlite",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.66",
      "HitRate@5_rel1plus": "0.68",
      "HitRate@5_rel2": "0.58",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.5831111111111111",
      "MRR@10_rel2": "0.5215238095238095",
      "Precision@5_rel1plus": "0.18",
      "Precision@5_rel2": "0.14",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.2888711995697166, 'ci95_lower': 0.22770816656316806, 'ci95_upper': 0.3524879543086983, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.5215238095238095, 'ci95_lower': 0.39523333333333344, 'ci95_upper': 0.6495238095238095, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.4683333333333334, 'ci95_lower': 0.35666666666666663, 'ci95_upper': 0.58, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.58, 'ci95_lower': 0.44, 'ci95_upper': 0.72, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.2888711995697166",
      "pooled_Recall@10_rel1plus": "0.13292074075349938",
      "pooled_Recall@10_rel2": "0.4683333333333334",
      "pooled_Recall@20_rel1plus": "0.18185106043381905",
      "pooled_Recall@20_rel2": "0.5266666666666666",
      "pooled_Recall@5_rel1plus": "0.10066811560087421",
      "pooled_Recall@5_rel2": "0.38666666666666666",
      "query_count": "50",
      "retriever": "dense",
      "slice_name": "dialect",
      "slice_value": "mariadb",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.74",
      "HitRate@5_rel1plus": "0.76",
      "HitRate@5_rel2": "0.66",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.6216904761904761",
      "MRR@10_rel2": "0.552936507936508",
      "Precision@5_rel1plus": "0.248",
      "Precision@5_rel2": "0.15600000000000003",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.34289322166854797, 'ci95_lower': 0.2773375003615134, 'ci95_upper': 0.4072920424049972, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.552936507936508, 'ci95_lower': 0.4310777777777778, 'ci95_upper': 0.6707234126984125, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.613, 'ci95_lower': 0.495, 'ci95_upper': 0.73, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.66, 'ci95_lower': 0.52, 'ci95_upper': 0.78, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.34289322166854797",
      "pooled_Recall@10_rel1plus": "0.13605225768383664",
      "pooled_Recall@10_rel2": "0.613",
      "pooled_Recall@20_rel1plus": "0.16956740803056594",
      "pooled_Recall@20_rel2": "0.672",
      "pooled_Recall@5_rel1plus": "0.11719320299583458",
      "pooled_Recall@5_rel2": "0.5529999999999999",
      "query_count": "50",
      "retriever": "dense",
      "slice_name": "dialect",
      "slice_value": "duckdb",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.62",
      "HitRate@5_rel1plus": "0.64",
      "HitRate@5_rel2": "0.48",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.5138015873015873",
      "MRR@10_rel2": "0.37535714285714283",
      "Precision@5_rel1plus": "0.188",
      "Precision@5_rel2": "0.11600000000000002",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.2682822065458198, 'ci95_lower': 0.20548015498245842, 'ci95_upper': 0.3366770520656455, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.37535714285714283, 'ci95_lower': 0.26582420634920634, 'ci95_upper': 0.49361527777777775, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.48533333333333334, 'ci95_lower': 0.36731666666666685, 'ci95_upper': 0.6026666666666667, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.48, 'ci95_lower': 0.34, 'ci95_upper': 0.62, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.2682822065458198",
      "pooled_Recall@10_rel1plus": "0.1345399278187002",
      "pooled_Recall@10_rel2": "0.48533333333333334",
      "pooled_Recall@20_rel1plus": "0.17819759382419229",
      "pooled_Recall@20_rel2": "0.5353333333333333",
      "pooled_Recall@5_rel1plus": "0.09348363266457894",
      "pooled_Recall@5_rel2": "0.3746666666666667",
      "query_count": "50",
      "retriever": "hybrid",
      "slice_name": "dialect",
      "slice_value": "postgresql",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.68",
      "HitRate@5_rel1plus": "0.72",
      "HitRate@5_rel2": "0.62",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.6153015873015873",
      "MRR@10_rel2": "0.507888888888889",
      "Precision@5_rel1plus": "0.35600000000000004",
      "Precision@5_rel2": "0.21600000000000003",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.3844606541016802, 'ci95_lower': 0.3069855598537894, 'ci95_upper': 0.46175023497812745, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.507888888888889, 'ci95_lower': 0.38416666666666666, 'ci95_upper': 0.6333333333333333, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.5408095238095239, 'ci95_lower': 0.4193321428571428, 'ci95_upper': 0.6602869047619047, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.62, 'ci95_lower': 0.48, 'ci95_upper': 0.76, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.3844606541016802",
      "pooled_Recall@10_rel1plus": "0.18715237416674238",
      "pooled_Recall@10_rel2": "0.5408095238095239",
      "pooled_Recall@20_rel1plus": "0.2411120386985618",
      "pooled_Recall@20_rel2": "0.6024761904761905",
      "pooled_Recall@5_rel1plus": "0.13474041624162655",
      "pooled_Recall@5_rel2": "0.4460952380952381",
      "query_count": "50",
      "retriever": "hybrid",
      "slice_name": "dialect",
      "slice_value": "mysql",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.38",
      "HitRate@5_rel1plus": "0.44",
      "HitRate@5_rel2": "0.32",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.29669047619047617",
      "MRR@10_rel2": "0.2112142857142857",
      "Precision@5_rel1plus": "0.16",
      "Precision@5_rel2": "0.08800000000000001",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.1732258349441225, 'ci95_lower': 0.11045874340781246, 'ci95_upper': 0.24038818749062021, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.2112142857142857, 'ci95_lower': 0.1191904761904762, 'ci95_upper': 0.30985773809523803, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.2866666666666667, 'ci95_lower': 0.18333333333333332, 'ci95_upper': 0.39666666666666667, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.32, 'ci95_lower': 0.2, 'ci95_upper': 0.46, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.1732258349441225",
      "pooled_Recall@10_rel1plus": "0.11096170756525447",
      "pooled_Recall@10_rel2": "0.2866666666666667",
      "pooled_Recall@20_rel1plus": "0.14855503700469375",
      "pooled_Recall@20_rel2": "0.3516666666666666",
      "pooled_Recall@5_rel1plus": "0.08044939912617258",
      "pooled_Recall@5_rel2": "0.21833333333333332",
      "query_count": "50",
      "retriever": "hybrid",
      "slice_name": "dialect",
      "slice_value": "sqlite",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.72",
      "HitRate@5_rel1plus": "0.82",
      "HitRate@5_rel2": "0.66",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.6886666666666666",
      "MRR@10_rel2": "0.5406666666666667",
      "Precision@5_rel1plus": "0.264",
      "Precision@5_rel2": "0.16400000000000003",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.3636584075954493, 'ci95_lower': 0.29905412474148924, 'ci95_upper': 0.4277159668644931, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.5406666666666667, 'ci95_lower': 0.418, 'ci95_upper': 0.664, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.5566666666666666, 'ci95_lower': 0.4432916666666671, 'ci95_upper': 0.67, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.66, 'ci95_lower': 0.52, 'ci95_upper': 0.78, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.3636584075954493",
      "pooled_Recall@10_rel1plus": "0.19956668081749218",
      "pooled_Recall@10_rel2": "0.5566666666666666",
      "pooled_Recall@20_rel1plus": "0.23283941070198677",
      "pooled_Recall@20_rel2": "0.6166666666666667",
      "pooled_Recall@5_rel1plus": "0.13852273620001815",
      "pooled_Recall@5_rel2": "0.45833333333333337",
      "query_count": "50",
      "retriever": "hybrid",
      "slice_name": "dialect",
      "slice_value": "mariadb",
      "source_field": "dialect"
    },
    {
      "HitRate@10_rel2": "0.74",
      "HitRate@5_rel1plus": "0.76",
      "HitRate@5_rel2": "0.64",
      "Judged@10": "1.0",
      "Judged@20": "1.0",
      "Judged@30": "1.0",
      "Judged@5": "1.0",
      "MRR@10_rel1plus": "0.6338571428571429",
      "MRR@10_rel2": "0.5245793650793651",
      "Precision@5_rel1plus": "0.24",
      "Precision@5_rel2": "0.14800000000000002",
      "confidence_intervals": "{'graded_nDCG@10': {'mean': 0.34528928004213383, 'ci95_lower': 0.283081982001657, 'ci95_upper': 0.4079358178942227, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'MRR@10_rel2': {'mean': 0.5245793650793651, 'ci95_lower': 0.40493313492063493, 'ci95_upper': 0.6443839285714286, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'pooled_Recall@10_rel2': {'mean': 0.6313333333333333, 'ci95_lower': 0.5133333333333333, 'ci95_upper': 0.7446833333333331, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}, 'HitRate@5_rel2': {'mean': 0.64, 'ci95_lower': 0.5, 'ci95_upper': 0.76, 'confidence_level': 0.95, 'bootstrap_samples': 10000, 'random_seed': 42, 'query_count': 50}}",
      "estimate_warning": "",
      "graded_nDCG@10": "0.34528928004213383",
      "pooled_Recall@10_rel1plus": "0.14701211361737676",
      "pooled_Recall@10_rel2": "0.6313333333333333",
      "pooled_Recall@20_rel1plus": "0.1709286838892102",
      "pooled_Recall@20_rel2": "0.6763333333333335",
      "pooled_Recall@5_rel1plus": "0.10916407276933592",
      "pooled_Recall@5_rel2": "0.514",
      "query_count": "50",
      "retriever": "hybrid",
      "slice_name": "dialect",
      "slice_value": "duckdb",
      "source_field": "dialect"
    }
  ],
  "row_count": 69,
  "sha256": "3d2d3de709d5660219eaeaa2dd55f87230b3f1f85f56dcd925b59dfe0e5c4fba",
  "slice_names": [
    "case_flag",
    "dialect",
    "error_category"
  ],
  "status": "PUBLISHED"
}
```

## Confidence intervals

```json
{
  "bm25": {
    "HitRate@5_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.416,
      "ci95_upper": 0.54,
      "confidence_level": 0.95,
      "mean": 0.48,
      "query_count": 250,
      "random_seed": 42
    },
    "MRR@10_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.3273358333333333,
      "ci95_upper": 0.4337925793650793,
      "confidence_level": 0.95,
      "mean": 0.38055714285714287,
      "query_count": 250,
      "random_seed": 42
    },
    "graded_nDCG@10": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.23433369432498227,
      "ci95_upper": 0.2955559431713689,
      "confidence_level": 0.95,
      "mean": 0.26486744946895774,
      "query_count": 250,
      "random_seed": 42
    },
    "pooled_Recall@10_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.37004261904761904,
      "ci95_upper": 0.47609619047619045,
      "confidence_level": 0.95,
      "mean": 0.4231809523809524,
      "query_count": 250,
      "random_seed": 42
    }
  },
  "dense": {
    "HitRate@5_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.396,
      "ci95_upper": 0.52,
      "confidence_level": 0.95,
      "mean": 0.456,
      "query_count": 250,
      "random_seed": 42
    },
    "MRR@10_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.3084144444444445,
      "ci95_upper": 0.4149477380952381,
      "confidence_level": 0.95,
      "mean": 0.3613793650793651,
      "query_count": 250,
      "random_seed": 42
    },
    "graded_nDCG@10": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.20950748173008152,
      "ci95_upper": 0.2710266568789776,
      "confidence_level": 0.95,
      "mean": 0.2398033932492553,
      "query_count": 250,
      "random_seed": 42
    },
    "pooled_Recall@10_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.334,
      "ci95_upper": 0.44081952380952377,
      "confidence_level": 0.95,
      "mean": 0.38701904761904765,
      "query_count": 250,
      "random_seed": 42
    }
  },
  "hybrid": {
    "HitRate@5_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.484,
      "ci95_upper": 0.604,
      "confidence_level": 0.95,
      "mean": 0.544,
      "query_count": 250,
      "random_seed": 42
    },
    "MRR@10_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.3779394047619048,
      "ci95_upper": 0.48512769841269837,
      "confidence_level": 0.95,
      "mean": 0.43194126984126985,
      "query_count": 250,
      "random_seed": 42
    },
    "graded_nDCG@10": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.2752703692689256,
      "ci95_upper": 0.33858329759021855,
      "confidence_level": 0.95,
      "mean": 0.3069832766458411,
      "query_count": 250,
      "random_seed": 42
    },
    "pooled_Recall@10_rel2": {
      "bootstrap_samples": 10000,
      "ci95_lower": 0.44676023809523807,
      "ci95_upper": 0.5536673809523809,
      "confidence_level": 0.95,
      "mean": 0.5001619047619047,
      "query_count": 250,
      "random_seed": 42
    }
  }
}
```

## Pairwise comparisons

```json
[
  {
    "bootstrap_samples": 10000,
    "ci95_lower": -0.056808615900660234,
    "ci95_upper": 0.006283948960286642,
    "confidence_level": 0.95,
    "mean_difference": -0.02506405621970243,
    "metric": "graded_nDCG@10",
    "queries_a_wins": 87,
    "queries_b_wins": 105,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "dense",
    "system_b": "bm25",
    "ties": 58
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": -0.07483123015873015,
    "ci95_upper": 0.03551714285714284,
    "confidence_level": 0.95,
    "mean_difference": -0.019177777777777776,
    "metric": "MRR@10_rel2",
    "queries_a_wins": 56,
    "queries_b_wins": 67,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "dense",
    "system_b": "bm25",
    "ties": 127
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": -0.09239142857142857,
    "ci95_upper": 0.018905714285714272,
    "confidence_level": 0.95,
    "mean_difference": -0.03616190476190476,
    "metric": "pooled_Recall@10_rel2",
    "queries_a_wins": 42,
    "queries_b_wins": 55,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "dense",
    "system_b": "bm25",
    "ties": 153
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": -0.088,
    "ci95_upper": 0.04,
    "confidence_level": 0.95,
    "mean_difference": -0.024,
    "metric": "HitRate@5_rel2",
    "queries_a_wins": 33,
    "queries_b_wins": 39,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "dense",
    "system_b": "bm25",
    "ties": 178
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.022727182745123847,
    "ci95_upper": 0.06146781838266836,
    "confidence_level": 0.95,
    "mean_difference": 0.04211582717688341,
    "metric": "graded_nDCG@10",
    "queries_a_wins": 109,
    "queries_b_wins": 73,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "bm25",
    "ties": 68
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.011423650793650793,
    "ci95_upper": 0.09062075396825395,
    "confidence_level": 0.95,
    "mean_difference": 0.051384126984126983,
    "metric": "MRR@10_rel2",
    "queries_a_wins": 60,
    "queries_b_wins": 41,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "bm25",
    "ties": 149
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.04131333333333335,
    "ci95_upper": 0.11478476190476185,
    "confidence_level": 0.95,
    "mean_difference": 0.07698095238095237,
    "metric": "pooled_Recall@10_rel2",
    "queries_a_wins": 39,
    "queries_b_wins": 14,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "bm25",
    "ties": 197
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.016,
    "ci95_upper": 0.112,
    "confidence_level": 0.95,
    "mean_difference": 0.064,
    "metric": "HitRate@5_rel2",
    "queries_a_wins": 28,
    "queries_b_wins": 12,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "bm25",
    "ties": 210
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.047480532468573276,
    "ci95_upper": 0.08688806504527857,
    "confidence_level": 0.95,
    "mean_difference": 0.06717988339658586,
    "metric": "graded_nDCG@10",
    "queries_a_wins": 127,
    "queries_b_wins": 52,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "dense",
    "ties": 71
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.03580027777777779,
    "ci95_upper": 0.10563373015873016,
    "confidence_level": 0.95,
    "mean_difference": 0.07056190476190477,
    "metric": "MRR@10_rel2",
    "queries_a_wins": 68,
    "queries_b_wins": 29,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "dense",
    "ties": 153
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.07475238095238095,
    "ci95_upper": 0.15382238095238088,
    "confidence_level": 0.95,
    "mean_difference": 0.11314285714285714,
    "metric": "pooled_Recall@10_rel2",
    "queries_a_wins": 56,
    "queries_b_wins": 13,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "dense",
    "ties": 181
  },
  {
    "bootstrap_samples": 10000,
    "ci95_lower": 0.036,
    "ci95_upper": 0.14,
    "confidence_level": 0.95,
    "mean_difference": 0.088,
    "metric": "HitRate@5_rel2",
    "queries_a_wins": 33,
    "queries_b_wins": 11,
    "query_count": 250,
    "random_seed": 42,
    "system_a": "hybrid",
    "system_b": "dense",
    "ties": 206
  }
]
```

## Complementarity

```json
{
  "BM25_HitRate@20_rel2": 0.616,
  "BM25_only_relevance_2_query_hits_at_20": 43,
  "Dense_HitRate@20_rel2": 0.572,
  "Dense_only_relevance_2_query_hits_at_20": 32,
  "diagnostic_investigation": {
    "dense_model_suitability": "The zero-shot dense model was frozen before evaluation; a diagnostic miss is reported rather than tuned away.",
    "normalization": "Document and query embeddings are L2-normalized and tested against cosine equivalence.",
    "query_document_prefix": "The fixed E5 query/document prefixes are asserted by automated tests.",
    "query_truncation": "The fixed maximum input length is recorded; inspect long serialized queries if complementarity targets fail.",
    "representation_independence": "BM25 tokens and dense embeddings use separate implementations; measured Jaccard values quantify overlap."
  },
  "diagnostic_target_status": "PASS",
  "diagnostic_targets": {
    "BM25_only_relevance_2_query_hits_at_20": {
      "observed": 43,
      "passed": true,
      "required_minimum": 5
    },
    "Dense_only_relevance_2_query_hits_at_20": {
      "observed": 32,
      "passed": true,
      "required_minimum": 5
    },
    "oracle_union_HitRate@20_delta_over_best_single": {
      "observed": 0.128,
      "passed": true,
      "required_minimum": 0.02
    }
  },
  "evaluation_label": "machine-proposed development evaluation",
  "mean_Jaccard@10": 0.06278709594499068,
  "mean_Jaccard@20": 0.05923189632477558,
  "median_Jaccard@10": 0.05263157894736842,
  "median_Jaccard@20": 0.02564102564102564,
  "oracle_union_HitRate@10": 0.68,
  "oracle_union_HitRate@20": 0.744,
  "oracle_union_HitRate@20_delta_over_best_single": 0.128,
  "oracle_union_HitRate@5": 0.612,
  "queries_hit_by_both_at_20": 111,
  "queries_missed_by_both_at_20": 64,
  "query_count": 250,
  "relevance_definition": "explicit relevance = 2",
  "unique_relevance_2_chunks_found_by_both": 137,
  "unique_relevance_2_chunks_only_BM25": 74,
  "unique_relevance_2_chunks_only_dense": 60
}
```

## Quality targets

```json
{
  "hybrid_HitRate@5_rel2_minimum": "best(BM25,dense)-0.01",
  "hybrid_graded_nDCG@10_minimum": "best(BM25,dense)+0.01",
  "hybrid_pooled_Recall@10_rel2_minimum": "best(BM25,dense)-0.01",
  "interpretation": "see validation_report.json quality.hybrid_targets",
  "maximum_unexplained_dialect_graded_nDCG@10_regression": 0.05,
  "status": "PASS"
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
- 当前 pool expansion required=`False`；补判前不发布 overall、slice、CI、pairwise 或 complementarity 指标，也不据此调参。
- annotation reproduction=`PARTIAL`；逐系统证据和缺失项见 `reports/provenance_audit.md`。
- 本阶段是检索基线，不含方言/版本加权、过滤、reranker、query rewriting、HyDE、SQL 修复或生成。
- AI6127 PDF 的简单 UI、5 条界面演示查询、grounded generator、答案级 RAG 指标、至少 1,000 条人工标注 held-out 数据及标注者一致性至少 80% 仍未完成。

推荐先完成外部补判并冻结有效评估；只有 engineering 与 evaluation integrity 都 PASS 后，才考虑 Stage 7 dialect-aware retrieval。PDF 的 UI、生成与人工测试要求仍是后续独立工作。
