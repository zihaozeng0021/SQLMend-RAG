# SQLMendRAG Codex development annotations

This directory contains a 250-record, machine-proposed development set for
SQLMendRAG. It exists to develop and debug retrieval, prompting, generation,
verification, and evaluation code.

It is **not** the assignment's final manually labelled evaluation set. It must
not be counted toward the required 1,000+ human annotations, and it must not be
described as gold, adjudicated, human-labelled, or held-out test data.

## Read-only knowledge-base inputs

All paths are relative to the repository root:

- `construction/data/processed/corpus.jsonl`
- `construction/reports/validation_report.json`

The annotation workflow treats `construction/` as read-only and uses only the
production `corpus.jsonl`, never the fixed-size baseline.

## Main artifacts

- `dev_250.jsonl`: one machine-proposed SQL debugging case per physical line.
- `candidate_pools.jsonl`: the machine-labelled union of BM25, neural dense-embedding, and source-linked candidates.
- `qrels_machine_proposed.jsonl`: one judged query/chunk pair per line, including labels 0, 1, and 2.
- `query_source_leakage.jsonl`: per-case query/source lexical leakage checks.
- `execution_evidence.jsonl`: structured verification summaries extracted from the cases.
- `validation_report.json`: acceptance-criterion PASS/FAIL report.
- `statistics.json` and `statistics.md`: dataset distributions and verification statistics.
- `quality_audit.json`: independent Codex-agent audit of a fixed stratified 50-case sample.
- `validate_annotations.py`: repository-root-compatible validation entry point.
- `manifest.json`: dataset identity, input snapshot, quotas, and artifact hashes.
- `schema/`: machine-readable JSON Schemas.
- `prompts/`: the frozen generation specification.
- `provenance/`: corpus and retrieval run metadata.
- `scripts/`: reproducible pooling, validation, reporting, and audit tools.
- `reports/`: detailed/mirrored validation, statistics, retrieval metrics, leakage, and audit results.

## Relevance judgements

Every candidate in every saved pool has an explicit machine-proposed label:

- `0`: judged irrelevant to solving the case;
- `1`: judged partially useful or contextual;
- `2`: judged directly supportive of the diagnosis, repair, or compatibility claim.

A missing query/chunk pair is unjudged. Missing pairs must never be silently
interpreted as relevance 0. Source-linked evidence labels come from the case
review; other 0/1 labels are deterministic context-overlap/retrieval-agreement
heuristics rather than independent semantic or human judgments. Consequently,
the saved retrieval metrics are circular, exploratory development diagnostics,
not unbiased estimates and not final human evaluation results.

## Sensitive-case targets

The dataset contains exactly 50 cases for each of PostgreSQL, MySQL, SQLite,
MariaDB, and DuckDB. Its machine-proposed flags identify at least 15
dialect-sensitive and 10 version-sensitive cases per dialect. The generation
and audit rules require material dependence and corpus support, but these flags
still require human confirmation before use in final evaluation.

## Verification meaning

Execution verification passes only when the original problem is reproduced,
the proposed repair executes, and a result/state/schema assertion confirms the
intended semantics. Parser success alone is not semantic verification.
Documentation-only cases explicitly say that no matching runtime execution was
performed. SQLite and DuckDB execution cases are additionally bound to frozen
execution-input hashes, replay ledgers, and independently reviewed structured
oracles whose expected and observed values are checked exactly (or by an
explicit numeric predicate).

## Validation and reproducibility

From the repository root, run:

```powershell
python annotation/codex/validate_annotations.py --root .
```

The validator recomputes case/schema checks, query hashes, leakage results,
pool/qrel/run consistency, RRF metrics, audit case hashes, replay input hashes,
and execution-oracle comparisons. A saved `PASS` field is never sufficient on
its own. The ignored neural-model cache is machine-local; its resolved revision
and every snapshot-file hash are frozen in
`provenance/embedding_model.json`.

Rebuilding the neural pool is intentionally separate and expensive:

```powershell
python annotation/codex/scripts/build_candidate_pools.py --root . --dense-backend fastembed
```

After a normal full rebuild, the idempotent provenance binder freezes the final
input, model, and artifact hashes without changing rankings or scores:

```powershell
python annotation/codex/scripts/finalize_retrieval_provenance.py --root . --capture-stage final_after_derived_refresh
```

For the legacy in-flight build used to create this release, the builder process
predated automatic model-manifest output. Its post-hoc sequence is explicitly:
bootstrap binding, `--leakage-only` deterministic refresh, then the final binding
command above. The binding report states this limitation and does not claim the
current builder file hash as proof of the earlier in-memory source image.

```powershell
python annotation/codex/scripts/finalize_retrieval_provenance.py --root . --capture-stage bootstrap_before_derived_refresh
python annotation/codex/scripts/build_candidate_pools.py --root . --leakage-only
python annotation/codex/scripts/finalize_retrieval_provenance.py --root . --capture-stage final_after_derived_refresh
```

`--leakage-only` preserves existing rankings/scores, refuses changed retrieval
query text, and refreshes leakage, deterministic pool judgments, qrels, and
metric reports without re-embedding the 12,000-chunk corpus. Correction, replay,
semantic-oracle review, and promotion scripts form a provenance chain; if an
execution input changes, its replay and bound audit must be regenerated before
validation can pass.
