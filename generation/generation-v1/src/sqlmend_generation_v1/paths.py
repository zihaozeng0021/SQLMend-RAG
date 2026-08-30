"""Repository-relative paths for the isolated generation-v1 release."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import BASELINE_SYSTEM_ID, GENERATION_V1_SYSTEM_ID


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
                    if (candidate / "construction" / "data" / "processed" / "corpus.jsonl").is_file()
                    and (candidate / "retrieval" / "retrieval-v1").is_dir()
                ),
                None,
            )
            if root is None:
                raise FileNotFoundError("Cannot locate the SQLMend-RAG repository root")
        required = (
            root / "construction" / "data" / "processed" / "corpus.jsonl",
            root / "retrieval" / "retrieval-v1" / "serialized_queries" / "dev_250_queries.jsonl",
            root
            / "retrieval"
            / "retrieval-v1"
            / "runs"
            / "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec",
        )
        if not all(path.is_file() for path in required):
            raise FileNotFoundError(f"Not a complete SQLMend-RAG repository root: {root}")
        return cls(root)

    @property
    def release(self) -> Path:
        return self.root / "generation" / "generation-v1"

    @property
    def baseline(self) -> Path:
        return self.root / "generation" / "baseline"

    @property
    def config_file(self) -> Path:
        return self.release / "config" / "generation.yaml"

    @property
    def answer_schema(self) -> Path:
        return self.release / "schema" / "answer.schema.json"

    @property
    def frozen_serialized_queries(self) -> Path:
        return (
            self.root
            / "retrieval"
            / "retrieval-v1"
            / "serialized_queries"
            / "dev_250_queries.jsonl"
        )

    @property
    def final_retrieval_run(self) -> Path:
        return (
            self.root
            / "retrieval"
            / "retrieval-v1"
            / "runs"
            / "hybrid_rrf_dialect_version_lexical_rerank_dev250.trec"
        )

    @property
    def corpus(self) -> Path:
        return self.root / "construction" / "data" / "processed" / "corpus.jsonl"

    @property
    def prepared_inputs(self) -> Path:
        return self.release / "prepared_inputs"

    @property
    def prepared_queries(self) -> Path:
        return self.prepared_inputs / "online_queries.jsonl"

    @property
    def generation_v1_evidence(self) -> Path:
        return self.prepared_inputs / "generation_v1_evidence_top5.jsonl"

    @property
    def runs(self) -> Path:
        return self.release / "runs"

    @property
    def baseline_runs(self) -> Path:
        return self.baseline / "runs"

    @property
    def baseline_reports(self) -> Path:
        return self.baseline / "reports"

    @property
    def baseline_manifest(self) -> Path:
        return self.baseline / "manifest.json"

    @property
    def baseline_run(self) -> Path:
        return self.baseline_runs / "baseline_closed_book_dev250.jsonl"

    @property
    def generation_v1_run(self) -> Path:
        return self.runs / "generation_v1_rag_dev250.jsonl"

    def result_path(self, system_id: str) -> Path:
        if system_id == BASELINE_SYSTEM_ID:
            return self.baseline_run
        if system_id == GENERATION_V1_SYSTEM_ID:
            return self.generation_v1_run
        raise ValueError(f"Unknown generation system: {system_id!r}")
