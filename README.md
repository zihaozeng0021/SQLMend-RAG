# SQLMend-RAG

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/)

## Install

This Python project requires no separate compilation step. Run from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\retrieval\retrieval-v1[test]" -e ".\generation\generation-v1[test]"
ollama pull qwen3.5:4b
```

## Run

The knowledge base, annotations, and retrieval baseline are included. Start Ollama, then run Retrieval v1 and Generation v1:

```powershell
python -m sqlmend_retrieval_v1.cli --root . all --clean
python -m sqlmend_generation_v1.cli --root . all --clean
```

