# SQLMendRAG

SQLMendRAG 是一个方言和版本感知的 SQL 调试 RAG 项目。目标是根据可靠证据诊断 SQL 的语法、语义、兼容性、版本变化、错误消息和迁移问题，再给出修复建议。

仓库按项目阶段拆成独立模块。现在已经完成的是知识库构建阶段：

```text
SQLMend-RAG/
├─ construction/       # 文档采集、清洗、去重、分块、统计和验证
├─ README.md           # 整个 SQLMendRAG 项目的入口说明
└─ .gitignore          # 整个项目共用的 Git 规则
```

后续的索引、检索、评测、生成器或标注数据应建立自己的模块，不要混进 `construction/` 的 raw、interim 或 processed 数据目录。

## Knowledge base construction

构建模块的完整说明、来源、许可、版本策略、语料结构和验收结果都在 [construction/README.md](construction/README.md)。

从项目根目录开始：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".\construction[test]"
python -m sqlmend_pipeline.cli build
```

安装后的 CLI 会自动找到 `construction/`。也可以显式指定：

```powershell
python -m sqlmend_pipeline.cli --root construction build --skip-collect
python -m sqlmend_pipeline.cli --root construction validate
python -m pytest construction/tests -q
```

当前生产语料位于 `construction/data/processed/corpus.jsonl`。这里还没有实现 RAG 生成器。
