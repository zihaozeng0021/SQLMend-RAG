# SQLMendRAG 知识库流水线完成报告

生成日期：2026-08-27。知识库构建流水线现已整体放在项目的 `construction/` 模块中。下文未加前缀的路径均相对于 `construction/`；从项目根目录看，请在前面加上 `construction/`。这里报告的是当前固定快照，不是以后重跑时保证永远不变的数字；重跑后请以同目录下自动生成的 JSON/CSV 报告为准。

## 1. 文件路径

入口和配置：

- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `config/sources.yaml`
- `config/chunking.yaml`

项目根目录的 `.gitignore` 是所有阶段共用的，不属于 construction 模块。

核心实现：

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

分阶段入口：

- `scripts/collect/collect.py`
- `scripts/parse/parse.py`
- `scripts/clean/clean.py`
- `scripts/enrich_metadata/enrich.py`
- `scripts/deduplicate/deduplicate.py`
- `scripts/chunk/chunk.py`
- `scripts/statistics/statistics.py`
- `scripts/validate/validate.py`

数据产物：

- `data/raw/postgresql/`、`data/raw/mysql/`、`data/raw/sqlite/`、`data/raw/mariadb/`、`data/raw/duckdb/`
- `data/raw/collection_index.jsonl`：列出 8,284 个原始文件的精确路径和采集索引
- `data/interim/parsed_documents.jsonl`
- `data/interim/cleaned_documents.jsonl`
- `data/interim/enriched_documents.jsonl`
- `data/interim/deduplicated_documents.jsonl`
- `data/processed/corpus.jsonl`：生产语料
- `data/processed/corpus_fixed.jsonl`：固定长度实验基线

报告：

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

测试文件是 `tests/conftest.py`、`tests/test_parsers.py`、`tests/test_metadata.py`、`tests/test_metadata_dedup.py`、`tests/test_deduplication.py`、`tests/test_chunking.py`、`tests/test_chunking_statistics.py`、`tests/test_manifest_validation.py` 和 `tests/test_statistics_validation.py`。原始文件、中间文件、归档和固定长度基线可重复生成，因此默认不进 Git；生产语料和审计报告会保留。没有混入后续标注数据。

## 2. 重建命令

```powershell
cd construction
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m sqlmend_pipeline.cli build
```

复用现有下载时运行：

```powershell
python -m sqlmend_pipeline.cli build --skip-collect
```

单独验证和测试：

```powershell
python -m sqlmend_pipeline.cli validate
python -m pytest -q
```

README 还列出了 collect、parse、clean、enrich、deduplicate、chunk、statistics、validate 的逐阶段命令。采集器有超时、重试、限速、URL 去重、确定性命名、哈希校验、断点复用和原子写入。

## 3. 语料统计

| 指标 | 结果 |
|---|---:|
| 原始文档 | 8,284 |
| 清洗文档 | 8,189 |
| 生产 chunks | 12,000 |
| 总词数 | 1,663,145 |
| 近似唯一词数 | 35,646 |
| 平均 / 中位 chunk 词数 | 138.5954 / 131 |
| 最小 / 最大 chunk 词数 | 13 / 580 |
| 含 SQL 的 chunks | 3,288（27.4%） |
| 含错误码或错误消息的 chunks | 1,882（15.6833%） |
| 含版本或兼容性提示的 chunks | 3,203（26.6917%） |
| 方言已知率 | 100% |
| 版本已知率 | 95.2083% |

13 词的最小块和 580 词的最大块都是保留语义完整性的原子块例外，例如独立错误记录、函数签名、代码块或表格；普通结构块仍受 `config/chunking.yaml` 的范围约束。

生产语料 SHA-256：`279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3`。

## 4. 每个方言的统计

| 方言 | 文档 | chunks | 占比 | 版本已知 |
|---|---:|---:|---:|---:|
| PostgreSQL | 724 | 2,400 | 20% | 100% |
| MySQL | 6 | 2,400 | 20% | 100% |
| SQLite | 551 | 2,400 | 20% | 100% |
| MariaDB | 878 | 2,400 | 20% | 76.0417% |
| DuckDB | 384 | 2,400 | 20% | 100% |

MySQL 的文档数小，是因为官方 HELP 和错误目录本来就是少数几个大型结构化源码文件；解析后会拆成独立 topic 和错误记录，并不是拿六个短页面重复扩充。最终按 topic、来源、来源类型和版本做确定性的分层轮转，各方言都保留了语法、函数、错误、迁移/兼容和版本信息；细分分布在 `corpus_statistics.json`。

## 5. 版本覆盖

| 方言 | 当前或近期 | 历史/旧版 | 状态分布 |
|---|---|---|---|
| PostgreSQL | 18.6、17.11 | 14.24 手册及 14.x 发布说明 | exact 2,390；range 10 |
| MySQL | 8.4.0–8.4.11 | 8.0.0–8.0.46 | range 2,400 |
| SQLite | 3.53.4 当前手册 | 官方 release log 中 1.x–3.53.4 的可证实发布标题 | exact 2,400 |
| MariaDB | current、11.4.10 | 官方 10.x/11.x 等发布说明 | current 1,169；exact 656；unknown 575 |
| DuckDB | current 1.5 系列 | 0.6.0–1.5.5 官方发布文章 | current 2,076；exact 324 |

MariaDB 的 575 个 unknown 块主要来自跨版本的差异表、策略页或没有绑定单一版本的通用参考页。它们没有被硬猜成某个版本。逐版本的文档数和 chunk 数见 `version_coverage.csv`。

## 6. 来源覆盖

清单共 12 条：6 条标为 `official_project_documentation`，6 条标为 `project_maintained_technical_documentation`，社区来源为 0。最终来源分布如下：

| source_id | 方言 | 类型 | chunks |
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

按来源类型汇总：official_docs 6,838、project_docs 2,163、error_reference 809、migration_guide 1,057、release_notes 1,133。URL、固定版本/提交、哈希和许可说明都在 `config/sources.yaml` 与 `source_coverage.csv`。

## 7. 重复统计

| 阶段 | 输入 | 输出 | 精确移除 | 近似移除 |
|---|---:|---:|---:|---:|
| 文档 | 8,189 | 8,043 | 140 | 6 |
| 结构块候选 | 104,657 | 104,504 | 40 | 113 |
| 合计移除 | — | — | 180 | 119 |

最终生产语料的精确重复数为 0。用规范化 5 词 shingles 的 Jaccard 和确定性 64 位 SimHash 候选法估计，残余近重复率为 0%，低于 3% 门槛。不同方言、不同版本范围和不同错误符号不会互相合并。

## 8. 失败、不可访问和排除项

- 下载失败来源：0；失败 URL：0；不可访问来源：0。
- 解析结果：8,284/8,284；`download_failures.jsonl` 和 `parse_failures.jsonl` 都为空。
- MariaDB 官方仓库中 145 个明确标为 “all rights reserved” 的页面按许可规则排除；这是有记录的主动排除，不是静默下载失败。

## 9. 人工抽查

固定种子 20260827，从每个方言抽 20 条，共 100 条。逐条全文检查结果：连贯且可检索 100/100；38 条带 SQL 或错误线索的适用样本中，保真 38/38。样本 SHA-256 是 `51a6be34b7b86b8d0feb57b744733b2b2dd2c7ea086f333e35e069a61ce2fe71`，详情见 `manual_inspection.json`。

## 10. 已知限制

- 为避免重新分发条款更严格的 Oracle 独立手册，MySQL 只用 Community 源码树里的 GPL HELP 和错误目录；语法/函数/错误很强，但长篇迁移叙述少于其他方言。
- SQLite 没有复制多套高度相似的旧手册；旧版差异主要依靠官方 release log。
- DuckDB 没有稳定的多版本整本手册，旧版覆盖依靠官方发布文章。
- MariaDB 有 575 个无法可靠绑定单一版本的块，按要求保持 unknown；全语料版本已知率仍为 95.2083%。
- SQL、错误和 topic 标签是可解释的词法启发式，可能有少量误报或漏报；不会改写正文。
- 人工检查是确定性随机样本，不等于对 12,000 条逐条人工标注。
- 当前没有实现 BM25、向量索引、检索器或 RAG 生成器，这正是本阶段的边界。

## 11. 为什么这五个系统适合开放、可重复的项目

PostgreSQL、SQLite、MariaDB 和 DuckDB 都有公开项目资料、可下载源码或文档快照，也容易在普通机器上安装。MySQL 限定 Community Edition，并使用可固定提交的 GPL 源码资料。五者还覆盖了服务器数据库、嵌入式数据库和分析型数据库，SQL 方言、错误体系、类型、函数和版本迁移差异足够丰富，很适合做可审计的 SQL 修复检索实验，同时不依赖付费订阅或封闭实验环境。

## 12. 验收清单

硬语料标准：

- PASS — 至少 10,000 chunks：实测 12,000。
- PASS — 至少 100,000 词：实测 1,663,145。
- PASS — 五个目标 RDBMS 全部出现。
- PASS — 每个 RDBMS 至少 1,000 chunks：各 2,400。
- PASS — 单一方言不超过 35%：各 20%。
- PASS — 100% chunks 使用受控方言词表：实测 100%。
- PASS — 至少 90% 有版本或版本范围：实测 95.2083%。
- PASS — 100% chunks 有来源 URL。
- PASS — 100% chunks 能对应来源清单。
- PASS — 最终精确重复率 0%。
- PASS — 估计残余近重复率低于 3%：实测 0%。
- PASS — 100 条随机样本至少 95% 连贯可检索：实测 100%。
- PASS — 适用样本至少 90% 保留 SQL/错误信息：实测 100%。

工程标准：

- PASS — README 给出无隐藏手工步骤的完整重建命令。
- PASS — 自动测试通过：90/90。
- PASS — 严格验证通过：24/24，退出码 0。
- PASS — 统计由流水线自动生成。
- PASS — 来源和版本覆盖 CSV 自动生成。
- PASS — 凭据扫描未发现秘密。
- PASS — raw、interim、processed 清楚分开。
- PASS — 下载和解析失败清单始终生成；当前均为空。
- PASS — 五方言词表在清单、元数据和验证中统一强制执行。

结论：本阶段全部硬标准和工程标准均为 PASS，可以进入后续 BM25/稠密索引实验；尚未实现、也不应在本阶段实现 RAG 生成器。
