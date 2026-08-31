# Annotation phase retriever source tracking audit

Nature of data: **machine-proposed development evaluation**.

Recall semantic statement: Any Recall can only be called a **pooled Recall**; this source audit itself does not publish retrieval quality indicators.

Status: `PARTIAL`

## Identification and audit methods

The annotation phase system is identified by the protected `provenance/retrieval_config.json`, `provenance/embedding_model.json`, `provenance/retrieval_runs.jsonl` and `candidate_pools.jsonl`. This audit recalculates the rankings independently from frozen corpus/cases and historical configurations; the saved historical run is only used for comparison after the recalculation is completed, and the candidate pool is only used for out-of-pool audits, neither of which is input to the recalculation of the rankings.

The historical query construct contains annotation-only fields such as `expected_behavior`, which are annotation infrastructure cyclic risks that must be disclosed; these fields are only used for reproduction sources and never enter official baselines. The audit input hash is:

```json
{
  "candidate_pools_sha256": "86549c5b1bb59cb1557c747db37c66b77a0812c8a8f9ff02dd2d75c0be87a60f",
  "corpus_sha256": "279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3",
  "embedding_model_sha256": "13a7ce3fa931cd23a575b05221d284f0327ee1820ad30f3a327024ba56d7ee43",
  "implementation_sha256": "5f54379847353ec8c04c9d85483fe24d9b9908f3c389ffd2d467fdd081d0719b",
  "queries_sha256": "2ce81dd27690795266fc5cc813dc1999f8c55d86ed1605fd6e1013213a416fae",
  "retrieval_config_sha256": "95afb9aadea78b8e3020d0e38fe6d33f5edb73aaaae6da52f764d4328345f6bd",
  "snapshot_manifest_sha256": "96de455d68206045c9752118bceced33cb0d65efff2d268ef3462db39dbaa0c5",
  "stored_runs_sha256": "bc68a4755b390f342d972257403655a4d76b6166071c9e6e55747c6a1a55cd51"
}
```

## Settings and independent reproduction results are available

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

- Status: `PASS`
- Available historical configurations: `{"b": 0.75, "k1": 1.2, "top_k": 30}`
- Independent recalculation run SHA-256: `9ff5b86bd011531c73cfa565a244913dab5f18bc012f54a3021b09485763d8ff`
- exact top-30 sequence match: `1.0`
- exact top-30 set match: `1.0`
- mean overlap / Jaccard / RBO: `1.0` / `1.0` / `0.99999999999999999`
- mean Kendall on common docs: `1.0`
- out-of-pool pairs / missing stored docs: `0` / `0`
- score differences: `{'compared_common_scores': 7500, 'exact_after_8_decimal_rounding_count': 7500, 'exact_after_8_decimal_rounding_rate': 1.0, 'maximum_absolute_difference': 0.0, 'mean_absolute_difference': 0.0}` (History saving run without score)
- Error or limitation: `None`

## dense

- Status: `PARTIAL`
- Available historical configurations: `{"cache_dir": "annotation/codex/work/model_cache", "dimensions": 384, "method": "fastembed_neural_text_embedding_cosine", "model_name": "BAAI/bge-small-en-v1.5", "resolved_repository": "qdrant/bge-small-en-v1.5-onnx-q", "resolved_revision": "52398278842ec682c6f32300af41344b1c0b0bb2", "snapshot_manifest_sha256": "96de455d68206045c9752118bceced33cb0d65efff2d268ef3462db39dbaa0c5", "top_k": 30}`
- Independent recalculation run SHA-256: `2bf6e3bb0028dda8faab58fbc656954880fdb9bed63506124838b99d89e72c5a`
- exact top-30 sequence match: `0.592`
- exact top-30 set match: `0.988`
- mean overlap / Jaccard / RBO: `0.9996` / `0.999225806451613` / `0.9982014908608511`
- mean Kendall on common docs: `0.9974594417077176`
- out-of-pool pairs / missing stored docs: `1` / `0`
- score differences: `{'compared_common_scores': 7497, 'exact_after_8_decimal_rounding_count': 3, 'exact_after_8_decimal_rounding_rate': 0.00040016006402561027, 'maximum_absolute_difference': 0.0001893699999999665, 'mean_absolute_difference': 3.296958116579968e-05}` (History save run without score)
- Error or limitation: `None`

## hybrid_rrf

- Status: `PARTIAL`
- Available historical configurations: `{"rrf_constant": 60, "top_k": 30}`
- Independent recalculation run SHA-256: `5451788e7e4a058fd5c5f4e888e8000deb8909bf2eda3e2c4e751c1cad49a036`
- exact top-30 sequence match: `0.864`
- exact top-30 set match: `0.988`
- mean overlap / Jaccard / RBO: `0.9996` / `0.999225806451613` / `0.9993888195438829`
- mean Kendall on common docs: `0.9981977011494253`
- out-of-pool pairs / missing stored docs: `0` / `0`
- score differences: `None` (history saved run without score)
- Error or limitation: `None`


## Missing information and limitations

Source integrity status: `PARTIAL`. Explicitly documented limitations: `["the historical binding does not attest the exact in-memory builder source bytes", "historical transitive ONNX/tokenizer/runtime versions are not fully pinned", "historical neural tie behavior has no explicit chunk-ID tie breaker"]`. When a system displays `NOT_REPRODUCIBLE`, the error or dependency reasons are listed on a system-by-system basis; success on other systems cannot be inferred to success on that system. The historical save run does not have a score, so it can only verify the ranking, not the historical floating point score.

## Isolation from official baselines

Formal BM25 uses `rank_bm25`, k1=1.5 and strict user field serializer; formal dense uses fixed revision `intfloat/e5-base-v2`, CPU exact search; formal hybrid only integrates the two sets of official run ranks, with fixed RRF k=60. The official search entry does not read qrels, candidate-pool ranks or annotation evidence, and any historical rankings are not copied into the official run.

## Formal results outside the existing pool

Official run The only query/chunk pair outside the existing judgment pool: `0`; the number of top-30 undetermined occurrences: `0`. If the value is `None`, it means that the source audit occurred before the formal pool audit, and the finalization phase will regenerate this report.
