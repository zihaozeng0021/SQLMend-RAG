"""Repository-relative paths for the isolated retrieval-v1 release."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path

    @classmethod
    def discover(cls, explicit_root: str | Path | None = None) -> "ProjectPaths":
        if explicit_root is not None:
            root = Path(explicit_root).expanduser().resolve()
        else:
            here = Path(__file__).resolve()
            root = next(
                (
                    candidate
                    for candidate in (Path.cwd().resolve(), *here.parents)
                    if (candidate / "construction").is_dir()
                    and (candidate / "annotation" / "codex").is_dir()
                    and (candidate / "retrieval" / "baseline").is_dir()
                ),
                None,
            )
            if root is None:
                raise FileNotFoundError("Cannot locate the SQLMend-RAG repository root")
        required = (
            root / "construction",
            root / "annotation" / "codex",
            root / "retrieval" / "baseline",
        )
        if not all(path.is_dir() for path in required):
            raise FileNotFoundError(f"Not a SQLMend-RAG repository root: {root}")
        return cls(root)

    @property
    def release(self) -> Path:
        return self.root / "retrieval" / "retrieval-v1"

    @property
    def baseline(self) -> Path:
        return self.root / "retrieval" / "baseline"

    @property
    def corpus(self) -> Path:
        return self.root / "construction" / "data" / "processed" / "corpus.jsonl"

    @property
    def queries(self) -> Path:
        return self.root / "annotation" / "codex" / "dev_250.jsonl"

    @property
    def qrels(self) -> Path:
        return self.baseline / "qrels" / "qrels_effective_dev250.trec"

    @property
    def baseline_run(self) -> Path:
        return self.baseline / "runs" / "hybrid_rrf_formal_dev250.trec"

    @property
    def baseline_bm25_run(self) -> Path:
        return self.baseline / "runs" / "bm25_formal_dev250.trec"

    @property
    def baseline_dense_run(self) -> Path:
        return self.baseline / "runs" / "dense_formal_dev250.trec"

    @property
    def baseline_serialized_queries(self) -> Path:
        return self.baseline / "serialized_queries" / "dev_250_queries.jsonl"

    @property
    def config(self) -> Path:
        return self.release / "config"

    @property
    def system_configs(self) -> Path:
        return self.config / "systems"

    @property
    def runs(self) -> Path:
        return self.release / "runs"

    @property
    def evaluation(self) -> Path:
        return self.release / "evaluation"

    @property
    def reports(self) -> Path:
        return self.release / "reports"

    @property
    def pool_expansion(self) -> Path:
        return self.release / "pool_expansion"

    @property
    def serialized_queries(self) -> Path:
        return self.release / "serialized_queries" / "dev_250_queries.jsonl"

    @property
    def reranker_cache(self) -> Path:
        return self.release / "indices" / "reranker" / "model_cache"
