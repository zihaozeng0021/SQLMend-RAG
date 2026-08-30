# SQLMend-RAG 正式基线检索模块

本目录实现三套独立、可审计基线：BM25 稀疏检索、零样本 E5 稠密检索，以及只融合前两者排名的 RRF hybrid。流水线自行控制语料加载、schema 验证、查询序列化、索引、精确检索、融合、TREC 导出、pool 审计、评估、性能记录和验证；第三方库只提供算法组件，不会把语料或查询发送给托管检索/RAG 服务。

版本身份说明：本目录及其 release 仅称为 **retrieval baseline**，不是 retrieval v1。之后加入方言与版本感知的正式检索系统才命名为 **retrieval v1**。现有 `*_formal_v1` run tag 是已被 annotation provenance 按字节哈希绑定的旧兼容标识，其中的 `v1` 不代表当前 retrieval release；不得据此把 baseline 称为 v1，也不得为改名原地重写这些已冻结 run。

这不是 AI6127 整体作业的完成声明。本阶段没有实现方言/版本显式加权、元数据过滤、reranker、query rewriting、HyDE、生成、SQL 修复或 UI。PDF 最终要求的简单 UI、五条界面演示查询、grounded generator、答案级 RAG 指标，以及至少 1,000 条人工标注且标注者一致性不低于 80% 的 held-out 数据，仍须在后续阶段完成。

## 数据身份与不可变性

正式语料固定为 `construction/data/processed/corpus.jsonl`：

- SHA-256：`279c2cffcbf74dad6b65867afacb92cbd52bc04c0e1ac2e49b8f3d95adb25db3`
- 12,000 chunks，五种方言各 2,400 条
- 处理顺序：按 `chunk_id` 升序

`construction/` 和 `annotation/codex/` 都是受保护目录。审计命令递归记录每个文件的二进制 SHA-256；工作前后任一文件新增、删除或字节变化都会失败。不能只用 Git status 代替该检查。

当前 250 条查询及 13,449 条 qrels 是 **machine-proposed development data**。基于它们的任何可发布结果必须写作 **machine-proposed development evaluation**，不能称为 gold、人工标注或 held-out test，也不能抵扣最终 1,000+ 人工标注要求。

## 严格查询白名单

实际 `dev_250.jsonl` schema 被保守映射为：

| 用户可提供语义 | 实际字段 |
|---|---|
| 数据库方言 | `dialect` |
| 数据库版本 | `version` |
| 自然语言问题 | `user_problem` |
| 原始 SQL | `sql` |
| 已观察错误 | `error_message`, `error_code`, `sqlstate`, `error_symbol` |

`expected_behavior`、schema/setup/seed、error category、root cause、reference fix、evidence、source link、case flags、verification、qrels 和其他标注字段一律不会进入正式查询。缺失字段会省略整个 section，不插入 `Unknown`、`N/A` 或 `None`。BM25 与 dense 共用 `sqlmend-query-v1` 序列化结果；SQL 只规范 CRLF/CR 换行，不做改写。

## 三套固定基线

### BM25

- `rank_bm25.BM25Okapi==0.2.2`
- `k1=1.5`, `b=0.75`, top 30
- lowercase；无 stemming、stopword removal 或 SQL 专用加权
- `sqlmend-lexical-v1` 保留 SQL 标识符、函数、SQLSTATE、错误码、版本号、限定名称和 `->>`、`->`、`::`、`<=`、`>=`、`<>`、`!=` 等运算符
- 排序：score 降序，再按 `chunk_id` 升序

### Zero-shot dense

- 模型：`intfloat/e5-base-v2`
- 固定 revision：`f52bf8ec8c7124536f0efb74aca902b2995e5bcd`
- `query: ` / `passage: ` 前缀；mean pooling
- CPU、14 线程、batch 64、最大输入 256 tokens
- 为可接受的 CPU 构建时间使用固定 dynamic-int8 模型推理；输出 embedding 明确转换并保存为 L2-normalized float32
- cosine 通过精确 inner product / matrix multiplication 实现，不使用 ANN
- 排序：similarity 降序，再按 `chunk_id` 升序

该模型与参数在查看开发集正式指标之前冻结；不会在同一 250 条数据上试多个模型后挑选最好者。模型下载/加载时间与语料编码、索引写入时间分开记录。

### Hybrid RRF

只读取正式 BM25 top 30 和正式 dense top 30：

```text
RRF(d) = 1 / (60 + rank_bm25(d)) + 1 / (60 + rank_dense(d))
```

缺失通道不贡献分数，component rank 保存为 `null`。输出排序依次为 RRF score 降序、最佳组件 rank 升序、`chunk_id` 升序。qrels、relevance、source links 或手工文档没有进入融合 API 的入口。

## TREC、qrels 与 pool 语义

三套 run 均采用：

```text
query_id Q0 chunk_id rank score run_tag
```

分数固定为 12 位小数；每条查询恰好 30 条、rank 连续、chunk 唯一且必须属于冻结语料。qrels 转换保留 0/1/2 全部标签，包括 relevance 0。

缺少 `(query_id, chunk_id)` qrel 表示“未判定”，绝不等同于 relevance 0。三个系统都必须达到 `Judged@30 = 1.000` 才能发布指标；否则生成 `pool_expansion_required.jsonl` 和 summary，将 `evaluation_integrity_status` 设为 `BLOCKED`，并等待外部人工或独立机器判断。扩池文件只提出 judgment 请求，不自动生成标签。

即使 pool 完整，Recall 也只能称为 **pooled Recall**：分母来自有限 judgment pool，不是 corpus-exhaustive recall。现有 pool 由历史 BM25、BGE dense 和 source-linked evidence 构造，因此存在 pooling bias；正式 E5/BM25 找到 pool 外文档是预期现象，不应被惩罚为 0。

### 怎样补充 pool judgments

`check-pool` 生成的 `retrieval/baseline/pool_expansion/pool_expansion_required.jsonl` 是只读请求清单，其中带有 passage 快照、正式系统排名和 component ranks。人工或独立标注流程完成后，把新增判断另存为 `retrieval/baseline/qrels/pool_expansion_judgments.jsonl`，每行只需：

```json
{"query_id":"DEV0001","chunk_id":"smr_example","relevance":1}
```

`relevance` 仍使用 0/1/2 语义。不要编辑 `annotation/codex/qrels_machine_proposed.jsonl`、已有 TREC qrels 或扩池请求文件，也不要让流水线自动猜标签。合并器只接受当前三套正式 top-30 union 内、且没有出现在冻结 base qrels 中的 query/chunk 对；冲突、重复、未知 chunk 或 pool 外记录都会失败。补判文件由外部标注过程拥有，流水线从不创建或覆盖它。

保存补判文件后，从 `check-pool` 开始重跑；命令会生成新的 `qrels_effective_dev250.trec` 和 merge metadata。只有三个系统的 `Judged@30` 都达到 1.000，`evaluate` 才会生成 overall、slice、confidence intervals、pairwise comparisons 与 complementarity。否则这些发布物必须继续缺席。

## 安装与完整重建

从仓库根目录执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pip install -e retrieval/baseline

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

正式测试证据必须由 `test` 子命令生成。它内部执行 `python -m pytest retrieval/baseline/tests -q -p no:cacheprovider`，记录 stdout/stderr、return code、Python 信息与测试前后 source-tree hash；直接运行 pytest 可用于开发诊断，但不能替代 `retrieval/baseline/reports/test_results.json`。

`finalize` 重新生成 failure analysis、manifest、baseline/completion reports，并对最终重写后的产物再次 validation。当前 pool 尚未补齐时，`evaluate` 会正常写入 BLOCKED 哨兵，而 `finalize` 与 `validate` 会以非零状态明确拒绝发布；这是预期的完整性门禁，不应绕过。

`python -m sqlmend_retrieval.cli all` 按同一依赖顺序执行完整流水线，在输入、索引、run、确定性或受保护目录硬失败时停止。Dense 模型首次下载保存在 `retrieval/baseline/indices/dense/model_cache/`；以后可离线重建 embedding。要重建索引，无需删除目录或执行未记录步骤，直接依次重跑 `build-bm25`、`build-dense`、三个 `run-*`、pool/evaluation/benchmark/test/after-audit/finalize 即可；每个项目自有产物都会按固定配置重写并重新绑定 hash。

关键正式产物包括：`serialized_queries/dev_250_queries.jsonl`、两个 index 目录、三套 TREC run、hybrid provenance、base/effective TREC qrels、pool-expansion 请求与 summary、evaluation 目录、四份人工可读报告、validation report 和根部 `manifest.json`。模型缓存内部文件由上游 snapshot 管理，manifest 用目录 tree hash 整体绑定。

## 性能测量

`benchmark` 先做 3 条 warm-up，再对全部 250 条查询运行一次，使用 `time.perf_counter`。它分别报告：

- 冷启动的索引/模型加载；
- BM25 warm latency；
- dense query encoding、exact vector search 和 total；
- hybrid BM25 component、dense component、RRF fusion 和 total；
- mean、median/P50、P95、max、QPS；
- build/encoding time、递归索引大小和硬件/软件环境。

当前 cold-start 计时明确包含索引/模型加载以及冻结语料与配置 binding 校验，不包含进程启动；warm-query 统计则排除这些一次性工作。

不同硬件的数字不能在不声明环境的情况下直接比较。

## 三个状态怎样解释

- `engineering_status=PASS`：冻结输入、隔离、250×30 runs、chunk/rank/score 合法性、两次 run 字节一致、测试、报告和受保护目录都通过。
- `evaluation_integrity_status=PASS|BLOCKED`：只有三个系统 Judged@30 均为 1、评估产物完整且 qrels 未变时才 PASS；存在未判定 top-30 时必须 BLOCKED。
- `retrieval_quality_status=PASS|FAIL|NOT_EVALUATED`：在完整 pool 上测量 hybrid nDCG/pooled Recall/HitRate 目标；pool 被阻塞时为 NOT_EVALUATED。质量 FAIL 不等于工程实现错误，不能通过改 qrels、查询、切片、模型或 RRF 参数来隐藏。

只有前两个状态都 PASS，阶段 5-6 才能称为完整 baseline release；之后才建议进入 Stage 7 dialect-aware retrieval。即使如此，PDF 所要求的 UI、生成和人工测试集仍属于课程项目的未完成工作。
