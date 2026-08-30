# SQLMend-RAG

SQLMend-RAG 是一个方言和版本感知的 SQL 调试 RAG 项目。仓库已经完成知识库构建、机器开发集、冻结检索 baseline、Retrieval v1，以及 Phase 10 的 Generation Baseline / Generation v1 对照。UI、最终 1,000+ 条人工 held-out 数据和最终课程报告仍未完成。

## 当前模块

| 模块 | 状态 | 入口 |
|---|---|---|
| Knowledge-base construction | 已验证完成 | [construction/README.md](construction/README.md) |
| Machine-proposed development annotation | 已验证，仅限开发评估 | [annotation/codex/README.md](annotation/codex/README.md) |
| Frozen retrieval baseline | 已验证完成 | [retrieval/baseline/README.md](retrieval/baseline/README.md) |
| Retrieval v1 | 已验证，仅限开发评估 | [retrieval/retrieval-v1/README.md](retrieval/retrieval-v1/README.md) |
| Generation Baseline | Closed-Book 正式基线已封存 | [generation/baseline/README.md](generation/baseline/README.md) |
| Generation v1 | Retrieval-v1 RAG 质量目标通过；Phase 10 总验收未通过 | [generation/generation-v1/README.md](generation/generation-v1/README.md) |

各阶段是独立模块。不要覆盖现有 knowledge base、annotation、retrieval baseline、Retrieval v1 或 generation artifacts；新版本应使用新的模块、system ID 和 provenance。

## Phase 10 结果

Generation v1 使用同一个本地 Ollama `qwen3.5:4b` 模型比较：

- Baseline Closed-Book：只接收用户可观察的 SQL debugging 输入；
- Generation v1 Retrieval-v1 RAG：在相同输入、prompt、schema 和 decoding 设置之外，额外接收冻结 Retrieval v1 Final Top-5 evidence。

精确模型 digest 为 `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`，两系统均设置 `think=false`。250 条查询共产生 500 个正式结果 wrapper，失败案例也保留在分母中。

| 指标 | Baseline | Generation v1 |
|---|---:|---:|
| Generation Contract Success | 250/250 | 241/250 |
| Task Success Rate | 50.8% | 68.0% |
| Root Cause Accuracy | 82.4% | 89.6% |
| SQL Repair Correctness | 52.4% | 68.4% |
| Dialect Compatibility | 80.0% | 90.8% |
| Version Compatibility | 79.6% | 90.8% |
| Structured Output Validity | 100.0% | 96.4% |
| Mean / P50 / P95 latency | 20.36 / 19.14 / 25.35 s | 32.51 / 28.17 / 62.10 s |

这里的 **Generation Contract Success** 只表示模型调用最终产生了满足 JSON/schema/citation contract 的 wrapper，不表示 SQL 已修对。**Task Success** 才表示根因、SQL 修复、dialect 和 version compatibility 四项同时正确。

Generation v1 的 Task Success 比 Baseline 绝对提高 `17.2` 个百分点，超过 `+10pp` 质量目标；但 Phase 10 总验收仍为 **FAIL**：Generation v1 Structured Output Validity 只有 `96.4%`，低于 `98%`，且离线 judge 只有 `249/250` 次调用成功。真实失败记录没有被删除或覆盖。

完整指标、paired cases、案例分析和限制见 [Generation v1 report](generation/generation-v1/reports/generation_v1_report.md)。这些结果来自 machine-proposed development data，不是人工 gold 或最终 held-out test 结果。

## 重建 Generation v1

需要 Python 3.11+、本地 Ollama，以及精确的 `qwen3.5:4b` 模型。以下命令会清理并重建 `generation/baseline/` 与 `generation/generation-v1/` 中允许重建的正式工件；不会修改冻结知识库、标注和检索资产。

```powershell
ollama pull qwen3.5:4b
python -m venv .venv-generation-v1
.\.venv-generation-v1\Scripts\python.exe -m pip install --upgrade pip
.\.venv-generation-v1\Scripts\python.exe -m pip install -r generation\generation-v1\requirements.txt
.\.venv-generation-v1\Scripts\python.exe -m pip install -e generation\generation-v1 --no-deps
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv-generation-v1\Scripts\python.exe -m sqlmend_generation_v1.cli --root . all --clean
```

`all --clean` 在所有工件写完后根据 acceptance gates 返回状态；当前正式结果预期以非零退出，因为上述工程门禁真实失败。非零退出不等于流程中途崩溃。详细分步命令和恢复语义见 [Generation v1 README](generation/generation-v1/README.md)。

主要证据：

- [Baseline formal run](generation/baseline/runs/baseline_closed_book_dev250.jsonl)
- [Generation v1 formal run](generation/generation-v1/runs/generation_v1_rag_dev250.jsonl)
- [Baseline manifest](generation/baseline/manifest.json)
- [Per-query comparison](generation/generation-v1/evaluation/per_query_comparison.jsonl)
- [Overall metrics](generation/generation-v1/evaluation/overall_metrics.json)
- [Validation report](generation/generation-v1/reports/validation_report.json)
- [Manifest](generation/generation-v1/manifest.json)
- [Naming migration provenance](generation/generation-v1/provenance/system_naming_migration.json)

内部阶段状态、受保护路径和变更影响规则见 [DEVELOPMENT_CONTEXT.md](DEVELOPMENT_CONTEXT.md)。
