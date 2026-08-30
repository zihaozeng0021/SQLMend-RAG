# Phase 10：Generation Baseline 与 Generation v1

Schema: `sqlmend-generation-report-v1`。本报告是 **machine-proposed development evaluation**；
当前 250 条记录及离线 reference/qrels 均为 machine-proposed development data，
不是人工 gold，也不是最终 held-out test 结果。失败 wrapper 始终保留在分母中。

## 实验与完整性边界

- 配对查询：250；正式结果 wrapper（含明确失败记录）：500。
- Baseline：`baseline`，不接收 retrieval evidence；Baseline 的 RAG 指标为 `N/A`。
- Generation v1：`generation_v1`，只使用冻结 Retrieval v1 本次 Top-5 evidence。
- 离线 judge：`qwen3.5:4b`，digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`，`think=false`（thinking disabled）；每 query 一次匿名 A/B 逻辑调用，奇偶反转，最多 3 次 attempt。
- Run seal：Baseline `70e4372ee2414be316ebd084b95503d01d87ecfa6ec49b916da13c64370f0927`；Generation v1 `0af9cc7c8dbbcecdeaa87cf73ff51c22cb13c0016e07614060787e3a1434c66c`。两个 run 在 reference/qrels 首次打开前封存。
- 在线 generation 输出没有被 reference、annotation evidence 或 qrels 回写；reference 字段只进入此离线评估。
- Generation `status=success` 只表示调用最终通过 JSON/schema/citation 合同，不表示 SQL 问题已修对；语义正确性只由下方离线指标衡量。

## Formal generation execution

| 系统 | 正式 wrapper | Generation Contract Success | 明确失败 | retries | 重试后恢复 |
|---|---:|---:|---:|---:|---:|
| Baseline | 250 | 250 | 0 | 2 | 2 |
| Generation v1 | 250 | 241 | 9 | 56 | 30 |

Attempts/retries 由每条正式 wrapper 保留的 `attempts` 数组独立复算；Generation Contract Success 不代表 SQL 语义正确。

## Offline judge execution（工程门禁）

**FAIL：judge call success 249/250，failure 1，retry 2。**
全部 250 条都必须得到成功 judge 结果；只有 judgment record 而 judge 失败，不满足工程门禁。

## 完整指标表

| 指标 | Baseline Closed-Book | Generation v1 Retrieval-v1 RAG | Generation v1 - Baseline |
|---|---:|---:|---:|
| Generation Contract Success Rate | 100.00% | 96.40% | -3.60pp |
| Task Success Rate | 50.80% | 68.00% | +17.20pp |
| Root Cause Accuracy | 82.40% | 89.60% | +7.20pp |
| SQL Repair Correctness | 52.40% | 68.40% | +16.00pp |
| Dialect Compatibility | 80.00% | 90.80% | +10.80pp |
| Version Compatibility | 79.60% | 90.80% | +11.20pp |
| Structured Output Validity | 100.00% | 96.40% | -3.60pp |
| Answer Relevance | 0.8194 | 0.8910 | +0.0716 |
| Citation Validity | N/A | 100.00% | N/A |
| Citation Coverage | N/A | 0.6416 | N/A |
| Faithfulness | N/A | 0.7726 | N/A |
| Context Precision (qrels rel>=1) | N/A | 0.2792 | N/A |
| Context Query Hit Rate (qrels rel>=1) | N/A | 72.40% | N/A |

Task Success 只在根因、SQL 修复、dialect 兼容性和 version 兼容性四项同时为真时计 1。
Generation v1 相对 Baseline 的绝对变化为 **+17.20 个百分点**；主要目标（至少 +10pp）：**达到**。

## Paired per-query comparison

完整 250 行配对结果见 [per_query_comparison.jsonl](../evaluation/per_query_comparison.jsonl)。
该文件逐 query 保留两系统 generation status、四项任务判断、structured validity、latency、judge retry，
以及 Generation v1 的 citation/context audit；没有删除失败案例。

| 配对结果 | 查询数 |
|---|---:|
| Generation v1 改善（Baseline Task fail → Generation v1 Task Success） | 71 |
| Generation v1 变差（Baseline Task Success → Generation v1 Task fail） | 28 |
| 两者均 Task Success | 99 |
| 两者均 Task fail | 52 |

## Generation v1 改善最明显的案例

实际符合 Baseline Task fail → Generation v1 Task Success 的案例共 71 条；不足 3 条时以明确占位行保留报告结构，不会把 tie 或 regression 冒充 improvement。

| Query | Paired outcome | Baseline → Generation v1 task | Context | Judge 摘要 |
|---|---|---:|---:|---|
| `DEV0008` | generation_v1_improved | 0 → 1 | P=0.20, hit=true | Baseline: B incorrectly claims the original SQL is valid in PostgreSQL and fails to provide the required FROM/WHERE syntax, contradicting the reference fix and evidence.; Generation v1: A correctly identifies the root c… |
| `DEV0013` | generation_v1_improved | 0 → 1 | P=0.20, hit=true | Baseline: Answer A incorrectly identifies the root cause as a deprecated operator '<->' and suggests replacing it with '<@', which is semantically incorrect for tsquery matching. It fails to recognize that the original … |
| `DEV0023` | generation_v1_improved | 0 → 1 | P=0.40, hit=true | Baseline: Answer A fails to identify the root cause (nested aggregates in SELECT) and proposes a syntactically invalid fix that does not compute the required average of per-account sums.; Generation v1: Answer B correct… |

## Generation v1 没有改善或表现更差的案例

| Query | Paired outcome | Baseline → Generation v1 task | Context | Judge 摘要 |
|---|---|---:|---:|---|
| `DEV0061` | generation_v1_regressed | 1 → 0 | P=0.20, hit=true | Baseline: Correctly identified INTERSECT as unsupported in 8.0.30 and provided a semantically equivalent fix using IN subquery, but failed to cite the specific version boundary evidence.; Generation v1: Incorrectly clai… |
| `DEV0083` | generation_v1_regressed | 1 → 0 | P=0.00, hit=false | Baseline: A correctly identifies the NULL poison in NOT IN and provides a valid fix, but fails to cite the retrieved evidence.; Generation v1: formal generation wrapper records a failed model call |
| `DEV0103` | generation_v1_regressed | 1 → 0 | P=0.00, hit=false | Baseline: Correctly identified the root cause and provided a semantically equivalent fix for SQLite, but failed to cite any retrieved evidence despite having access to relevant SQLite documentation.; Generation v1: form… |

## RAG 有效与无效的原因

- Generation failure 分开统计：Baseline 0 条，Generation v1 9 条；offline judge failure 1 条。以下 context/evidence-utilization 计数只分析 judge 与 Generation v1 generation 都成功的 240 条，不会把调用失败误归因于 retrieval 或 evidence utilization。
- 在 qrels rel>=1 context hit 的查询中，60 条转为 Task Success，115 条没有形成净改善。命中相关 passage 是必要帮助，但不保证模型会利用它。
- 65 条查询的 Top-5 没有 rel>=1 hit；这类失败更可能来自 retrieval context 不相关或不足。
- 192 条 Generation v1 answer 的 faithfulness ≥ 0.8，48 条低于 0.8；相关 context 已存在但 faithfulness 仍低时，问题更接近 evidence utilization 或模型能力。
- 84 条答案没有虚构 citation（validity=1）但 coverage<0.8；这说明 citation validity 单独不能证明关键诊断与修复都被证据覆盖。
- Citation Validity 对零引用采用 vacuous 1.0（没有虚构 passage ID）；它不表示存在证据支持，必须与 Citation Coverage、generation/judge failure 一起解释。
- paired case 的 judge reason 与 context/citation audit 保存在 per-query artifact，可进一步区分 retrieval context、prompt、模型 SQL 能力和 evidence utilization。
- 解释限制：generation 与 offline judge 使用同一个 Qwen 模型，可能存在相关的 self-judge 偏差；reference 也是 machine-proposed development reference，而非人工 gold。因此这些分数适合 baseline 对照与失败分析，不应被表述为独立人工裁决。

## Generation latency

latency 是正式 wrapper 记录的端到端 generation wall time；judge latency 不混入此表。

| 系统 | Mean (ms) | P50 (ms) | P95 (ms) |
|---|---:|---:|---:|
| Baseline | 20359.111 | 19139.369 | 25354.586 |
| Generation v1 | 32508.251 | 28171.236 | 62102.867 |
| Generation v1 - Baseline | +12149.139 | +9031.867 | +36748.281 |

## 500 个正式结果 wrapper 与 provenance

- Baseline 的 250 个 wrapper：`generation/baseline/runs/baseline_closed_book_dev250.jsonl`。
- Generation v1 的 250 个 wrapper：`generation/generation-v1/runs/generation_v1_rag_dev250.jsonl`。
- Generation v1 实际 Top-5 evidence：`generation/generation-v1/prepared_inputs/generation_v1_evidence_top5.jsonl`。
- 匿名配对 judge journal：`generation/generation-v1/evaluation/judgments.jsonl`。
- Generation seal：`generation/generation-v1/evaluation/generation_seal.json`。
- 命名迁移 ledger：`generation/generation-v1/provenance/system_naming_migration.json`。

每个 answer wrapper 自带 input provenance、prompt SHA、exact model provenance、统一 retry attempts 和 wall latency；
因此 500 个正式结果 wrapper 都可以回溯到自己的无标签输入和模型调用；其中生成失败项的 `answer=null`，但仍保留完整 failure 与 provenance。

## Acceptance

- Engineering：**FAIL**。
- Evaluation integrity：**PASS**。
- Quality target：**PASS**。
- Phase success：**FAIL**。

任何失败门禁都不会触发 reference label 修改、失败案例删除或结果覆盖。

## 从干净环境重新生成与评估

在仓库根目录执行：

```powershell
python -m venv .venv-generation-v1
.\.venv-generation-v1\Scripts\python.exe -m pip install --upgrade pip
.\.venv-generation-v1\Scripts\python.exe -m pip install -r generation\generation-v1\requirements.txt
.\.venv-generation-v1\Scripts\python.exe -m pip install -e generation\generation-v1 --no-deps
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Resolve-Path 'generation\generation-v1\src')
.\.venv-generation-v1\Scripts\python.exe -m sqlmend_generation_v1.cli --root . all --clean
```
