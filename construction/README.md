# SQLMendRAG 知识库流水线

这个目录是 SQLMendRAG 的 knowledge base construction 模块：把五种数据库的官方资料抓下来，清洗、补元数据、去重、切块，最后得到可以直接拿去做 BM25 或向量索引的 `corpus.jsonl`。

本文中的 `data/`、`config/`、`reports/` 等路径都相对于 `construction/`。项目级说明在上一级 [README](../README.md)，以后新增的索引、检索、生成或标注模块不要塞进本目录的数据阶段。

## 为什么选这五种数据库

我们只覆盖 PostgreSQL、MySQL Community Edition、SQLite、MariaDB 和 DuckDB。理由很实际：它们常见或增长很快，能免费安装，项目资料公开，实验室里也容易复现。PostgreSQL、SQLite、MariaDB、DuckDB 本身都是开放项目；MySQL 这里限定 Community Edition，并优先使用它公开的 GPL 源码资料。

## 现在仓库里有什么

```text
construction/
  data/
    raw/{postgresql,mysql,sqlite,mariadb,duckdb}/  # 原文 + 采集元数据
    interim/                                      # 解析、清洗、去重中间产物
    processed/
      corpus.jsonl                                # 生产语料：结构感知分块
      corpus_fixed.jsonl                          # 固定长度实验基线
  scripts/{collect,parse,clean,deduplicate,enrich_metadata,chunk,validate,statistics}/
  sqlmend_pipeline/                               # 可测试、可复用的核心代码
  config/sources.yaml                             # 来源、版本、许可、固定哈希
  config/chunking.yaml                            # 分块、去重、平衡、验收阈值
  reports/                                        # 统计、覆盖率、失败记录、验证结果
  tests/
```

`data/raw` 里的每个 JSON 都保留原文、原始标题、来源 URL、UTC 抓取时间、方言、版本、来源类型、本地路径、内容 SHA-256 和字符编码信息。原始压缩包放在对应方言的 `.archives/` 目录，断点重跑时会先验哈希再复用。

顺便说明一下 Git 边界：`raw`、`interim`、下载归档和固定长度基线都能由命令重新生成，所以默认不跟踪；生产 `corpus.jsonl`、配置、代码、统计、覆盖率、验证结果和 100 条审计样本会保留。工作区里的 raw/中间产物不会因此被删掉。

## 数据来源和许可说明

机器可读的完整清单在 [config/sources.yaml](config/sources.yaml)。每条来源都明确写了 `authority_class`，区分社区文章和官方资料。

| 系统 | 主要来源 | 版本处理 | 来源级别 / 许可要点 |
|---|---|---|---|
| PostgreSQL | 官方发布归档里的 HTML 或 SGML 手册 | 18.6、17.11、14.24 分开保留 | 官方项目文档；保留 PostgreSQL 文档许可中的版权和免责声明 |
| MySQL Community | 官方 `mysql/mysql-server` 固定提交里的 HELP 表和错误目录 | 8.4.0–8.4.11、8.0.0–8.0.46 | 项目维护技术文档，源文件是 GPLv2 |
| SQLite | 官网发布的 3.53.4 静态 HTML 包 | 当前手册是 3.53.4；历史版本从 release log 标题提取 | 官方项目文档；SQLite 说明其代码和文档属于 public domain |
| MariaDB | 官方文档仓库固定提交，加 11.4.10 HELP/错误目录 | 通用页标 `current`；发布说明提取 10.x/11.x 精确版本；结构目录标 11.4.10 | 官方/项目维护资料；逐页记录 CC BY-SA / GNU FDL 或 GPLv2，排除 “all rights reserved” 页面 |
| DuckDB | 官方 `duckdb-web` 固定提交的 current 手册和发布文章 | 当前手册记 1.5 系列；发布文章提取精确版本 | 官方项目/发布文档；仓库使用 MIT License |

这里有个容易踩坑的地方：Oracle 的 MySQL 在线手册虽然权威，但它不是 GPL，条款对修改和独立再分发有限制。所以可发布语料默认只用官方 Community 源码树里自带的 HELP 和错误资料。它们仍然带有对应手册的规范 URL，做 SQL 语法、函数、运算符和错误检索够扎实，也不会把授权风险藏起来。

所有远程大包都固定到了发布版本或提交，并在清单中保存官方 SHA-256、官方 SHA3-256，或第一次验证后的归档 SHA-256。采集器不会绕过登录、付费墙、robots、限速或其他访问控制。SQLite 直接用官网提供的文档 ZIP，不爬它禁止抓取的 Fossil `/src`、`/docsrc` 路径。

## 一次跑完

需要 Python 3.10 以上。推荐先建虚拟环境：

```powershell
cd construction
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m sqlmend_pipeline.cli build
```

Linux/macOS 只要把激活命令换成：

```bash
source .venv/bin/activate
```

如果已经从项目根目录用 `python -m pip install -e ".\construction[test]"` 安装过，也可以直接在根目录运行 CLI；它会自动识别 `construction/`。想完全显式控制路径，则使用 `python -m sqlmend_pipeline.cli --root construction build`。

`build` 会依次采集、解析、清洗、补元数据、文档去重、分块、生成统计并验证。任何硬验收项失败，最后都会返回非零退出码。重跑是安全的：URL 对应文件和内容哈希一致时直接复用，不会往旧文件后面追加，也不会把半截下载当成成功文件。

只想复用已经下载的原文，可以运行：

```powershell
python -m sqlmend_pipeline.cli build --skip-collect
```

## 分阶段跑和排错

```powershell
python -m sqlmend_pipeline.cli collect
python -m sqlmend_pipeline.cli parse
python -m sqlmend_pipeline.cli clean
python -m sqlmend_pipeline.cli enrich
python -m sqlmend_pipeline.cli deduplicate
python -m sqlmend_pipeline.cli chunk
python -m sqlmend_pipeline.cli statistics
python -m sqlmend_pipeline.cli validate
```

也可以只补采某个来源：

```powershell
python -m sqlmend_pipeline.cli collect --source sqlite_3_53_4_docs
```

下载失败看 `reports/collection_report.json` 和 `reports/download_failures.jsonl`；解析失败则看 `reports/parse_report.json` 和 `reports/parse_failures.jsonl`。目前固定快照的完整采集没有失败或不可访问来源，未来网络或上游状态变化时，报告会如实变化。

## 清洗到底做了什么

解析器分别处理 HTML、Markdown、XML/SGML 和纯文本，还单独认识 MySQL/MariaDB 的 `fill_help_tables.sql` 与错误消息目录。后两类文件看起来是一两个大文件，实际上会还原成 HELP topic 或错误记录，并保留数值错误号、符号名、SQLSTATE、`%s`/`%d` 等占位符、解释、示例和规范 URL。

清洗会去掉导航、页脚、cookie 提示、重复目录、PostgreSQL 的 Prev/Up/Home/Next 页表、GitBook/Marketo 控件、Liquid 展示标签和许可页脚，但许可信息已经留在元数据里。SQLite 自动生成、只用于站内构建的 crossreference 页面也不进生产候选。SQL 代码、命令行示例、配置片段、错误字符串、函数签名、运算符和大小写不会被“美化”掉。表格会变成这种可检索形式：

```text
Table:
- SQLSTATE: 23505 | Condition: unique_violation
```

也就是说，列名和每个值的关系还在，不会只剩一串脱离表头的单元格。

## 分块策略

生产语料使用结构感知分块：文档 → 章节 → 子章节 → 解释/语法/示例。默认目标 150 个词，正文最少 35、普通最大 260、重叠 20，参数都在 [config/chunking.yaml](config/chunking.yaml)。最小长度按正文算，不会靠很长的合成标题；短签名、参数、返回值、错误记录和代码/表格仍作为有意义的原子块保留。相邻的小节会配对，避免把函数签名和参数拆开。重叠内容会明确标成 `Context carried from the preceding passage`，也不会单独产生只有半句的尾块。代码块会和紧邻解释绑在一起；错误消息不会和错误号拆开；表格只会在行之间切，并在每个子块里保留列名。超长但必须原样保留的单个代码块允许超过普通最大值，这会在统计里如实体现。

为了以后做消融实验，流水线还会生成 180 词、30 词重叠的固定尺寸基线 `data/processed/corpus_fixed.jsonl`。生产索引应该使用 `corpus.jsonl`，不要把两套混在一起。

`chunk_id` 由文档 ID、策略、章节位置和文本哈希确定；相同输入与配置会得到相同 ID。每块开头会补 `Title` 和 `Section`，离开整篇原文也能看懂上下文。

## 版本和元数据怎么记

只记录来源明确支持的信息：

- 精确发布包用 `exact`；
- 明确覆盖一个版本族用 `range`；
- 官方当前文档但没有可靠补丁号用 `current`；
- 明确旧版用 `legacy`；
- 实在判断不了才用 `unknown`。

SQLite、MariaDB、DuckDB 的发布说明会从版本标题进一步细化到 chunk；跨版本相同或相似的文字只有在版本范围相同且没有独立语义时才参与合并。

生产 JSONL 至少包含下面这些字段：

```json
{
  "chunk_id": "smr_postgresql_...",
  "document_id": "doc_postgresql_...",
  "dialect": "postgresql",
  "vendor_or_project": "PostgreSQL Global Development Group",
  "version": "18.6",
  "version_min": "18.6",
  "version_max": "18.6",
  "version_status": "exact",
  "source_type": "official_docs",
  "source_name": "PostgreSQL 18.6 Reference Manual",
  "source_url": "https://...",
  "title": "SELECT",
  "section": "SELECT > Examples",
  "text": "...",
  "contains_sql": true,
  "contains_error_code": false,
  "retrieved_at": "2026-08-27T...Z",
  "content_hash": "..."
}
```

另外保留了 `source_id`、`authority_class`、`topic`、`chunking_strategy` 和版本/兼容性提示标志，方便以后做过滤和实验。

## 去重和平衡

精确去重使用规范化空白后的 SHA-256。近似去重先用确定性的 64 位 SimHash 找候选，再算规范化 5 词 shingles 的 Jaccard，相似阈值默认 0.94。规则有意保守：只在同方言、同版本范围里比较；错误符号集合不同就保留；跨方言或有意义的跨版本内容不合并。删掉了什么、相似度多少，都写进 `document_duplicate_report.json` 和 `chunk_duplicate_report.json`。

候选文档大小差别很大，所以最终不是简单“全收”。每个方言目标 2,400 块，先在语法、函数、错误、迁移、类型、事务、配置、发布说明等主题之间轮询，再在来源、来源类型和版本之间轮询。这样既不会让某个超大的官方站点占满语料，也不会让拥有几百个版本标题的 release notes 挤掉语法和错误资料。硬规则仍是每方至少 1,000 块、任一方不超过 35%、总量至少 10,000。

## 报告、验证和测试

统计命令会自动写出：

- `reports/corpus_statistics.json`
- `reports/corpus_statistics.md`
- `reports/source_coverage.csv`
- `reports/version_coverage.csv`
- `reports/inspection_sample.jsonl`（固定随机种子、五方分层抽 100 块）
- `reports/manual_inspection.json`（人工逐条结果、样本/语料 SHA-256 和边界观察）

验证命令会把每项的 PASS/FAIL、观测值、要求、失败原因和修复建议写入 `reports/validation_report.json`。它会检查 JSONL、ID、必填字段、URL、五方词表、版本状态、编码、短垃圾、精确/近似重复、数量与词数、方言占比、版本覆盖、来源清单一致性、可检查样本，以及待跟踪文件中的疑似凭据。

运行测试：

```powershell
python -m pytest
```

测试覆盖四类解析器、元数据提取、方言规范化、版本解析、两类去重、跨版本保护、结构/固定分块、代码块保护、表格转换、导航清理、稳定 ID、统计、来源清单一致性，以及验证失败必须返回非零状态。

## 已知限制

- MySQL 默认没有收 Oracle 单独授权的整本在线手册和 release notes，因此发布说明覆盖比另外四方弱；这是明确的许可取舍，不是把缺失藏起来。
- DuckDB 没有 PostgreSQL 那种集中式数值错误码表，主要保留 Binder、Catalog、Parser、Conversion 等官方错误类别和原始消息。
- MariaDB 当前文档的版本提示分散，无法可靠判断的通用页只标 `current`，不会乱填精确版本；混合许可页面逐页筛选，排除了 145 份本次快照中不允许复用的页面。
- PostgreSQL 17/18 发布包提供结构化 SGML 而不是生成 HTML；这些 passage 的来源 URL 指向可校验的官方归档成员，14.24 则可直接指向版本化网页。
- 当前只保留 SQLite 的当前手册和完整历史 release log，没有整包复制多个高度相似的旧手册。
- 自动一致性检查不能代替法律意见，也不能代替最终论文里的人工误差分析。`inspection_sample.jsonl` 就是留给人工抽查的入口。
- 还没有做 BM25、向量索引、检索器或 RAG 生成器，这是刻意留给后续阶段的。

## 当前构建状态

下面是 2026-08-27 这次固定快照的摘要；以后重跑仍应以自动报告为准：

| 项目 | 当前结果 |
|---|---:|
| 原始 / 清洗文档 | 8,284 / 8,189 |
| 生产 chunks / 总词数 | 12,000 / 1,663,145 |
| 五方分布 | 每方 2,400（各 20%） |
| 方言已知 / 版本已知 | 100% / 95.2083% |
| 最终精确重复 / 估计残余近重复 | 0 / 0% |
| 下载失败 / 不可访问来源 | 0 / 0 |
| 人工样本 | 连贯可检索 100/100；SQL/错误适用项保留 38/38 |
| 自动验证 / 测试 | 24/24 PASS；90 tests PASS |

详细数字在 [corpus_statistics.json](reports/corpus_statistics.json)，逐项门槛在 [validation_report.json](reports/validation_report.json)，人工记录在 [manual_inspection.json](reports/manual_inspection.json)。
