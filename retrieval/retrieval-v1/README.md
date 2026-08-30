# SQLMend-RAG Retrieval v1

Retrieval v1 在冻结的 BM25、Dense E5 与 Hybrid RRF baseline 之上加入方言感知、版本感知和一个轻量 lexical reranker。它是独立 release；不会改写 `construction/`、`annotation/codex/` 或 `retrieval/baseline/`。

这里的 250 条查询及 relevance judgments 只能称为 **machine-proposed development data**。它们用于开发实验和回归验证，不是人工 gold，也不是最终 held-out test set。

## 五个正式系统

| 简称 | System ID | 配置 | Run |
|---|---|---|---|
| Frozen Hybrid | `hybrid_rrf_frozen_control_v1` | `config/systems/frozen_hybrid_control.yaml` | 引用冻结的 `retrieval/baseline/runs/hybrid_rrf_formal_dev250.trec` |
| + Dialect | `hybrid_rrf_dialect_aware_v1` | `config/systems/dialect_aware.yaml` | `runs/hybrid_rrf_dialect_aware_dev250.trec` |
| + Version | `hybrid_rrf_version_aware_v1` | `config/systems/version_aware.yaml` | `runs/hybrid_rrf_version_aware_dev250.trec` |
| + Dialect + Version | `hybrid_rrf_dialect_version_aware_v1` | `config/systems/dialect_version_aware.yaml` | `runs/hybrid_rrf_dialect_version_aware_dev250.trec` |
| + Dialect + Version + Reranker | `hybrid_rrf_dialect_version_lexical_rerank_v1` | `config/systems/dialect_version_reranker.yaml` | `runs/hybrid_rrf_dialect_version_lexical_rerank_dev250.trec` |

所有新系统都从冻结 BM25 Top-30 与 Dense Top-30 的 RRF union 取候选。每条查询有 45–60 个候选；metadata awareness 与 reranker 只做 soft reranking，不删除跨方言、旧版本或 metadata 未知的文档。最终输出深度固定为 30。

## 方法

Dialect awareness 使用查询 dialect 与 corpus-owned dialect metadata。相同方言优先；MySQL/MariaDB 作为 related，但仍按显式 dialect mismatch 计入 `Wrong-Dialect@5`；unknown 位于 related 与明确 incompatible 之间。任何类别都不会被硬过滤。

Version awareness 只使用 corpus metadata 和 passage 中明确、保守匹配的版本边界，不从文档时间或措辞凭空推断支持范围。优先级是 compatible、general、unknown、incompatible；跨 dialect 的版本命名空间标为 `not_applicable`。如果一个排除当前版本的明确边界直接点名查询中的函数、运算符或错误标识，它属于有用的诊断证据，而不是错误版本证据。

Reranker 对 `user_problem`、SQL、实际错误字段与 candidate passage 分别计算确定性的 corpus-IDF BM25/精确标识匹配，再以很小的权重与 Dialect+Version score 相加。它不训练模型、不访问网络、不读取 qrels、reference fix、root cause、case flags 或 candidate labels。

在线排序的数据边界是 `OnlineQuery`：dialect、version、user problem、SQL、实际 error message/code/SQLSTATE/symbol 以及冻结 serializer 的文本。原始 annotation records 只在安全 projection 与离线 slice evaluation 中出现；离线 qrels 加载位于 `experiment.py`，不会进入 `build-runs`。

## 开发集结果

下表来自完整判断的五系统 Top-30 pool；`Judged@30` 均为 1.0。精确值、全部切片、成功/失败案例和 latency 以 `reports/retrieval_v1_report.md`、`evaluation/comparison_results.json` 与 `reports/latency.json` 为准。

| 系统 | nDCG@10 | MRR@10 rel2 | pooled Recall@10 rel2 | HitRate@5 rel2 | Wrong-Dialect@5 | Wrong-Version@5 | Unknown-Version@5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Frozen Hybrid | 0.306983 | 0.431941 | 0.500162 | 0.544 | 0.2704 | 0.0032 | 0.0096 |
| + Dialect | 0.325623 | 0.453219 | 0.519162 | 0.568 | 0.1008 | 0.0040 | 0.0112 |
| + Version | 0.317487 | 0.450606 | 0.508029 | 0.576 | 0.1720 | 0.0016 | 0.0080 |
| + Dialect + Version | 0.329306 | 0.468895 | 0.536695 | 0.588 | 0.0944 | 0.0008 | 0.0072 |
| + Dialect + Version + Reranker | 0.345570 | 0.494748 | 0.559629 | 0.632 | 0.0952 | 0.0008 | 0.0088 |

最终系统相对 Frozen Hybrid 的 nDCG、MRR 与 pooled Recall 分别提升 `+0.038587`、`+0.062806`、`+0.059467`。在 174 条 dialect-sensitive 查询上，`Wrong-Dialect@5` 相对下降 63.68%；在 53 条 version-sensitive 查询上，`Wrong-Version@5` 相对下降 66.67%。所有 Phase 7/8/9 与最终 acceptance gates 均通过。

这些数字不能外推为人工测试性能。只要任何新正式 Top-30 pair 缺少判断，`check-pool` 就把它写入 `pool_expansion/pool_expansion_required.jsonl`，evaluation 状态变为 `BLOCKED`，且不发布 nDCG、MRR 或 pooled Recall。

## 从干净环境重建

以下命令从仓库根目录运行。Python 3.11+ 可用；当前冻结环境为 Python 3.12。

```powershell
python -m venv .venv-retrieval-v1
.\.venv-retrieval-v1\Scripts\python.exe -m pip install --upgrade pip
.\.venv-retrieval-v1\Scripts\python.exe -m pip install -r retrieval\retrieval-v1\requirements.txt
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Resolve-Path 'retrieval\retrieval-v1\src')
.\.venv-retrieval-v1\Scripts\python.exe -m sqlmend_retrieval_v1.cli --root . all --clean
```

分步重建和审计顺序如下：

```powershell
python -m sqlmend_retrieval_v1.cli --root . audit-protected-paths --phase before
python -m sqlmend_retrieval_v1.cli --root . verify-inputs
python -m sqlmend_retrieval_v1.cli --root . build-runs
python -m sqlmend_retrieval_v1.cli --root . check-pool
python -m sqlmend_retrieval_v1.cli --root . evaluate
python -m sqlmend_retrieval_v1.cli --root . benchmark
python -m sqlmend_retrieval_v1.cli --root . test
python -m sqlmend_retrieval_v1.cli --root . audit-protected-paths --phase after
python -m sqlmend_retrieval_v1.cli --root . finalize
python -m sqlmend_retrieval_v1.cli --root . validate
```

`build-runs` 是无标签在线路径；`check-pool` 之后的命令属于离线 evaluation/release 路径。`clean` 只移除 `retrieval/retrieval-v1/` 下已知的生成目录和 manifest，不触碰源码、测试、配置、本文或任何冻结目录。

## 正式证据

- `manifest.json`：输入、配置、系统、runs、provenance、评估、报告和测试证据的 SHA-256 绑定。
- `reports/validation_report.json`：从当前文件重新计算的独立验证结果。
- `reports/protected_paths_before.json` / `protected_paths_after.json`：三个受保护目录的完整 byte snapshot。
- `evaluation/acceptance.json`：Phase 7/8/9 与最终门禁。
- `evaluation/judged_coverage.json`：五系统 Top-30 判断完整性。
- `reports/test_results.json`：测试前后 source-tree hash 与 pytest 输出。

本阶段没有实现 generator、UI 或最终人工 held-out dataset。
