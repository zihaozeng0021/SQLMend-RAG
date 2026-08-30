# SQLMend-RAG Generation

Generation releases follow the same baseline/version structure as retrieval:

```text
generation/
├─ baseline/       # Closed-Book generation baseline
└─ generation-v1/  # Retrieval-v1 RAG generation and paired offline evaluation
```

The canonical public system names are **Generation Baseline** and
**Generation v1**. The baseline receives only user-observable SQL debugging
input. Generation v1 uses the same local model, prompt contract, output schema,
decoding settings, and retry policy, with frozen Retrieval-v1 Final Top-5
evidence as the only main experimental difference.

The paired Phase 10 orchestrator remains in `generation/generation-v1/` so one
implementation enforces the shared contract. Its baseline output is written to
`generation/baseline/`; its Retrieval-v1 RAG output is written to
`generation/generation-v1/`.

The original experimental-arm identifiers are retained only in the historical
system-naming migration ledger. They are not canonical release names. No model
answer was regenerated during that metadata-only migration.

- [Generation Baseline](baseline/README.md)
- [Generation v1](generation-v1/README.md)
- [Paired formal report](generation-v1/reports/generation_v1_report.md)

