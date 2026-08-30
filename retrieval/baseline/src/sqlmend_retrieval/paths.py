"""Repository paths resolved independently of the current working directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @classmethod
    def discover(cls, explicit_root: str | Path | None = None) -> "ProjectPaths":
        if explicit_root is not None:
            root = Path(explicit_root).expanduser().resolve()
        else:
            here = Path(__file__).resolve()
            candidates = [Path.cwd().resolve(), *here.parents]
            root = next(
                (
                    candidate
                    for candidate in candidates
                    if (candidate / "construction").is_dir()
                    and (candidate / "annotation" / "codex").is_dir()
                ),
                None,
            )
            if root is None:
                raise FileNotFoundError(
                    "Cannot locate the SQLMend-RAG root containing construction/ and annotation/codex/."
                )
        if not (root / "construction").is_dir() or not (root / "annotation" / "codex").is_dir():
            raise FileNotFoundError(f"Not a SQLMend-RAG repository root: {root}")
        return cls(root=root)

    @property
    def retrieval(self) -> Path:
        return self.root / "retrieval" / "baseline"

    @property
    def corpus(self) -> Path:
        return self.root / "construction" / "data" / "processed" / "corpus.jsonl"

    @property
    def annotation(self) -> Path:
        return self.root / "annotation" / "codex"

    @property
    def queries(self) -> Path:
        return self.annotation / "dev_250.jsonl"

    @property
    def qrels_source(self) -> Path:
        return self.annotation / "qrels_machine_proposed.jsonl"

    @property
    def candidate_pools(self) -> Path:
        return self.annotation / "candidate_pools.jsonl"

    @property
    def protected_report(self) -> Path:
        return self.retrieval / "reports" / "protected_paths_report.json"

    @property
    def config(self) -> Path:
        return self.retrieval / "config"

    @property
    def serialized_queries(self) -> Path:
        return self.retrieval / "serialized_queries" / "dev_250_queries.jsonl"

    @property
    def bm25_index(self) -> Path:
        return self.retrieval / "indices" / "bm25"

    @property
    def dense_index(self) -> Path:
        return self.retrieval / "indices" / "dense"

    @property
    def runs(self) -> Path:
        return self.retrieval / "runs"

    @property
    def bm25_run(self) -> Path:
        return self.runs / "bm25_formal_dev250.trec"

    @property
    def dense_run(self) -> Path:
        return self.runs / "dense_formal_dev250.trec"

    @property
    def hybrid_run(self) -> Path:
        return self.runs / "hybrid_rrf_formal_dev250.trec"

    @property
    def hybrid_provenance(self) -> Path:
        return self.runs / "hybrid_rrf_formal_dev250.provenance.jsonl"

    @property
    def qrels(self) -> Path:
        return self.retrieval / "qrels" / "qrels_machine_proposed_dev250.trec"

    @property
    def supplemental_qrels(self) -> Path:
        """Optional external judgments; never generated or overwritten by the pipeline."""

        return self.retrieval / "qrels" / "pool_expansion_judgments.jsonl"

    @property
    def effective_qrels(self) -> Path:
        return self.retrieval / "qrels" / "qrels_effective_dev250.trec"

    @property
    def pool_expansion(self) -> Path:
        return self.retrieval / "pool_expansion"

    @property
    def evaluation(self) -> Path:
        return self.retrieval / "evaluation"

    @property
    def reports(self) -> Path:
        return self.retrieval / "reports"

    @property
    def reproduction(self) -> Path:
        return self.retrieval / "reproduction"
