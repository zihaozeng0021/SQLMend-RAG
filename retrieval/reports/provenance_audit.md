# 标注阶段检索器来源追踪审计

数据性质：**machine-proposed development evaluation**。

Recall 语义声明：任何 Recall 都只能称为 **pooled Recall**；本来源审计本身不发布检索质量指标。

状态：`PARTIAL`

## 识别与审计方法

标注阶段系统由受保护的 `provenance/retrieval_config.json`、`provenance/embedding_model.json`、`provenance/retrieval_runs.jsonl` 与 `candidate_pools.jsonl` 共同识别。本审计从冻结 corpus/cases 和历史配置独立重算排名；保存的历史 run 只在重算完成后用于比较，candidate pool 只用于 out-of-pool 审计，二者都不是重算排名的输入。

历史 query 构造包含 `expected_behavior` 等 annotation-only 字段，这是必须披露的标注基础设施循环性风险；这些字段仅用于复现来源，绝不进入正式 baselines。审计输入哈希为：

```json
{
  "candidate_pools_sha256": "0d8a89ad0eb39b3e481e58668c15df9416da69bf750100ec695f5d150f3f8d85",
  "corpus_sha256": "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3",
  "embedding_model_sha256": "13a7ce3fa931cd23a575b05221d284f0327ee1820ad30f3a327024ba56d7ee43",
  "implementation_sha256": "5f54379847353ec8c04c9d85483fe24d9b9908f3c389ffd2d467fdd081d0719b",
  "queries_sha256": "2ce81dd27690795266fc5cc813dc1999f8c55d86ed1605fd6e1013213a416fae",
  "retrieval_config_sha256": "95afb9aadea78b8e3020d0e38fe6d33f5edb73aaaae6da52f764d4328345f6bd",
  "snapshot_manifest_sha256": "96de455d68206045c9752118bceced33cb0d65efff2d268ef3462db39dbaa0c5",
  "stored_runs_sha256": "bc68a4755b390f342d972257403655a4d76b6166071c9e6e55747c6a1a55cd51"
}
```

## 可获得设置与独立复现结果

```json
{
  "bm25": {
    "b": 0.75,
    "k1": 1.2,
    "top_k": 30
  },
  "dense": {
    "cache_dir": "annotation/codex/work/model_cache",
    "dimensions": 384,
    "method": "fastembed_neural_text_embedding_cosine",
    "model_name": "BAAI/bge-small-en-v1.5",
    "resolved_repository": "qdrant/bge-small-en-v1.5-onnx-q",
    "resolved_revision": "52398278842ec682c6f32300af41344b1c0b0bb2",
    "snapshot_manifest_sha256": "96de455d68206045c9752118bceced33cb0d65efff2d268ef3462db39dbaa0c5",
    "top_k": 30
  },
  "model_snapshot": {
    "resolved_repository": "qdrant/bge-small-en-v1.5-onnx-q",
    "resolved_revision": "52398278842ec682c6f32300af41344b1c0b0bb2",
    "snapshot_manifest_sha256": "96de455d68206045c9752118bceced33cb0d65efff2d268ef3462db39dbaa0c5"
  },
  "pooling": "union of BM25, dense embedding, and source-linked case evidence"
}
```

## bm25

- 状态：`PASS`
- 可获得的历史配置：`{"b": 0.75, "k1": 1.2, "top_k": 30}`
- 独立重算 run SHA-256：`9ff5b86bd011531c73cfa565a244913dab5f18bc012f54a3021b09485763d8ff`
- exact top-30 sequence match：`1.0`
- exact top-30 set match：`1.0`
- mean overlap / Jaccard / RBO：`1.0` / `1.0` / `0.9999999999999999`
- mean Kendall on common docs：`1.0`
- out-of-pool pairs / missing stored docs：`0` / `0`
- score differences：`{'compared_common_scores': 7500, 'exact_after_8_decimal_rounding_count': 7500, 'exact_after_8_decimal_rounding_rate': 1.0, 'maximum_absolute_difference': 0.0, 'mean_absolute_difference': 0.0}`（历史保存 run 无 score）
- 错误或限制：`None`

## dense

- 状态：`PASS`
- 可获得的历史配置：`{"cache_dir": "annotation/codex/work/model_cache", "dimensions": 384, "method": "fastembed_neural_text_embedding_cosine", "model_name": "BAAI/bge-small-en-v1.5", "resolved_repository": "qdrant/bge-small-en-v1.5-onnx-q", "resolved_revision": "52398278842ec682c6f32300af41344b1c0b0bb2", "snapshot_manifest_sha256": "96de455d68206045c9752118bceced33cb0d65efff2d268ef3462db39dbaa0c5", "top_k": 30}`
- 独立重算 run SHA-256：`766178797dcc3411a12772fdde585cce37717801483650d4dc83063f3f402164`
- exact top-30 sequence match：`1.0`
- exact top-30 set match：`1.0`
- mean overlap / Jaccard / RBO：`1.0` / `1.0` / `0.9999999999999999`
- mean Kendall on common docs：`1.0`
- out-of-pool pairs / missing stored docs：`0` / `0`
- score differences：`{'compared_common_scores': 7500, 'exact_after_8_decimal_rounding_count': 7500, 'exact_after_8_decimal_rounding_rate': 1.0, 'maximum_absolute_difference': 0.0, 'mean_absolute_difference': 0.0}`（历史保存 run 无 score）
- 错误或限制：`None`

## hybrid_rrf

- 状态：`PASS`
- 可获得的历史配置：`{"rrf_constant": 60, "top_k": 30}`
- 独立重算 run SHA-256：`ad11d4a3e59d32fc0299a5c97dcc63bd0778b0d575e85acdefc72115ac39d148`
- exact top-30 sequence match：`1.0`
- exact top-30 set match：`1.0`
- mean overlap / Jaccard / RBO：`1.0` / `1.0` / `0.9999999999999999`
- mean Kendall on common docs：`1.0`
- out-of-pool pairs / missing stored docs：`0` / `0`
- score differences：`None`（历史保存 run 无 score）
- 错误或限制：`None`


## 缺失信息与限制

来源完整性状态：`PARTIAL`。明确记录的限制：`["the historical binding does not attest the exact in-memory builder source bytes", "historical transitive ONNX/tokenizer/runtime versions are not fully pinned", "historical neural tie behavior has no explicit chunk-ID tie breaker"]`。某个系统显示 `NOT_REPRODUCIBLE` 时，其错误或依赖原因已逐系统列出；不能把其余系统的成功推断成该系统也成功。历史保存 run 没有 score，因此只能核验排名，不能核验历史浮点 score。

## 与正式 baselines 的隔离

正式 BM25 使用 `rank_bm25`、k1=1.5 与严格用户字段 serializer；正式 dense 使用固定 revision 的 `intfloat/e5-base-v2`、CPU exact search；正式 hybrid 只融合这两套正式 run 的 rank，固定 RRF k=60。正式检索入口不读取 qrels、candidate-pool ranks 或 annotation evidence，任何历史 ranking 都未被复制进正式 run。

## 现有 pool 之外的正式结果

正式 run 落在现有 judgment pool 之外的唯一 query/chunk 对数：`10003`；top-30 未判定出现次数：`14668`。若值为 `None`，说明来源审计发生在正式 pool audit 之前，最终化阶段会重新生成本报告。
