# SQLMend-RAG Generation Baseline

This directory is the frozen Closed-Book baseline for the Phase 10 generation
comparison. It receives only the serialized, user-observable SQL debugging
input and never opens or receives retrieval evidence.

Canonical identity:

```text
release=generation-baseline
system_id=baseline
method=closed_book
formal_run=generation/baseline/runs/baseline_closed_book_dev250.jsonl
```

The baseline intentionally shares the Generation v1 implementation, model,
prompt, answer schema, decoding parameters, and retry policy. Keeping one
orchestrator prevents configuration drift; the presence of Retrieval-v1 Top-5
evidence is the only main system variable.

The current formal run contains 250 result wrappers. A successful wrapper
means only that the JSON/schema/citation contract passed; semantic Task Success
is measured in the paired offline evaluation under
`generation/generation-v1/evaluation/`.

Rebuild both the baseline and Generation v1 from the repository root with:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Resolve-Path 'generation\generation-v1\src')
python -m sqlmend_generation_v1.cli --root . all --clean
```

`all --clean` only removes allowlisted generated entries from the two
generation releases. It does not modify construction, annotation, retrieval
baseline, or Retrieval v1 inputs.
