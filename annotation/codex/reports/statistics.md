# SQLMendRAG Codex development-set statistics

> Machine-proposed development data; not human-labelled evaluation data.

- Total cases: 250
- Documented-error cases: 69
- Plausible-but-wrong cases: 214
- Qrel judgments: 13449
- Estimated near-duplicate record rate: 0.0000%

## Cases per dialect

| Dialect | Cases | Dialect-sensitive | Version-sensitive |
|---|---:|---:|---:|
| postgresql | 50 | 17 | 10 |
| mysql | 50 | 38 | 10 |
| sqlite | 50 | 38 | 10 |
| mariadb | 50 | 34 | 10 |
| duckdb | 50 | 47 | 13 |

## Cases per error category

| Category | Cases |
|---|---:|
| syntax_error | 25 |
| dialect_incompatibility | 25 |
| version_incompatibility | 25 |
| function_or_operator_incompatibility | 25 |
| aggregation_or_grouping | 25 |
| join_or_query_logic | 25 |
| null_semantics | 25 |
| type_or_casting | 25 |
| date_time_semantics | 25 |
| schema_or_identifier_issue | 25 |
