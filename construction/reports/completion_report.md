# SQLMendRAG knowledge base pipeline completion report

Generation date: 2026-08-27. The knowledge base build pipeline is now integrated into the project's `construction/` module. The unprefixed paths below are relative to `construction/`; from the project root directory, please add `construction/` in front. What is reported here is the current fixed snapshot, not a number that is guaranteed to remain unchanged when re-running in the future; after re-running, please refer to the automatically generated JSON/CSV report in the same directory.

## 1. File path

Entry and configuration:

- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `config/sources.yaml`
- `config/chunking.yaml`

`.gitignore` in the project root directory is common to all stages and does not belong to the construction module.

Core implementation:

- `sqlmend_pipeline/__init__.py`
- `sqlmend_pipeline/constants.py`
- `sqlmend_pipeline/utils.py`
- `sqlmend_pipeline/manifest.py`
- `sqlmend_pipeline/collect.py`
- `sqlmend_pipeline/parsers.py`
- `sqlmend_pipeline/clean.py`
- `sqlmend_pipeline/metadata.py`
- `sqlmend_pipeline/dedup.py`
- `sqlmend_pipeline/chunking.py`
- `sqlmend_pipeline/statistics.py`
- `sqlmend_pipeline/validation.py`
- `sqlmend_pipeline/cli.py`

Staged entrance:

- `scripts/collect/collect.py`
- `scripts/parse/parse.py`
- `scripts/clean/clean.py`
- `scripts/enrich_metadata/enrich.py`
- `scripts/deduplicate/deduplicate.py`
- `scripts/chunk/chunk.py`
- `scripts/statistics/statistics.py`
- `scripts/validate/validate.py`

Data products:

- `data/raw/postgresql/`, `data/raw/mysql/`, `data/raw/sqlite/`, `data/raw/mariadb/`, `data/raw/duckdb/`
- `data/raw/collection_index.jsonl`: Lists the exact path and collection index of 8,284 raw files
- `data/interim/parsed_documents.jsonl`
- `data/interim/cleaned_documents.jsonl`
- `data/interim/enriched_documents.jsonl`
- `data/interim/deduplicated_documents.jsonl`
- `data/processed/corpus.jsonl`: production corpus
- `data/processed/corpus_fixed.jsonl`: Fixed length experimental baseline

Report:

- `reports/collection_report.json`
- `reports/download_failures.jsonl`
- `reports/parse_report.json`
- `reports/parse_failures.jsonl`
- `reports/cleaning_report.json`
- `reports/metadata_report.json`
- `reports/document_duplicate_report.json`
- `reports/chunk_duplicate_report.json`
- `reports/chunking_report.json`
- `reports/corpus_statistics.json`
- `reports/corpus_statistics.md`
- `reports/source_coverage.csv`
- `reports/version_coverage.csv`
- `reports/validation_report.json`
- `reports/inspection_sample.jsonl`
- `reports/manual_inspection.json`
- `reports/completion_report.md`

The test files are `tests/conftest.py`, `tests/test_parsers.py`, `tests/test_metadata.py`, `tests/test_metadata_dedup.py`, `tests/test_deduplication.py`, `tests/test_chunking.py`, `tests/test_chunking_statistics.py`, `tests/test_manifest_validation.py` and `tests/test_statistics_validation.py`. Original files, intermediate files, archives, and fixed-length baselines can be generated repeatedly, so they are not entered into Git by default; production corpus and audit reports will be retained. No subsequent annotation data is mixed in.

## 2. Rebuild command

```powershell
cd construction
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m sqlmend_pipeline.cli build
```

To reuse an existing download run:

```powershell
python -m sqlmend_pipeline.cli build --skip-collect
```

Individually verified and tested:

```powershell
python -m sqlmend_pipeline.cli validate
python -m pytest -q
```

The README also lists the stage-by-stage commands for collect, parse, clean, enrich, deduplicate, chunk, statistics, and validate. The collector has timeouts, retries, rate limits, URL deduplication, deterministic naming, hash verification, breakpoint reuse and atomic writing.

## 3. Corpus statistics

| Indicators | Results |
|---|---:|
| Original document | 8,284 |
| Cleaning Documents | 8,189 |
| production chunks | 12,000 |
| Total words | 1,663,145 |
| Approximate number of unique words | 35,646 |
| Average/median chunk word count | 138.5954 / 131 |
| Minimum/maximum number of chunk words | 13 / 580 |
| Chunks with SQL | 3,288 (27.4%) |
| Chunks with error codes or error messages | 1,882 (15.6833%) |
| Chunks with version or compatibility hints | 3,203 (26.6917%) |
| Dialect known rate | 100% |
| Version known rate | 95.2083% |

The minimum chunk of 13 words and the maximum chunk of 580 words are exceptions to atomic chunks that preserve semantic integrity, such as standalone error records, function signatures, code chunks, or tables; ordinary structural chunks are still subject to the scope of `config/chunking.yaml`.

Production corpus SHA-256: `279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3`.

## 4. Statistics for each dialect

| Dialect | Documentation | chunks | Percentage | Version known |
|---|---:|---:|---:|---:|
| PostgreSQL | 724 | 2,400 | 20% | 100% |
| MySQL | 6 | 2,400 | 20% | 100% |
| SQLite | 551 | 2,400 | 20% | 100% |
| MariaDB | 878 | 2,400 | 20% | 76.0417% |
| DuckDB | 384 | 2,400 | 20% | 100% |

The number of MySQL documents is small because the official HELP and error directories are originally a few large structured source code files; after parsing, they will be split into independent topics and error records, instead of repeatedly expanding them with six short pages. Finally, deterministic hierarchical rotation is performed according to topic, source, source type and version. Each dialect retains syntax, function, error, migration/compatibility and version information; the subdivision is distributed in `corpus_statistics.json`.

## 5. Version coverage

| Dialect | Current or Recent | Historical/Old Version | Status Distribution |
|---|---|---|---|
| PostgreSQL | 18.6, 17.11 | 14.24 manual and 14.x release notes | exact 2,390; range 10 |
| MySQL | 8.4.0–8.4.11 | 8.0.0–8.0.46 | range 2,400 |
| SQLite | 3.53.4 current manual | Verifiable release titles for 1.x–3.53.4 in official release log | exact 2,400 |
| MariaDB | current, 11.4.10 | Official 10.x/11.x and other release notes | current 1,169; exact 656; unknown 575 |
| DuckDB | current 1.5 series | 0.6.0–1.5.5 official release article | current 2,076; exact 324 |

MariaDB's 575 unknown blocks mainly come from cross-version difference tables, policy pages, or common reference pages that are not bound to a single version. They are not hard-guessed into a certain version. See `version_coverage.csv` for the number of documents and chunks per version.

## 6. Source coverage

There are 12 items in the list: 6 tagged `official_project_documentation`, 6 tagged `project_maintained_technical_documentation`, and the community source is 0. The final source distribution is as follows:

| source_id | dialect | type | chunks |
|---|---|---|---:|
| postgresql_18_6_manual | PostgreSQL | official_docs | 735 |
| postgresql_17_11_manual | PostgreSQL | official_docs | 733 |
| postgresql_14_24_manual | PostgreSQL | official_docs | 932 |
| mysql_8_4_help | MySQL | project_docs | 884 |
| mysql_8_4_errors | MySQL | error_reference | 292 |
| mysql_8_0_help | MySQL | project_docs | 931 |
| mysql_8_0_errors | MySQL | error_reference | 293 |
| sqlite_3_53_4_docs | SQLite | official_docs | 2,400 |
| mariadb_docs_snapshot | MariaDB | official_docs | 2,007 |
| mariadb_11_4_10_help | MariaDB | project_docs | 348 |
| mariadb_11_4_10_errors | MariaDB | error_reference | 45 |
| duckdb_docs_snapshot | DuckDB | official_docs | 2,400 |

Aggregated by source type: official_docs 6,838, project_docs 2,163, error_reference 809, migration_guide 1,057, release_notes 1,133. URLs, pinned versions/commits, hashes and licensing instructions are all in `config/sources.yaml` and `source_coverage.csv`.

## 7. Repeat statistics

| Stage | Input | Output | Exact Removal | Approximate Removal |
|---|---:|---:|---:|---:|
| Documents | 8,189 | 8,043 | 140 | 6 |
| Structural Block Candidates | 104,657 | 104,504 | 40 | 113 |
| Total removed | — | — | 180 | 119 |

The exact number of repetitions in the final production corpus is 0. Estimated with Jaccard and deterministic 64-bit SimHash candidates for normalized 5-word shingles, the residual near-duplication rate is 0%, below the 3% threshold. Different dialects, different version ranges, and different error symbols are not merged with each other.

## 8. Failure, Inaccessibility and Exclusions

- Download failed source: 0; failed URL: 0; inaccessible source: 0.
- Parsing results: 8,284/8,284; both `download_failures.jsonl` and `parse_failures.jsonl` are empty.
- 145 pages clearly marked “all rights reserved” in the MariaDB official repository are excluded according to permission rules; this is a recorded active exclusion, not a silent download failure.

## 9. Manual spot check

Fixed seed 20260827, 20 from each dialect, 100 in total. Full-text check results one by one: coherent and searchable 100/100; among 38 applicable samples with SQL or error clues, fidelity 38/38. The sample SHA-256 is `51a6be34b7b86b8d0feb57b744733b2b2dd2c7ea086f333e35e069a61ce2fe71`, see `manual_inspection.json` for details.

## 10. Known limitations

- To avoid redistribution of Oracle's standalone manuals with stricter terms, MySQL only uses GPL HELP and error directories in the Community source tree; syntax/functions/errors are strong, but less lengthy migration narratives than other dialects.
- SQLite does not copy multiple sets of highly similar old manuals; the differences between old versions mainly rely on the official release log.
- DuckDB does not have stable multi-versions of the entire manual, and coverage of older versions relies on official published articles.
- MariaDB has 575 blocks that cannot be reliably bound to a single version and remain unknown as required; the full corpus version known rate is still 95.2083%.
- SQL, error, and topic tags are interpretable lexical heuristics and may have a small number of false positives or false negatives; the text will not be rewritten.
- Manual inspection is a deterministic random sample and does not equal manual labeling of 12,000 items one by one.
- There are currently no implementations of BM25, vector indexes, retrievers or RAG generators, which is the boundary of this phase.

## 11. Why these five systems are suitable for open, repeatable projects

PostgreSQL, SQLite, MariaDB and DuckDB all have public project information, downloadable source code or documentation snapshots, and are easy to install on ordinary machines. MySQL is limited to Community Edition and uses GPL source code that can be submitted. The five also cover server databases, embedded databases and analytical databases. The differences in SQL dialects, error systems, types, functions and version migrations are rich enough, making them suitable for auditable SQL repair retrieval experiments without relying on paid subscriptions or closed experimental environments.

## 12. Acceptance Checklist

Hard corpus standards:

- PASS — At least 10,000 chunks: measured 12,000.
- PASS — Minimum 100,000 words: Measured 1,663,145.
- PASS — All five target RDBMS appear.
- PASS — At least 1,000 chunks per RDBMS: 2,400 each.
- PASS – no more than 35% for a single dialect: 20% each.
- PASS — 100% chunks using controlled dialect vocabulary: 100% measured.
- PASS — At least 90% has version or version range: measured 95.2083%.
- PASS — 100% chunks have source URL.
- PASS — 100% chunks can correspond to the source list.
- PASS — Final exact repeat rate 0%.
- PASS — Estimated residual near-repeat rate less than 3%: measured 0%.
- PASS — 100 random samples at least 95% coherent and searchable: measured 100%.
- PASS — applies to samples at least 90% retained SQL/error messages: measured 100%.

Engineering standards:

- PASS — README gives complete rebuild command with no hidden manual steps.
- PASS — Automatic test passed: 90/90.
- PASS — Strict verification passed: 24/24, exit code 0.
- PASS — Statistics are automatically generated by the pipeline.
- PASS — source and version coverage CSV automatically generated.
- PASS — Credential scan found no secret.
- PASS — raw, intermediate, processed are clearly separated.
- PASS — Download and parse failure lists are always generated; currently both are empty.
- PASS — Five dialect vocabularies are uniformly enforced in manifests, metadata, and validation.

Conclusion: All hard standards and engineering standards at this stage are PASS, and you can enter subsequent BM25/dense index experiments; the RAG generator has not yet been implemented and should not be implemented at this stage.
