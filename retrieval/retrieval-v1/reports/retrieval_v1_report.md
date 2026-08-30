# SQLMend-RAG Retrieval v1 development report

Schema: `sqlmend-retrieval-v1-report-v1`. All results below are machine-proposed development evaluation on the current 250-query development set. They are not human gold and are not a final held-out test result.

Qrels are joined only for offline evaluation, judged-pool validation, and the displayed offline relevance grades. Online retrieval and reranking do not receive qrels or any reference fix, expected root cause, annotation evidence, or held-out label.

## Overall five-system comparison

| System ID | graded_nDCG@10 | MRR@10_rel2 | pooled_Recall@10_rel2 | HitRate@5_rel2 | Wrong-Dialect@5 | Wrong-Version@5 | Unknown-Version@5 | Judged@30 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hybrid_rrf_frozen_control_v1 | 0.3070 | 0.4319 | 0.5002 | 0.5440 | 0.2704 | 0.0032 | 0.0096 | 1.0000 |
| hybrid_rrf_dialect_aware_v1 | 0.3256 | 0.4532 | 0.5192 | 0.5680 | 0.1008 | 0.0040 | 0.0112 | 1.0000 |
| hybrid_rrf_version_aware_v1 | 0.3175 | 0.4506 | 0.5080 | 0.5760 | 0.1720 | 0.0016 | 0.0080 | 1.0000 |
| hybrid_rrf_dialect_version_aware_v1 | 0.3293 | 0.4689 | 0.5367 | 0.5880 | 0.0944 | 0.0008 | 0.0072 | 1.0000 |
| hybrid_rrf_dialect_version_lexical_rerank_v1 | 0.3456 | 0.4947 | 0.5596 | 0.6320 | 0.0952 | 0.0008 | 0.0088 | 1.0000 |

## Dialect-sensitive and version-sensitive slices

| Slice | System ID | Queries | graded_nDCG@10 | MRR@10_rel2 | pooled_Recall@10_rel2 | HitRate@5_rel2 | Wrong-Dialect@5 | Wrong-Version@5 | Unknown-Version@5 | Judged@30 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dialect-sensitive | hybrid_rrf_frozen_control_v1 | 174 | 0.3318 | 0.4715 | 0.5231 | 0.5920 | 0.2563 | 0.0046 | 0.0138 | 1.0000 |
| dialect-sensitive | hybrid_rrf_dialect_aware_v1 | 174 | 0.3491 | 0.4909 | 0.5389 | 0.6207 | 0.0989 | 0.0046 | 0.0161 | 1.0000 |
| dialect-sensitive | hybrid_rrf_version_aware_v1 | 174 | 0.3414 | 0.4874 | 0.5383 | 0.6207 | 0.1586 | 0.0023 | 0.0115 | 1.0000 |
| dialect-sensitive | hybrid_rrf_dialect_version_aware_v1 | 174 | 0.3534 | 0.5081 | 0.5612 | 0.6264 | 0.0920 | 0.0011 | 0.0103 | 1.0000 |
| dialect-sensitive | hybrid_rrf_dialect_version_lexical_rerank_v1 | 174 | 0.3740 | 0.5394 | 0.5894 | 0.6782 | 0.0931 | 0.0011 | 0.0126 | 1.0000 |
| version-sensitive | hybrid_rrf_frozen_control_v1 | 53 | 0.4313 | 0.6052 | 0.6781 | 0.7547 | 0.1019 | 0.0113 | 0.0113 | 1.0000 |
| version-sensitive | hybrid_rrf_dialect_aware_v1 | 53 | 0.4369 | 0.6146 | 0.6781 | 0.7547 | 0.0226 | 0.0113 | 0.0113 | 1.0000 |
| version-sensitive | hybrid_rrf_version_aware_v1 | 53 | 0.4423 | 0.6324 | 0.6844 | 0.7547 | 0.0642 | 0.0038 | 0.0075 | 1.0000 |
| version-sensitive | hybrid_rrf_dialect_version_aware_v1 | 53 | 0.4504 | 0.6729 | 0.7127 | 0.7736 | 0.0189 | 0.0038 | 0.0038 | 1.0000 |
| version-sensitive | hybrid_rrf_dialect_version_lexical_rerank_v1 | 53 | 0.4783 | 0.6942 | 0.7567 | 0.8302 | 0.0189 | 0.0038 | 0.0075 | 1.0000 |

Compatibility event counts use the fixed Top-5 denominator. On the dialect-sensitive slice, wrong-dialect results move from 223/870 (baseline) to 86/870 (Phase 7) and 81/870 (final). On the version-sensitive slice, explicitly wrong-version results move from 3/265 to 1/265 (version-only) and 1/265 (final); unknown version metadata is reported separately and never counted as incompatible.

## Per-dialect slices

| Dialect | System ID | Queries | graded_nDCG@10 | MRR@10_rel2 | pooled_Recall@10_rel2 | HitRate@5_rel2 | Wrong-Dialect@5 | Wrong-Version@5 | Unknown-Version@5 | Judged@30 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| postgresql | hybrid_rrf_frozen_control_v1 | 50 | 0.2683 | 0.3754 | 0.4853 | 0.4800 | 0.3880 | 0.0000 | 0.0000 | 1.0000 |
| postgresql | hybrid_rrf_dialect_aware_v1 | 50 | 0.2951 | 0.4072 | 0.5153 | 0.5200 | 0.1560 | 0.0040 | 0.0000 | 1.0000 |
| postgresql | hybrid_rrf_version_aware_v1 | 50 | 0.2897 | 0.4016 | 0.4987 | 0.5400 | 0.2920 | 0.0000 | 0.0000 | 1.0000 |
| postgresql | hybrid_rrf_dialect_version_aware_v1 | 50 | 0.3134 | 0.4531 | 0.5587 | 0.5800 | 0.1560 | 0.0000 | 0.0000 | 1.0000 |
| postgresql | hybrid_rrf_dialect_version_lexical_rerank_v1 | 50 | 0.3242 | 0.4763 | 0.5953 | 0.6400 | 0.1560 | 0.0000 | 0.0000 | 1.0000 |
| mysql | hybrid_rrf_frozen_control_v1 | 50 | 0.3845 | 0.5079 | 0.5408 | 0.6200 | 0.1880 | 0.0000 | 0.0000 | 1.0000 |
| mysql | hybrid_rrf_dialect_aware_v1 | 50 | 0.4093 | 0.5364 | 0.5625 | 0.6200 | 0.0400 | 0.0000 | 0.0000 | 1.0000 |
| mysql | hybrid_rrf_version_aware_v1 | 50 | 0.3887 | 0.5255 | 0.5285 | 0.6600 | 0.0400 | 0.0000 | 0.0000 | 1.0000 |
| mysql | hybrid_rrf_dialect_version_aware_v1 | 50 | 0.3863 | 0.5322 | 0.5118 | 0.6600 | 0.0320 | 0.0000 | 0.0000 | 1.0000 |
| mysql | hybrid_rrf_dialect_version_lexical_rerank_v1 | 50 | 0.3962 | 0.5438 | 0.5258 | 0.6600 | 0.0320 | 0.0000 | 0.0000 | 1.0000 |
| sqlite | hybrid_rrf_frozen_control_v1 | 50 | 0.1732 | 0.2112 | 0.2867 | 0.3200 | 0.3160 | 0.0040 | 0.0000 | 1.0000 |
| sqlite | hybrid_rrf_dialect_aware_v1 | 50 | 0.1929 | 0.2314 | 0.3167 | 0.3400 | 0.1200 | 0.0040 | 0.0000 | 1.0000 |
| sqlite | hybrid_rrf_version_aware_v1 | 50 | 0.1709 | 0.2122 | 0.2917 | 0.2800 | 0.2600 | 0.0040 | 0.0000 | 1.0000 |
| sqlite | hybrid_rrf_dialect_version_aware_v1 | 50 | 0.1778 | 0.2096 | 0.3117 | 0.2800 | 0.1160 | 0.0000 | 0.0000 | 1.0000 |
| sqlite | hybrid_rrf_dialect_version_lexical_rerank_v1 | 50 | 0.1918 | 0.2317 | 0.3217 | 0.3200 | 0.1160 | 0.0000 | 0.0000 | 1.0000 |
| mariadb | hybrid_rrf_frozen_control_v1 | 50 | 0.3637 | 0.5407 | 0.5567 | 0.6600 | 0.2120 | 0.0000 | 0.0480 | 1.0000 |
| mariadb | hybrid_rrf_dialect_aware_v1 | 50 | 0.3734 | 0.5613 | 0.5600 | 0.7000 | 0.1120 | 0.0000 | 0.0560 | 1.0000 |
| mariadb | hybrid_rrf_version_aware_v1 | 50 | 0.3790 | 0.5680 | 0.5867 | 0.6800 | 0.1680 | 0.0000 | 0.0400 | 1.0000 |
| mariadb | hybrid_rrf_dialect_version_aware_v1 | 50 | 0.3981 | 0.5984 | 0.6117 | 0.7000 | 0.1000 | 0.0000 | 0.0360 | 1.0000 |
| mariadb | hybrid_rrf_dialect_version_lexical_rerank_v1 | 50 | 0.4083 | 0.6039 | 0.6217 | 0.7400 | 0.1040 | 0.0000 | 0.0440 | 1.0000 |
| duckdb | hybrid_rrf_frozen_control_v1 | 50 | 0.3453 | 0.5246 | 0.6313 | 0.6400 | 0.2480 | 0.0120 | 0.0000 | 1.0000 |
| duckdb | hybrid_rrf_dialect_aware_v1 | 50 | 0.3575 | 0.5298 | 0.6413 | 0.6600 | 0.0760 | 0.0120 | 0.0000 | 1.0000 |
| duckdb | hybrid_rrf_version_aware_v1 | 50 | 0.3591 | 0.5457 | 0.6347 | 0.7200 | 0.1000 | 0.0040 | 0.0000 | 1.0000 |
| duckdb | hybrid_rrf_dialect_version_aware_v1 | 50 | 0.3709 | 0.5512 | 0.6897 | 0.7200 | 0.0680 | 0.0040 | 0.0000 | 1.0000 |
| duckdb | hybrid_rrf_dialect_version_lexical_rerank_v1 | 50 | 0.4074 | 0.6180 | 0.7337 | 0.8000 | 0.0680 | 0.0040 | 0.0000 | 1.0000 |

## Retrieval latency

| System ID | Method | Mean ms | P50 ms | P95 ms | Recorded incremental mean ms | Recorded incremental P50 ms | Recorded incremental P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hybrid_rrf_frozen_control_v1 | frozen_measured_reference | 260.549 | 253.569 | 347.998 | — | — | — |
| hybrid_rrf_dialect_aware_v1 | estimate | 274.202 | 266.648 | 372.535 | 13.652 | 13.079 | 24.537 |
| hybrid_rrf_version_aware_v1 | estimate | 274.424 | 267.186 | 373.207 | 13.874 | 13.618 | 25.208 |
| hybrid_rrf_dialect_version_aware_v1 | estimate | 272.873 | 265.150 | 371.343 | 12.324 | 11.581 | 23.344 |
| hybrid_rrf_dialect_version_lexical_rerank_v1 | estimate | 273.578 | 265.769 | 372.452 | 13.028 | 12.201 | 24.454 |

Frozen Hybrid is a measured end-to-end reference. New-system totals are explicitly componentwise estimates (frozen measured total plus separately measured increments); the reranker-only mean/P50/P95 increment is directly measured on all 250 queries.

Reranking overhead versus dialect+version-aware retrieval, computed from total latency: mean +0.705 ms, P50 +0.619 ms, P95 +1.110 ms.

## Success cases

### Success case 1: DEV0221

- Selection: final − baseline per-query graded nDCG@10 = +0.5948 (0.0000 → 0.5948).
- Safe problem: A PostgreSQL report groups only by a primary key and selects another column from the same table. Why must DuckDB receive a more explicit grouping list?
- Dialect / version: duckdb / 1.5
- Baseline top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_duckdb_6c1bd483b92393642fe7d445 | Announcing DuckDB 1.2.0 | duckdb | 1.2.0 | 0 |
| 2 | smr_mysql_8bc7cc6706aa4d8f9665612c | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 3 | smr_duckdb_2288f7fee3eb27800dc4284b | Announcing DuckDB 1.5.5 | duckdb | 1.5.5 | 0 |

- Final top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_duckdb_391f7ad87e8140c6fd20a9c0 | PostgreSQL Compatibility | duckdb | 1.5 | 2 |
| 2 | smr_duckdb_6c1bd483b92393642fe7d445 | Announcing DuckDB 1.2.0 | duckdb | 1.2.0 | 0 |
| 3 | smr_duckdb_332d76b46dc88b066f98f721 | Tuning Workloads | duckdb | 1.5 | 0 |

### Success case 2: DEV0161

- Selection: final − baseline per-query graded nDCG@10 = +0.4387 (0.1665 → 0.6052).
- Safe problem: A query using a nonrecursive CTE must run on MariaDB 10.2.0, but the parser fails before the inner SELECT is evaluated.
- Dialect / version: mariadb / 10.2.0
- Baseline top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mariadb_017d540b2d956bfd0411008d | MariaDB 10.11.19 Release Notes | mariadb | 10.11.19 | 0 |
| 2 | smr_mariadb_0973ea024c4c765a4e46f6b6 | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 0 |
| 3 | smr_mariadb_276deafcb60c929767e0ca23 | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 0 |

- Final top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mariadb_00cac4471f00aba470f8d53f | MariaDB 10.2.0 Changelog | mariadb | 10.2.0 | 1 |
| 2 | smr_mariadb_c2734c9792f92a6f71484add | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 0 |
| 3 | smr_mariadb_18d2d548c34809bda09da2af | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 2 |

### Success case 3: DEV0163

- Selection: final − baseline per-query graded nDCG@10 = +0.3631 (0.2421 → 0.6052).
- Safe problem: A tag filter works on MariaDB 10.9 but fails after being deployed unchanged to a MariaDB 10.8 server.
- Dialect / version: mariadb / 10.8
- Baseline top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mariadb_4383545265819fb7860ae35d | Incompatibilities and Feature Differences Between MariaDB 10.8 and MySQL 8.0 | mariadb | unknown | 1 |
| 2 | smr_mariadb_017d540b2d956bfd0411008d | MariaDB 10.11.19 Release Notes | mariadb | 10.11.19 | 0 |
| 3 | smr_mariadb_09527c0a1b8a1d9e6ec6bd0f | Upgrading from MariaDB 10.7 to MariaDB 10.8 | mariadb | current | 0 |

- Final top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mariadb_4383545265819fb7860ae35d | Incompatibilities and Feature Differences Between MariaDB 10.8 and MySQL 8.0 | mariadb | unknown | 1 |
| 2 | smr_mariadb_18520deb52145d98c39151ae | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 0 |
| 3 | smr_mariadb_03e06b6baffde2add8dd2463 | JSON\\_OVERLAPS | mariadb | current | 2 |

## Failure cases

### Failure case 1: DEV0082

- Selection: final − baseline per-query graded nDCG@10 = -0.2349 (0.4514 → 0.2165).
- Safe problem: A cleanup report is meant to select profiles that have a biography, yet comparing the column to NULL never identifies them.
- Dialect / version: mysql / 8.0.46
- Baseline top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mysql_39330123d5504a4ef198bd19 | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 2 | smr_mysql_2c8c26361f0da2fcceca4b55 | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 3 | smr_mysql_8d999ee1e231b559eda52bfa | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 1 |

- Final top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mysql_39330123d5504a4ef198bd19 | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 2 | smr_mysql_827625242c6020e2a6878eca | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 3 | smr_mysql_2c8c26361f0da2fcceca4b55 | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |

### Failure case 2: DEV0095

- Selection: final − baseline per-query graded nDCG@10 = -0.2124 (0.6967 → 0.4842).
- Safe problem: A PostgreSQL weekday extraction must be rewritten for MySQL, with Sunday numbered 1 and Saturday numbered 7.
- Dialect / version: mysql / 8.0.46
- Baseline top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mysql_3dcd5132e638f620f2f1b292 | MySQL Community 8.4.11 Built-in HELP Catalog server-side help | mysql | 8.4.11 | 2 |
| 2 | smr_mysql_82a08d58eac768242e956a9a | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 3 | smr_mysql_5d51edce5db63f13166af74f | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 2 |

- Final top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mysql_82a08d58eac768242e956a9a | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 0 |
| 2 | smr_mysql_5d51edce5db63f13166af74f | MySQL Community 8.0.46 Built-in HELP Catalog server-side help | mysql | 8.0.46 | 2 |
| 3 | smr_mysql_3dcd5132e638f620f2f1b292 | MySQL Community 8.4.11 Built-in HELP Catalog server-side help | mysql | 8.4.11 | 2 |

### Failure case 3: DEV0174

- Selection: final − baseline per-query graded nDCG@10 = -0.2060 (0.5467 → 0.3407).
- Safe problem: A list aggregation copied from another function passes the comma as a second expression, producing repeated commas instead of a delimiter setting.
- Dialect / version: mariadb / 11.4.10
- Baseline top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mariadb_2302cf20660148d664577f51 | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 2 |
| 2 | smr_sqlite_1aa5b8ab8569532293746651 | Built-in Aggregate Functions | sqlite | 3.53.4 | 0 |
| 3 | smr_mariadb_232c9712db27849fd84681d2 | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 0 |

- Final top documents (relevance is an offline development judgment):

| Rank | Chunk | Title | Dialect | Version | Offline relevance grade |
| --- | --- | --- | --- | --- | --- |
| 1 | smr_mariadb_2302cf20660148d664577f51 | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 2 |
| 2 | smr_mariadb_232c9712db27849fd84681d2 | MariaDB Community Server 11.4.10 Built-in HELP Catalog server-side help | mariadb | 11.4.10 | 0 |
| 3 | smr_sqlite_1aa5b8ab8569532293746651 | Built-in Aggregate Functions | sqlite | 3.53.4 | 0 |

## Design and acceptance conclusions

- Dialect awareness is a soft metadata ranking signal: matching dialects are preferred, explicitly incompatible dialects are penalized, unknown metadata is retained, and cross-dialect evidence is never categorically removed.
- Version awareness applies the conservative order compatible, general without a known conflict, unknown, then explicitly incompatible. It uses only corpus-owned version metadata or explicit passage statements and does not invent support ranges.
- The reranker uses only frozen safe-query fields and candidate passages. It blends corpus-IDF BM25 evidence from problem, SQL, and observed-error fields plus exact error-code/SQLSTATE/symbol matches into the dialect+version score with deterministic ties. This promotes passages that directly name the failing construct or observed error while retaining the compatibility prior, which explains the measured top-10 gain.
- Evaluation integrity: PASS; Judged@30=1.0000. Qrels are used only in this offline report and never by online ranking.
- Acceptance summary: phase7=PASS, phase8=PASS, phase9=PASS, final=PASS; retrieval quality=PASS.
- Targets not met: none; every configured Phase 7/8/9 and final gate passed.

| Scope | Gate | Observed | Required | Result |
| --- | --- | --- | --- | --- |
| phase7 | Dialect-sensitive graded nDCG@10 delta versus frozen Hybrid | 0.0173 | 0.0000 | PASS |
| phase7 | No dialect slice nDCG@10 regression exceeds the configured limit | see component rows | configured composite gate | PASS |
| phase7 | Overall graded nDCG@10 delta versus frozen Hybrid | 0.0186 | -0.0100 | PASS |
| phase7 | Dialect-sensitive Wrong-Dialect@5 relative reduction versus frozen Hybrid | 0.6143 | 0.3000 | PASS |
| phase8 | Combined overall graded nDCG@10 delta versus Phase 7 | 0.0037 | -0.0100 | PASS |
| phase8 | Combined overall pooled Recall@10_rel2 delta versus frozen Hybrid | 0.0365 | -0.0100 | PASS |
| phase8 | Version-sensitive graded nDCG@10 delta for version-only ablation | 0.0109 | 0.0000 | PASS |
| phase8 | Version-sensitive Wrong-Version@5 relative reduction for version-only ablation | 0.6667 | 0.3000 | PASS |
| phase9 | mrr_delta | 0.0259 | — | reported |
| phase9 | ndcg_delta | 0.0163 | — | reported |
| phase9 | one_primary_gain_and_other_preserved | see component rows | configured composite gate | PASS |
| phase9 | Reranked pooled Recall@10_rel2 delta versus combined unreranked | 0.0229 | -0.0100 | PASS |
| phase9 | sensitive_slice_regressions | see component rows | configured composite gate | PASS |
| final | Final overall MRR@10_rel2 delta versus frozen Hybrid | 0.0628 | 0.0000 | PASS |
| final | Final overall graded nDCG@10 delta versus frozen Hybrid | 0.0386 | 0.0200 | PASS |
| final | Final overall pooled Recall@10_rel2 delta versus frozen Hybrid | 0.0595 | -0.0100 | PASS |
| final | Final dialect-sensitive Wrong-Dialect@5 relative reduction | 0.6368 | 0.3000 | PASS |
| final | Final version-sensitive Wrong-Version@5 relative reduction | 0.6667 | 0.3000 | PASS |
