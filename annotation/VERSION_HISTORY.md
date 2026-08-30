# Annotation version history

当前主版本是 **v1**。`annotation/codex/` 是唯一供当前 baseline retrieval 使用的 Codex 机器开发标注；它不是人工 gold，也不是最终 held-out test set。

## v1 — current main version

### Dataset revision 1.1.0 — 2026-08-30

- 保持原 250 个开发 query、12,000-chunk corpus 和 v1 身份不变。
- 对冻结 BM25、dense、hybrid 三套正式 run 的 Top-30 并集做了两轮独立盲标，共 14,232 个 query/chunk pair；906 个标签分歧由第三轮盲裁决处理。
- 模型提示中不包含旧 qrels、query/chunk ID、系统身份、rank 或 score；A/B 候选顺序独立打乱。
- 原 v1 的显式 case-evidence 标签优先保留；正式范围内的启发式标签由盲标结果替换；原先缺失的 10,003 个 pair 全部加入主 qrels；正式范围外的旧 v1 pair 保留。
- 主 qrels 现有 23,452 个判断，正式三路 `Judged@30=1.0`。
- 完整性与一致性摘要见 `codex/provenance/top30_blind_refresh.json`；指标敏感性见 `codex/reports/top30_annotation_sensitivity.json`。

### Dataset revision 1.0.0 — 2026-08-28

- 创建 250 个 Codex 机器建议开发 query，每个方言 50 个。
- qrels 由显式 case evidence 与 deterministic contextual heuristic 构成，共 13,449 个判断。
- 该 pool 来自历史 annotation retriever，并未覆盖后来冻结的正式 baseline Top-30，因此不能用于完整评估正式 baseline。

## v2 — abandoned experiment

v2 曾作为实验性重建尝试，但未成为主版本，也不供当前 retrieval 读取。主工作树不保留 v2 标注产物；后续 dialect/version-aware retrieval 应以当前 v1 development qrels 为对照，另行版本化 retrieval 系统，而不是恢复或混用 v2 annotation。

## Interpretation limits

- 所有 v1 标注仍是 machine-proposed development data；双盲和裁决降低了单次判断与缺标带来的失真，但不能消除同模型共享偏差。
- 最终课程评估仍需独立、人工标注、冻结且未用于调参的 held-out set。
