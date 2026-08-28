# Frozen generation specification

Generate exactly 250 machine-proposed development cases for SQLMendRAG under
`annotation/codex/`. The production knowledge base is the sole primary evidence
source and is read from `construction/data/processed/corpus.jsonl`.

## Identity and split

- IDs are exactly `DEV0001` through `DEV0250`.
- `split` is always `dev`.
- `annotation_source` is always `codex_machine_proposed`.
- `annotation_status` is always `unverified`.
- No artifact may claim to be human-labelled, gold, adjudicated, or eligible for
  the assignment's final manual evaluation requirement.

## Distribution

- PostgreSQL, MySQL, SQLite, MariaDB, DuckDB: exactly 50 each.
- The ten error categories are each represented and no category exceeds 20%.
- Each dialect has at least 15 genuinely dialect-sensitive cases.
- Each dialect has at least 10 genuinely version-sensitive cases.
- At least 30 cases contain a documented database error, error code, SQLSTATE,
  or error symbol.
- At least 50 cases contain plausible-looking but wrong SQL.

Flags are based on material reasoning, not on the mere presence of a dialect or
version string.

## Evidence and qrels

Every evidence `chunk_id` must resolve in the production corpus. Every case has
a primary evidence chunk which is present in its evidence array with relevance
2. Candidate pools are the union of BM25 top-30, neural dense-embedding top-30, and explicitly
source-linked evidence. Every pooled candidate is judged 0, 1, or 2; missing
pairs remain unjudged. Any non-independent machine heuristic used to propose
these labels must be recorded, and metrics computed from it must be disclosed
as circular exploratory diagnostics.

## Query-source leakage

Do not copy explanatory prose from evidence into `user_problem`. SQL, exact
errors, identifiers, product names, and version literals may remain exact when
necessary. After those exemptions, no query may copy more than 12 consecutive
natural-language tokens or a full explanatory sentence from supporting evidence.

## Versions and nulls

Use `version`, `version_min`, `version_max`, and `version_status`. When status is
`unknown`, all three version values are JSON null. Never invent a version.
Compatibility values use `compatible`, `incompatible`, `unknown`, or
`not_applicable`.

## Verification

Execution claims require the exact engine/version, setup, the original outcome,
the repaired outcome, and a semantic assertion over result rows, scalar values,
database state, or schema state. Executability alone is not correctness. Use a
documented-behavior oracle only with `documentation_only` when a matching
runtime is unavailable, and state that limitation truthfully.
