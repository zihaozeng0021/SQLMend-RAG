# SQLMend-RAG 开发上下文

> 内部开发交接文档，不面向最终用户。
>
> Last verified: 2026-08-29 (Singapore Time)
>
> 当前阶段：知识库已完成；Codex 机器开发集已完成；正式检索 v1 的上一候选快照工程为 `PASS`，但当前 checkout 的正式证据已 `STALE`；质量评估仍因 judgment pool 不完整而 `BLOCKED`。

## 1. 这份文件的用途

这份文件是后续开发者和自动化 Agent 的项目入口，用于回答以下问题：

- 课程究竟要求什么，哪些阶段仍未完成；
- 仓库中每个目录负责什么，哪些目录不能修改；
- 当前数据是什么性质，哪些结果可以或不可以声称；
- 已实现的检索系统如何工作，怎样重建和验证；
- 当前真正的 blocker 是什么，下一步应按什么顺序推进；
- 修改某类文件后，哪些证据必须重新生成。

它不替代以下文件：

- 根目录 `README.md`：最终面向用户的安装、运行与产品说明；当前版本状态滞后，计划在项目完成时重写。
- 自动生成的 manifest、validation report 和测试报告：它们才是可机器核验的事实证据。
- 最终课程报告：课程问题、实验分析、截图和提交材料应另行整理。

如果本文与当前文件字节或重新运行的 validator 冲突，以 validator 和当前字节为准，并更新本文；不要为了让报告匹配本文而手工修改生成物。

## 2. 优先级与事实来源

### 2.1 要求优先级

1. 课程 `Assignment.pdf` 是最高准则。当前机器上的来源路径为 `C:\Users\zihao\Desktop\Documents\AI6127\Assignment.pdf`，它不在仓库中；当前文件 SHA-256 为 `B61A21DC5ED61B94EB584B7A50694C9304152246192BF4C40A753E17C6A1C2BB`。协作者必须从受控共享位置取得同一 PDF 并校验该哈希，不能用名称相同但内容不同的文件替代。
2. 用户明确决定在不违反 PDF 时生效。
3. 当前阶段的专项规格或 prompt 只约束该阶段。
4. 仓库模块文档与工程惯例。
5. 本文和 README。

当前已确认的特殊决定：现有 250 条 Codex 生成数据可用于开发和调试，人工标注之后补充；这不改变 PDF 对最终人工评估集的要求。

阶段 5-6 规格中“不实现 UI、生成器或最终人工集”只是当时的实现边界，不代表这些课程要求被取消。

### 2.2 当前事实优先级

1. 当前文件字节，以及在这些字节上重新运行的 validator、manifest 和测试结果。
2. 冻结配置、schema、源数据和源码。
3. 自动生成的报告。
4. 本文中的验证快照。
5. README 或口头摘要。

易变化的数字在本文中只保存摘要和权威路径。每次重跑后，应先检查相应 manifest/report，再更新本文。

## 3. 课程硬要求摘要

课程作业占总成绩 35%，主要评分分为知识库构建 20 分、检索 40 分、下游任务与生成 40 分。小组为 5 或 6 人。系统不能只是现有服务的拼装或对 hosted RAG API 的单次调用；必须能解释并实现自己的离线知识库、检索和基于证据的生成阶段。

提交截止时间为 2026-11-07 23:59 SGT，经 Blackboard 提交。只计第一次提交；PDF 原文规定每个 rounded-off day 扣 `5% points`。与同年或往年项目重合超过 30% 会被取消资格。

知识库至少需要 10,000 个 documents/passages 和 100,000 words。小组仍需自行采集并标注 held-out test set；它不得有重复，并应尽量平衡。检索至少包含 sparse、dense 和 hybrid，不能用 SQL `LIKE` 代替文本检索。

课程问题与最低交付要求：

| 问题 | 必须覆盖的内容 | 当前状态 |
|---|---|---|
| Q1 | 语料来源、采集、清洗、分块、存储；应用与示例查询；文档/chunk/word/type 数量 | 知识库工程已完成，最终报告尚未写 |
| Q2 | 简单友好的 UI；5 条查询、结果与查询速度 | `PLANNED` |
| Q3 | sparse、dense、hybrid；检索创新；Precision@K、Recall@K、MRR、nDCG 等 rank-aware 指标与案例 | v1 已实现；上一候选 engineering `PASS`，当前证据 `STALE`；正式质量评估 `BLOCKED`，创新阶段未开始 |
| Q4 | 生成/分类方法选择与预处理；自行建立至少 1,000 条无重复、尽量平衡的人工 held-out records；IAA 至少 80%（推荐 3 名 annotators，2 名也可）；任务指标、RAG 指标和性能指标 | `PLANNED` |
| Q5 | 下游创新；若有多个创新必须做单项与组合 ablation；解释具体问题与案例 | `PLANNED` |

最终提交是一个以组号命名的 PDF，例如 `10.pdf`。第一页需列全部组员姓名和学号；正文回答 Q1-Q5；还需提供两个可访问的压缩包链接：

1. 数据包：知识库、查询与检索结果、评估集、生成答案/分类结果，以及 Q3/Q5 所需数据；
2. 源码包：全部源码和依赖，并包含说明如何编译和运行的最终用户 README。

课程还要求 Week 13 线下展示，最终报告应包含清晰图片。生成式任务必须让答案由用户可检查的检索证据支撑。答案级 RAG 评估至少覆盖 faithfulness、answer relevance 和 context relevance/precision；同时讨论 latency、吞吐量、成本和可扩展性。

## 4. 当前项目总览

| 模块或交付 | 状态 | 结论 |
|---|---|---|
| `construction/` | `VERIFIED_COMPLETE` | 12,000 chunks 的五方言知识库；24/24 验证和 90/90 测试通过 |
| `annotation/codex/` | `VERIFIED_COMPLETE_FOR_DEVELOPMENT_ONLY` | 250 条 Codex 机器建议开发数据；不是人工 gold 或最终测试集 |
| `retrieval/` 工程 | `LAST_VERIFIED_PASS / CURRENT_EVIDENCE_STALE` | BM25、零样本 E5、两路 RRF、审计、测试、性能与发布门禁均已实现；`retrieval/README.md` 后续变更使当前源码快照与旧证据绑定不一致 |
| 正式检索评估完整性 | `LAST_SNAPSHOT_BLOCKED` | 上一候选的三路正式 top-30 中仍有未判断文档；当前 checkout 尚未重新最终化 |
| 检索质量 | `NOT_EVALUATED` | 未发布正式 Precision/pooled Recall/MRR/nDCG 或比较结论 |
| 方言/版本感知检索创新 | `PLANNED` | 当前项目决定先完成有效 baseline 评估，再开始 v2 创新 |
| Grounded generator / SQL repair | `PLANNED` | 尚未实现 |
| 最终人工 held-out 数据 | `PLANNED` | 仍需至少 1,000 条人工记录和 IAA >= 80% |
| UI 与 5 条演示查询 | `PLANNED` | 尚未实现 |
| 最终课程报告与用户 README | `PLANNED` | 根 README 暂不作为当前开发状态来源 |

上一次已最终化候选的发布状态对象：

```text
release=retrieval-baseline-v1-candidate
engineering_status=PASS
evaluation_integrity_status=BLOCKED
retrieval_quality_status=NOT_EVALUATED
annotation_reproduction_status=PARTIAL
overall_success=false
```

当前 checkout 的证据状态：

```text
current_checkout_retrieval_evidence=STALE
stale_reason=retrieval/README.md changed after the last formal test/finalize/validate cycle
last_verified_source_tree_sha256=d804a637d0c64b2f79170f929d1e9520f37060b13e0edefc797728f438572562
current_source_tree_sha256=afecdc290e80248a8f9826ceab80930269b463567cfbee17fdd6c9f1ab7c4f31
required_refresh=test -> finalize -> validate
```

[retrieval manifest](retrieval/manifest.json) 和 [retrieval validation](retrieval/reports/validation_report.json) 是上一候选快照的权威来源：当时 validation 共 31 项，29 `PASS`、2 `BLOCKED`、0 `FAIL`。它们不能作为当前 checkout 已验证的证明；刷新后应以新报告为准。

## 5. Git 时点说明

本文创建前观察到：

- branch：`baseline-retrieval`
- HEAD：`a192ed166549a9b46f706999729d4a219e4e2d59`
- retrieval 首次提交：`0cca1db2c667e56bfa2693cf642efac287ada4b3`
- 机器开发标注提交：`f6afc4023e44218e89d4ad9ce6ad37b4350d391e`

`retrieval/manifest.json` 是 retrieval 被提交前生成的候选工件，所以其中仍记录：

```text
git_commit=f6afc4023e44218e89d4ad9ce6ad37b4350d391e
retrieval_status_porcelain=["?? retrieval/"]
```

这是生成时 provenance，不是当前 Git 状态。不要手工把它改成当前 HEAD；只能通过正式流程重新绑定。

提交 `a192ed1` 在上一轮正式证据生成后修改了 `retrieval/README.md`，使当前 source tree SHA 从 `d804a6...` 变为 `afecdc...`。因此现有 test/manifest/validation 只描述上一候选；若现在直接运行 validator，它应拒绝旧的 source binding。下一次正式 retrieval 维护必须先运行 `test -> finalize -> validate`，并按 pool 不完整时的预期非零退出语义解释结果。

`DEVELOPMENT_CONTEXT.md` 位于 retrieval source snapshot 之外，单独更新本文不会再次改变该 snapshot。

## 6. 仓库目录与所有权

```text
SQLMend-RAG/
├─ construction/           # 知识库采集、清洗、去重、分块、统计、验证；冻结输入
├─ annotation/codex/       # Codex 机器开发集与其 provenance；冻结输入
├─ retrieval/              # 当前正式检索基线、runs、评估门禁和报告
├─ tmp/                    # 本地临时目录；不是事实来源或交付契约
├─ DEVELOPMENT_CONTEXT.md  # 本文件：内部开发交接入口
└─ README.md               # 最终用户入口；计划在项目后期重写
```

### 6.1 受保护目录

`construction/` 和 `annotation/codex/` 在 retrieval v1 中是只读、字节级保护输入。除非用户明确启动一个独立的数据维护版本，否则不得：

- 新增、删除、重命名或修改任何文件；
- 运行可能生成 `__pycache__` 的 Python 命令；
- 执行跨仓库 cache 清理或 formatter；
- 覆盖原始机器 qrels、candidate pool 或 provenance。

正式 before/after 审计均为：

```text
protected_file_count=8481
protected_tree_sha256=0d53bc19626850bd469eda2350d117a9fcfb2e5758dd84b9c92a7a60fa15bd26
protected_paths_unchanged=true
```

所有 Python 命令建议先设置：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
```

### 6.2 可重建但不跟踪的本地工件

以下内容由 `.gitignore` 排除，fresh clone 不会自带：

- `retrieval/indices/bm25/index.pkl` 和 metadata；
- `retrieval/indices/dense/embeddings.npy`、chunk mapping、metadata 与 E5 model cache；
- `retrieval/reproduction/model_cache/` 中的历史 BGE snapshot；
- Python bytecode 和 pytest cache。

不要把这些目录的“本机存在”当作仓库可复现性的证明；重建命令和 manifest binding 才是证明。

## 7. 知识库构建：已完成事实

模块入口与详细设计见 [construction README](construction/README.md)，权威验收见 [construction validation](construction/reports/validation_report.json) 和 [construction completion report](construction/reports/completion_report.md)。

生产语料只能使用：

```text
path=construction/data/processed/corpus.jsonl
sha256=279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3
```

不要把被忽略、可重建的 `corpus_fixed.jsonl` 当作生产语料。

关键统计：

| 项目 | 数值 |
|---|---:|
| raw documents | 8,284 |
| cleaned documents | 8,189 |
| final chunks | 12,000 |
| total words | 1,663,145 |
| approximate unique word types | 35,646 |
| average chunk words | 138.5954 |
| median chunk words | 131 |
| PostgreSQL / MySQL / SQLite / MariaDB / DuckDB | 各 2,400 chunks |
| known version/range coverage | 95.2083% |
| exact duplicates / estimated residual near duplicates | 0 / 0% |
| manual inspection | 连贯可检索 100/100；SQL/错误适用项保留 38/38 |
| automated validation / tests | 24/24 PASS；90/90 PASS |

流水线支持 HTML、Markdown、XML/SGML、纯文本，以及 MySQL/MariaDB HELP 和错误目录。生产分块是结构感知策略，目标 150 词、普通上限 260、重叠 20；详细参数在 `construction/config/chunking.yaml`。

历史重建参考（**不要在当前受保护 checkout 中执行**）：

以下命令会改写 `construction/` 的生成物，并可能产生缓存。它们只适用于一次性干净副本，或用户明确启动的、独立版本化的知识库维护工作；`PYTHONDONTWRITEBYTECODE` 只能阻止 `.pyc`，不能阻止应用写文件。

```powershell
python -m pip install -e ".\construction[test]"
python -m sqlmend_pipeline.cli build
python -m sqlmend_pipeline.cli validate
python -m pytest construction/tests -q
```

知识库阶段可以被称为完成，但这不表示 RAG 系统或课程作业完成。

## 8. Codex 机器开发集：严格边界

模块说明见 [annotation README](annotation/codex/README.md)，身份见 [annotation manifest](annotation/codex/manifest.json)，验收与分布见 [annotation validation](annotation/codex/validation_report.json) 和 [annotation statistics](annotation/codex/statistics.json)。

Manifest 明确记录：

```text
dataset_id=sqlmendrag-codex-dev-250
purpose=development_only
split=dev
annotation_origin=codex_machine_proposed
human_verified=false
eligible_for_assignment_final_eval=false
validation_status=PASS
```

必须使用以下称呼：

> machine-proposed development data
>
> machine-proposed development evaluation

严禁称为 `gold`、人工标注、adjudicated、held-out test 或最终评估集。固定 50 条 Codex 独立质量审计也不是人工验证，不能计入 PDF 的人工标注要求。

### 8.1 数据身份与统计

| 项目 | 数值或 SHA-256 |
|---|---|
| queries | 250；五方言各 50 |
| query SHA | `2ce81dd27690795266fc5cc813dc1999f8c55d86ed1605fd6e1013213a416fae` |
| candidate pool SHA | `0d8a89ad0eb39b3e481e58668c15df9416da69bf750100ec695f5d150f3f8d85` |
| qrels source SHA | `bcc0ef136a7ef06409ddf9a8e9d811ebe39671e4c4ad24e5c67e15b4463a47c6` |
| qrels | 13,449 |
| relevance 0 / 1 / 2 | 9,216 / 3,931 / 302 |
| dialect-sensitive | 174 |
| version-sensitive | 53 |
| documented-error cases | 69 |
| plausible-but-wrong cases | 214 |
| execution verified / documentation only | 78 / 172 |
| independent Codex audit | 50/50 PASS；仍非人工 |

十个 error categories 各有 25 条。所有 250 条在所声明的验证方法下为 passed，但 documentation-only 不等同于实际运行验证。

### 8.2 Relevance 语义

- `0`：已判断为不能支持该问题；
- `1`：部分有用或提供背景；
- `2`：直接支持诊断、修复或兼容性结论；
- qrels 中不存在的 pair：`unjudged`，绝不能静默转换为 relevance 0。

现有 qrels 只完整覆盖历史 candidate pool。正式 E5/BM25 可以返回这个 pool 之外的文档，因此“每个历史 candidate 都有标签”不代表“正式 top-30 已完整判断”。

历史验证参考（**不要在当前受保护 checkout 中执行**）：

该 validator 不是只读检查；它会重写 execution evidence、quality audit、statistics、validation report 和 manifest。只有在一次性干净副本或用户明确授权的数据维护版本中才运行。`PYTHONDONTWRITEBYTECODE` 不能阻止这些应用级写入。

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python annotation/codex/validate_annotations.py --root .
```

通常不要重建这个受保护开发集；需要新人工数据时，应新建独立目录、schema 和 manifest。

## 9. 正式检索基线设计

技术入口见 [retrieval README](retrieval/README.md)。`retrieval/` 是独立模块，当前只负责 fixed v1 baseline，不负责 UI、生成、SQL 修复、reranker、query rewriting、HyDE 或显式方言/版本调权。

### 9.1 数据流与隔离

```text
[retrieval path]
frozen corpus + frozen dev queries
             |
             +-> strict user-field serializer -> BM25 -> BM25 top-30 ---+
             |                                                           +-> RRF -> hybrid top-30
             +-> passage rendering -------------> E5  -> dense top-30 --+

[offline evaluation path]
machine-proposed base qrels + external supplemental judgments
                         |
                         +-> effective qrels ----------------------------+
                                                                         |
BM25 top-30 --------------------------------------------------------------+
dense top-30 -------------------------------------------------------------+-> pool audit
hybrid top-30 ------------------------------------------------------------+
                                                                         |
                              +------------------------------------------+----------------------------------+
                              |                                                                             |
                     any top-30 unjudged                                                        all Judged@30 = 1
                              |                                                                             |
                  BLOCKED sentinel only                                                       atomic metric bundle
                              +------------------------------------------+----------------------------------+
                                                                         |
                                                  reports -> manifest -> validation fixed point
```

Qrels 只进入离线评估，绝不进入 BM25、E5、RRF 或线上回答路径；RRF 只读取 BM25 与 dense 的正式排名。

历史 annotation retriever reproduction 是旁路 provenance audit。它可以为复现历史流程而读取 annotation-only query 字段，但它的 run 绝不进入正式 BM25、E5 或 RRF。

### 9.2 严格 query serializer

`sqlmend-query-v1` 只允许：

- `dialect`
- `version`
- `user_problem`
- `sql`
- 实际观察到的 `error_message`、`error_code`、`sqlstate`、`error_symbol`

以下字段绝不能进入正式搜索：`expected_behavior`、setup/schema/seed、error category、root cause、reference fix、evidence、source link、case flags、verification、qrels 或 candidate ranks。

序列化输出：

```text
retrieval/serialized_queries/dev_250_queries.jsonl
sha256=e9cc591b815e9afb584381ad60c6872b7c36d82e65e255e6dc7045e21ecbdb3c
```

BM25 和 dense 必须共用同一序列化文本。

### 9.3 三套 frozen v1 baseline

| 系统 | 固定设计 |
|---|---|
| BM25 | `rank_bm25.BM25Okapi==0.2.2`；`k1=1.5`、`b=0.75`、top 30；lowercase；无 stemming/stopword removal；SQL-aware tokenizer |
| Dense | `intfloat/e5-base-v2`，revision `f52bf8ec8c7124536f0efb74aca902b2995e5bcd`；768 维；精确前缀 `"query: "` / `"passage: "`（含末尾空格）；CPU 14 threads；dynamic-int8；max 256 tokens；L2-normalized float32；exact inner product |
| Hybrid | 只融合正式 BM25/dense top 30；RRF `k=60`；输出 30；tie-break 为 RRF score、最佳 component rank、`chunk_id` |

每个正式 run 必须恰好覆盖 250 个 query，每个 query 恰好 30 条结果；rank 连续、chunk 唯一、score 有限、chunk 属于冻结 corpus。

上一候选的正式 run hashes（当前文件字节仍在，但正式证据待刷新）：

```text
BM25   e72361668fc3338abac657a04c598eb36983e8a8201e506e34084d474e268f98
Dense  eeada87a6e1457f91a577e8c6d7a3d60cb59854523a4e31a4fff81b023513cdd
Hybrid 05a907f5ab05c3e09aad872d8523db74fd61c77bf34a4108e55c7c9fc667a468
```

上一候选的三路重复正式运行均字节一致。不要原地修改 v1 YAML 进行调参；需要创新实验时创建明确的 v2 配置、system ID、artifact 名称和验证契约。

### 9.4 源码职责

| 模块 | 主要职责 |
|---|---|
| `paths.py` | 发现仓库根并集中定义全部输入/输出路径 |
| `hashing.py` | 文件、canonical JSON、目录树、protected paths 和 retrieval source snapshot 哈希 |
| `corpus.py` | 冻结 corpus 校验、排序和 passage rendering |
| `queries.py` | 查询白名单、序列化和泄漏隔离 |
| `tokenization.py` | SQL-aware lexical tokenizer |
| `bm25.py` | BM25 索引、binding 和确定性搜索 |
| `dense.py` | pinned E5、dynamic-int8 encoding、embedding binding 和 exact search |
| `rrf.py` | 两通道固定 RRF 与 component ranks |
| `trec.py` | canonical 六列 TREC run 读写和验证 |
| `qrels.py` | JSONL/TREC qrels 和 supplemental merge |
| `pool_audit.py` | Judged@K、unjudged 语义和扩池请求 |
| `metrics.py` | nDCG、MRR、pooled Recall、Precision、HitRate、Judged@K |
| `slices.py` | 只按显式字段构造 dialect/error/flag slices |
| `bootstrap.py` | query-level bootstrap、paired comparison、CI |
| `latency.py` | latency/QPS/index size/runtime environment |
| `reproduction.py` | 历史 annotation BM25/BGE/RRF 独立复现 |
| `reporting.py` | failure analysis、provenance、manifest 和人读报告 |
| `validation.py` | 对现存 bytes 和契约做独立 release validation；不执行模型 |
| `cli.py` | 命令编排、退出码和 finalize 固定点收敛 |

增加第四个正式 retriever 不是注册一个插件即可完成；至少需要同步修改 `paths.py`、`cli.py`、`pool_audit.py`、`validation.py`、`reporting.py` 和测试中的三系统显式契约。

## 10. 当前 blocker：正式 top-30 未完整判断

本节数字来自上一候选工件。run 与 qrels 输入未因 `a192ed1` 的 README 变更而改变，因此它们仍指出同一个补判缺口；但当前 checkout 的发布证据仍必须刷新。

权威文件：

- [judged coverage](retrieval/evaluation/judged_coverage.json)
- [pool summary](retrieval/pool_expansion/pool_expansion_summary.json)
- [pool expansion requests](retrieval/pool_expansion/pool_expansion_required.jsonl)
- [BLOCKED sentinel](retrieval/evaluation/overall_metrics.json)

当前覆盖：

| 系统 | Judged@5 | Judged@10 | Judged@20 | Judged@30 | top-30 未判断出现次数 |
|---|---:|---:|---:|---:|---:|
| BM25 | 0.7152 | 0.6024 | 0.4700 | 0.3841333333 | 4,619 |
| Dense | 0.4608 | 0.3752 | 0.3054 | 0.2592 | 5,556 |
| Hybrid | 0.6608 | 0.5764 | 0.4698 | 0.4009333333 | 4,493 |

合计：

```text
unjudged_top30_occurrences=14668
unique_pool_expansion_requests=10003
required_Judged@30=1.0 for every system
```

因此当前必须：

- 只发布六字段 `BLOCKED` sentinel；
- 保持 `per_query_metrics.csv`、`slice_metrics.csv`、CI、pairwise 和 complementarity 不存在；
- 不发布或使用不完整 pool 上的正式质量结论；
- 不把缺失判断当 0；
- 不在这些不完整指标上选择模型、修改 RRF 或宣称 hybrid 更好。

任何 Recall 即使在 pool 完整后也只能写作 **pooled Recall**，因为分母来自有限 pool，而不是穷举 12,000 chunks。

### 10.1 开发 pool 补判入口

不要编辑冻结的 `annotation/codex/qrels_machine_proposed.jsonl`、已生成的 TREC qrels 或扩池请求。外部补判只能写入：

```text
retrieval/qrels/pool_expansion_judgments.jsonl
```

每行：

```json
{"query_id":"DEV0001","chunk_id":"smr_example","relevance":1}
```

合并器只接受当前三套正式 top-30 union 内尚未判断的 pair、已知 chunk 和 relevance 0/1/2；冲突、重复、未知 chunk 或 pool 外记录会失败。

即使这 10,003 个 pair 全部由人工补判，effective qrels 仍混合原有机器 base labels，不能称为全人工 held-out test。

## 11. 历史 annotation retriever provenance

历史标注阶段使用的系统与正式 v1 不同：历史 BM25 为 `k1=1.2`，历史 dense 为 `BAAI/bge-small-en-v1.5`，然后做历史 RRF。正式系统则为 `k1=1.5` BM25 + pinned E5 + 两路 RRF。

三套历史排名均独立实现 250/250 exact top-30 sequence match：

```text
historical BM25   9ff5b86bd011531c73cfa565a244913dab5f18bc012f54a3021b09485763d8ff
historical dense  766178797dcc3411a12772fdde585cce37717801483650d4dc83063f3f402164
historical hybrid ad11d4a3e59d32fc0299a5c97dcc63bd0778b0d575e85acdefc72115ac39d148
```

系统级 empirical reproduction 是 `PASS`，但总 provenance 保守为 `PARTIAL`，原因包括：

- 历史 binding 没有证明当时内存中 builder 的精确源码 bytes；
- 历史 ONNX/tokenizer/runtime 的全部传递依赖未完整锁定；
- 历史 neural tie behavior 没有显式 `chunk_id` tie-breaker。

这项 `PARTIAL` 不阻止正式 baseline，因为正式检索不读取历史 candidate ranks，也不在 search 中使用 qrels 或 annotation evidence。修改 `reproduction.py` 或相关输入可能使 cache 失效并触发数小时重算。

## 12. 上一候选的测试、性能和本机快照

### 12.1 Retrieval 测试证据（当前 checkout：`STALE`）

权威文件：[test results](retrieval/reports/test_results.json)。

```text
95 tests PASS in 76.97 s
Python 3.12.7
source_file_count=41
source_tree_sha256=d804a637d0c64b2f79170f929d1e9520f37060b13e0edefc797728f438572562
source_stable_during_tests=true
current_source_tree_sha256=afecdc290e80248a8f9826ceab80930269b463567cfbee17fdd6c9f1ab7c4f31
evidence_applies_to_current_checkout=false
```

95 项通过是上一候选的事实，不是当前 checkout 已通过正式测试的声明。正式测试证据必须通过 CLI `test` 生成；手工 pytest 只适合开发诊断，不能替代 `test_results.json`。

### 12.2 上一候选性能快照

权威文件：[latency report](retrieval/evaluation/latency.json)。当前环境为 Windows 11、CPU-only、20 logical CPUs、约 34 GB RAM。不同硬件不得直接比较。

| 系统 | Mean | P95 | QPS |
|---|---:|---:|---:|
| BM25 warm | 214.59 ms | 293.11 ms | 4.66 |
| Dense total warm | 45.57 ms | 55.04 ms | 21.95 |
| Hybrid total warm | 260.55 ms | 348.00 ms | 3.84 |
| RRF fusion only | 0.396 ms | 0.502 ms | 2,524.96 |

其他一次性成本：

```text
BM25 cold start=0.556 s
Dense cold start=33.627 s
BM25 index build=1.630 s
Dense corpus encoding=1141.272 s
Dense model load/download=9.585 s
```

`benchmark` 的 cold-start 范围包含 index/model load 和冻结 corpus/config binding 校验，不包含进程启动。性能测量时不要并行运行重 CPU 任务。

## 13. Retrieval 安装、重建与退出码

要求 Python 3.11+。`retrieval/pyproject.toml` 和 `retrieval/requirements.txt` 固定了直接 runtime/test 依赖，但仓库没有完整 lockfile；build system（如 `setuptools>=69`、`wheel`）及传递依赖并未全部精确锁定。

从仓库根目录执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pip install -e retrieval

python -m sqlmend_retrieval.cli audit-protected-paths --phase before
python -m sqlmend_retrieval.cli verify-inputs
python -m sqlmend_retrieval.cli serialize-queries
python -m sqlmend_retrieval.cli audit-annotation-retrievers

python -m sqlmend_retrieval.cli build-bm25
python -m sqlmend_retrieval.cli build-dense

python -m sqlmend_retrieval.cli run-bm25
python -m sqlmend_retrieval.cli run-dense
python -m sqlmend_retrieval.cli run-hybrid

python -m sqlmend_retrieval.cli check-pool
python -m sqlmend_retrieval.cli evaluate
python -m sqlmend_retrieval.cli benchmark

python -m sqlmend_retrieval.cli test
python -m sqlmend_retrieval.cli audit-protected-paths --phase after
python -m sqlmend_retrieval.cli finalize
python -m sqlmend_retrieval.cli validate
```

`python -m sqlmend_retrieval.cli all` 执行同一依赖链，但首次运行会下载模型、构建 E5 embeddings，并可能重做历史 BGE reproduction，耗时较长。

针对当前仅因 `retrieval/README.md` 变化而陈旧的证据，最小正式刷新链是 `test -> finalize -> validate`；不要手工修改旧报告中的 source hash。由于 judgment pool 仍不完整，`finalize`/`validate` 预期仍会以发布阻塞语义返回非零。

若从仓库外运行，`--root` 必须放在子命令前：

```powershell
python -m sqlmend_retrieval.cli --root C:\path\to\SQLMend-RAG verify-inputs
```

当前 pool 不完整时的退出码语义：

- `evaluate` 返回 0，表示正确写入了 `BLOCKED` 状态，不表示 evaluation `PASS`；
- `finalize`、`validate` 和 `all` 返回非零，表示发布门禁拒绝将候选称为完整 release；
- 工程 `FAIL` 与评估 `BLOCKED` 必须分开处理。

补判完成后，从 `check-pool` 开始重跑，然后依次运行 `evaluate`、`benchmark`、`test`、after audit、`finalize`、`validate`。

## 14. Pool 完整后的质量门禁

只有三路 `Judged@30=1.0` 后才会原子发布：

- `overall_metrics.json`
- `per_query_metrics.csv`
- `slice_metrics.csv`
- `confidence_intervals.json`
- `pairwise_differences.json`
- `complementarity_report.json`

固定 v1 的质量目标是：

- hybrid graded nDCG@10 至少高于最佳单系统 0.01；
- hybrid pooled Recall@10_rel2 不低于最佳单系统减 0.01；
- hybrid HitRate@5_rel2 不低于最佳单系统减 0.01；
- 不允许存在未解释的、超过 0.05 的 dialect slice regression。

质量 `FAIL` 是测量结论，不等于工程实现错误。不得通过改 qrels、查询、切片定义、模型或 RRF 参数来隐藏失败。

## 15. 后续开发路线

后续工作分为两个互不混淆的标注轨道：

1. **开发 pool 补判**：补齐当前 10,003 个正式 top-30 pair，使 v1 baseline 可以被完整测量；
2. **最终人工 held-out 数据**：由小组另行采集并标注至少 1,000 条无重复、尽量平衡的记录；推荐三名 annotators，两名也可，IAA >= 80%；保存原始标注、annotator、分歧、adjudication 与 manifest；不得在其上反复调参。

这些工作不是全部串行依赖。应立即并行启动三条线：

1. `BLOCKED` **baseline evaluation**：完成开发 pool 补判，冻结有效 v1 release；它是 retrieval v2 正式比较和阶段 7 发布的前置条件；
2. `PLANNED` **human evaluation protocol**：在任何相关调参前冻结 held-out split、schema、指南、抽样、平衡、annotator 和 adjudication 流程；
3. `PLANNED` **product scaffolding**：generator 和 UI 可以基于当前冻结 v1 接口并行开发，不必等待 10,003 个开发 pool labels 全部完成。

随后按各自依赖推进：

1. 新建 dialect/version-aware retrieval v2，并保留 v1 作为不可覆盖对照；
2. 评估 reranker、query rewriting、HyDE 或其他创新；多个创新必须提供独立与组合配置以支持 ablation；
3. 实现 grounded SQL diagnosis/repair generator，答案引用可检查证据，并加入 unsupported-claim/faithfulness 检查；
4. 实现简单 UI。服务启动时加载并保持热的 BM25、DenseIndex 和模型，不要逐请求执行 CLI；
5. 完成任务指标、faithfulness、answer relevance、context precision/relevance，以及 latency、吞吐量、成本、可扩展性评估；
6. 准备 5 条代表性查询、结果、来源和查询速度；
7. 重写最终用户 README，准备 Q1-Q5 报告、截图、两份压缩包链接和 Week 13 演示。

现有 250 条 Codex 机器数据可以作为开发数据用于 prompt 设计、回归检查和明确记录 provenance 的离线训练；它绝不能充当最终测试数据，也不得污染最终 held-out split。

生产推理和最终测试不得读取 qrels、reference answers、candidate-pool ranks、annotation evidence 或 held-out labels 来影响搜索或回答。离线训练、fine-tuning 或 instruction-tuning 可以使用明确划分的非测试训练数据，但必须保存 split 与 provenance，并与最终 held-out set 严格隔离。

## 16. 变更影响矩阵

| 修改内容 | 必须重跑或更新 |
|---|---|
| 仅本文 | 无需重跑 retrieval；提交本文即可 |
| 根 `README.md` | 不在 retrieval source snapshot 中；按最终用户体验验证 |
| `retrieval/README.md` | `test -> finalize -> validate` |
| retrieval 源码、测试、config、requirements、pyproject | 相关 build/run/eval；正式 `test`；after audit；`finalize -> validate` |
| query serializer 或允许字段 | serialized queries、两个 retriever runs、RRF、pool、evaluation、test、finalize、validate |
| corpus | 这是新数据版本；不能原地修改受保护文件。新建版本并重建全部索引、runs、qrels binding、评估和报告 |
| supplemental qrels | 从 `check-pool` 开始重跑完整评估与最终化链 |
| 新 retriever | 新 system ID/config/artifact；更新 pool/evaluation/reporting/validation/tests；保留 v1 对照 |
| RRF 常量或 tie-break | 新 hybrid 版本；重建 hybrid、pool、evaluation、tests、reports；不得覆盖 v1 |
| 历史 reproduction 实现或输入 | 重新审计；可能触发数小时 BGE 重算 |
| generator/UI | 新模块、独立依赖与测试；更新本文、最终 README 和课程报告 |

## 17. 禁止事项与常见陷阱

- 不修改 `construction/` 或 `annotation/codex/` 的任何字节，包括缓存。
- 不把 missing qrel 当 relevance 0。
- 不把 250 条开发数据、50 条 Codex audit 或混合 effective qrels 称为人工 gold/held-out test。
- 不在不完整 pool 上发布 Recall/nDCG/MRR，或用这些数字调模型。
- 不让正式 retriever 读取 reference fix、evidence、qrels、candidate ranks 或 case flags。
- 不把历史 annotation retriever 当作正式 baseline。
- 不手工编辑 run、TREC qrels、metric、report 或 manifest；通过对应 CLI 重建。
- 不从不可信来源加载 BM25 pickle；只加载本项目生成且通过 hash binding 的 index。
- 不把 exact dense search 静默替换为 ANN；ANN 应作为新系统并记录 recall/latency/index identity。
- 不把生成器退化为 hosted RAG API 的单次调用。
- 不在最终人工 held-out 数据上进行反复调参。
- 不宣称整个 AI6127 作业已经完成。

## 18. 权威证据索引

### Knowledge-base construction

- [Construction README](construction/README.md)
- [Construction validation](construction/reports/validation_report.json)
- [Construction statistics](construction/reports/corpus_statistics.json)
- [Construction completion report](construction/reports/completion_report.md)

### Machine development annotations

- [Annotation README](annotation/codex/README.md)
- [Annotation manifest](annotation/codex/manifest.json)
- [Annotation validation](annotation/codex/validation_report.json)
- [Annotation statistics](annotation/codex/statistics.json)
- [Annotation provenance](annotation/codex/provenance/)

### Formal retrieval baseline

- [Retrieval README](retrieval/README.md)
- [Retrieval manifest](retrieval/manifest.json)
- [Retrieval validation](retrieval/reports/validation_report.json)
- [Retrieval completion report](retrieval/reports/completion_report.md)
- [Baseline report](retrieval/reports/baseline_report.md)
- [Failure analysis](retrieval/reports/failure_analysis.md)
- [Provenance audit](retrieval/reports/provenance_audit.md)
- [Judged coverage](retrieval/evaluation/judged_coverage.json)
- [Pool summary](retrieval/pool_expansion/pool_expansion_summary.json)
- [Pool expansion requests](retrieval/pool_expansion/pool_expansion_required.jsonl)
- [Evaluation sentinel](retrieval/evaluation/overall_metrics.json)
- [Latency](retrieval/evaluation/latency.json)
- [Test evidence](retrieval/reports/test_results.json)

## 19. 本文件维护协议

每次阶段状态、冻结输入、接口契约、主要 blocker 或课程解释发生变化时更新本文。普通内部重构如果不改变开发者需要知道的事实，可以不更新。

更新步骤：

1. 记录 `Last verified`、当前 branch/HEAD 和相关工件生成时点；
2. 重跑受影响模块的测试和 validator；未经验证的事实标为 `UNVERIFIED` 或 `STALE`；
3. 更新“当前项目总览”、数据身份、状态对象、blocker、下一步和证据路径；
4. 动态数字只保留摘要，不复制大段自动报告；
5. 不静默改写重要决定，在下面追加 decision log；
6. 确认根 README 仍保持面向用户，而本文保持面向开发者。

建议的 future-work 状态词：`PLANNED`、`IN_PROGRESS`、`BLOCKED`、`VERIFIED_COMPLETE`。

## 20. Decision log

| 日期 | 决定 | 原因与影响 |
|---|---|---|
| 2026-08-29 | `Assignment.pdf` 高于阶段 prompt 和开发便利性 | 任何冲突以课程要求为准；阶段边界不取消最终 UI、生成与人工评估要求 |
| 2026-08-29 | 250 条 Codex 数据仅作 machine-proposed development data | 人工标注后补；当前数据不能抵扣 1,000+ 人工记录或称为 held-out test |
| 2026-08-29 | `construction/` 和 `annotation/codex/` 在 retrieval v1 中字节级冻结 | 新判断通过独立 supplemental/new dataset 输入，不覆盖既有来源 |
| 2026-08-29 | missing qrel 永远是 unjudged，不是 relevance 0 | pool 不完整时必须阻止正式指标发布 |
| 2026-08-29 | 正式 v1 固定为 BM25 + pinned E5 + two-channel RRF | 历史 BGE/candidate ranks 只用于 provenance；创新建立新版本而不覆盖 v1 |
| 2026-08-29 | 根 `README.md` 留作最终用户文档；根 `DEVELOPMENT_CONTEXT.md` 维护内部状态 | 避免把交接细节、临时 blocker 和用户安装文档混在一起 |
| 2026-08-29 | 当前 retrieval 正式证据标为 `STALE` | `a192ed1` 修改 `retrieval/README.md` 后 source tree SHA 与上一候选证据不再一致；下一次维护先执行 `test -> finalize -> validate` |
