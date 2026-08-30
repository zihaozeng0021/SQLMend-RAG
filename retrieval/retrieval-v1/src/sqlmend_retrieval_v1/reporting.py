"""Deterministic, leakage-safe offline reporting for retrieval v1.

The report is a presentation layer over already-produced evaluation artifacts.
Qrels are admitted only here, after retrieval, to verify judged coverage and to
annotate the qualitative case studies.  Query text comes exclusively from the
frozen safe-serializer artifact; raw development records are not an input.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .evaluation import DIALECTS
from .io import group_run, load_json, load_jsonl, read_qrels, read_trec_run
from .models import RunEntry
from .pool import FORMAL_SYSTEM_IDS
from .query import ALLOWED_SOURCE_FIELDS, SERIALIZER_VERSION


EVALUATION_LABEL = "machine-proposed development evaluation"
BASELINE_SYSTEM_ID = "hybrid_rrf_frozen_control_v1"
DIALECT_SYSTEM_ID = "hybrid_rrf_dialect_aware_v1"
VERSION_SYSTEM_ID = "hybrid_rrf_version_aware_v1"
FINAL_SYSTEM_ID = "hybrid_rrf_dialect_version_lexical_rerank_v1"
COMBINED_SYSTEM_ID = "hybrid_rrf_dialect_version_aware_v1"
SYSTEM_ORDER = tuple(FORMAL_SYSTEM_IDS)
REPORT_SCHEMA_VERSION = "sqlmend-retrieval-v1-report-v1"

QUALITY_METRICS = (
    "graded_nDCG@10",
    "MRR@10_rel2",
    "pooled_Recall@10_rel2",
    "HitRate@5_rel2",
    "Wrong-Dialect@5",
    "Wrong-Version@5",
    "Unknown-Version@5",
    "Judged@30",
)

_SERIALIZED_QUERY_KEYS = frozenset(
    {
        "query_id",
        "source_fields_used",
        "serialized_text",
        "serialized_text_sha256",
        "serializer_version",
    }
)
_SECTION_BOUNDARY = r"(?=\n\n(?:Observed error or behavior:|SQL:)\n|\Z)"


class ReportBlockedError(RuntimeError):
    """Raised before publication when the formal pool is not fully judged."""


@dataclass(frozen=True, slots=True)
class ReportSources:
    """Paths to immutable inputs consumed by :func:`generate_retrieval_v1_report`.

    ``serialized_queries`` must be the safe serializer artifact, not the raw
    query records.  ``qrels`` is explicitly an offline-analysis-only input.
    """

    overall_metrics: Path
    slice_metrics: Path
    per_query_metrics: Path
    runs: Mapping[str, Path]
    serialized_queries: Path
    corpus: Path
    qrels: Path
    latency: Path
    acceptance: Path
    evaluation_status: Path


@dataclass(frozen=True, slots=True)
class _SafeQueryView:
    query_id: str
    problem: str | None
    dialect: str | None
    version: str | None


@dataclass(frozen=True, slots=True)
class _LatencyView:
    method: str
    mean_ms: float
    p50_ms: float
    p95_ms: float
    incremental_mean_ms: float | None
    incremental_p50_ms: float | None
    incremental_p95_ms: float | None


@dataclass(frozen=True, slots=True)
class _CaseDelta:
    query_id: str
    baseline_ndcg: float
    final_ndcg: float

    @property
    def delta(self) -> float:
        return self.final_ndcg - self.baseline_ndcg


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError(f"CSV has no data rows: {path}")
    return rows


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} must be a non-empty, trimmed string")
    return value


def _number(value: Any, field: str, *, nonnegative: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if nonnegative and result < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return result


def _extract_serialized_field(serialized: str, label: str) -> str | None:
    if label in {"Dialect", "Version"}:
        match = re.search(rf"(?m)^{re.escape(label)}:[ \t]*(.+)$", serialized)
    elif label == "Question":
        match = re.search(
            rf"(?ms)(?:^|\n\n){re.escape(label)}:\n(.*?){_SECTION_BOUNDARY}",
            serialized,
        )
    else:  # pragma: no cover - internal misuse guard
        raise ValueError(f"Unsupported safe serialized field: {label}")
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def _load_safe_queries(path: Path) -> dict[str, _SafeQueryView]:
    result: dict[str, _SafeQueryView] = {}
    for record in load_jsonl(path):
        unexpected = sorted(set(record) - _SERIALIZED_QUERY_KEYS)
        if unexpected:
            raise ValueError(
                "serialized query input contains fields outside the safe artifact "
                f"contract: {unexpected}"
            )
        query_id = _identifier(record.get("query_id"), "serialized query_id")
        if query_id in result:
            raise ValueError(f"duplicate serialized query: {query_id!r}")
        if record.get("serializer_version") != SERIALIZER_VERSION:
            raise ValueError(f"unexpected serializer version for {query_id!r}")
        source_fields = record.get("source_fields_used")
        if not isinstance(source_fields, list) or any(
            not isinstance(field, str) or field not in ALLOWED_SOURCE_FIELDS
            for field in source_fields
        ):
            raise ValueError(f"unsafe source_fields_used for {query_id!r}")
        serialized = record.get("serialized_text")
        if not isinstance(serialized, str) or not serialized.strip():
            raise ValueError(f"missing serialized_text for {query_id!r}")
        expected_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if record.get("serialized_text_sha256") != expected_hash:
            raise ValueError(f"serialized query hash mismatch for {query_id!r}")
        result[query_id] = _SafeQueryView(
            query_id=query_id,
            problem=_extract_serialized_field(serialized, "Question"),
            dialect=_extract_serialized_field(serialized, "Dialect"),
            version=_extract_serialized_field(serialized, "Version"),
        )
    if not result:
        raise ValueError("serialized query input is empty")
    return dict(sorted(result.items()))


def _load_corpus(path: Path) -> dict[str, dict[str, str | None]]:
    """Load only the corpus-owned fields permitted in qualitative displays."""

    result: dict[str, dict[str, str | None]] = {}
    for record in load_jsonl(path):
        chunk_id = _identifier(record.get("chunk_id"), "corpus chunk_id")
        if chunk_id in result:
            raise ValueError(f"duplicate corpus chunk_id: {chunk_id!r}")

        def optional_text(field: str) -> str | None:
            value = record.get(field)
            return value if isinstance(value, str) and value.strip() else None

        result[chunk_id] = {
            "title": optional_text("title"),
            "dialect": optional_text("dialect"),
            "version": optional_text("version"),
            "version_min": optional_text("version_min"),
            "version_max": optional_text("version_max"),
            "version_status": optional_text("version_status"),
        }
    if not result:
        raise ValueError("corpus input is empty")
    return result


def _load_runs(paths: Mapping[str, Path]) -> dict[str, dict[str, list[RunEntry]]]:
    if set(paths) != set(SYSTEM_ORDER):
        missing = sorted(set(SYSTEM_ORDER) - set(paths))
        extra = sorted(set(paths) - set(SYSTEM_ORDER))
        raise ValueError(f"run system IDs differ; missing={missing!r}, extra={extra!r}")
    return {
        system_id: group_run(read_trec_run(Path(paths[system_id])))
        for system_id in SYSTEM_ORDER
    }


def _verify_judged_pool(
    runs: Mapping[str, Mapping[str, Sequence[RunEntry]]],
    qrels: Mapping[str, Mapping[str, int]],
    query_ids: set[str],
    *,
    depth: int,
) -> None:
    missing_pairs: set[tuple[str, str]] = set()
    for system_id in SYSTEM_ORDER:
        ranking_by_query = runs[system_id]
        if set(ranking_by_query) != query_ids:
            missing = sorted(query_ids - set(ranking_by_query))
            extra = sorted(set(ranking_by_query) - query_ids)
            raise ValueError(
                f"{system_id} query coverage differs; missing={missing!r}, extra={extra!r}"
            )
        for query_id in sorted(query_ids):
            ranking = ranking_by_query[query_id]
            if len(ranking) < depth:
                raise ReportBlockedError(
                    f"{system_id}/{query_id} has only {len(ranking)} rows; "
                    f"Judged@{depth} cannot be established"
                )
            for row in ranking[:depth]:
                if row.chunk_id not in qrels.get(query_id, {}):
                    missing_pairs.add((query_id, row.chunk_id))
    if missing_pairs:
        examples = ", ".join(f"{qid}/{docid}" for qid, docid in sorted(missing_pairs)[:3])
        raise ReportBlockedError(
            f"formal report blocked: {len(missing_pairs)} unique top-{depth} pairs are "
            f"unjudged (examples: {examples})"
        )


def _load_overall(path: Path) -> dict[str, dict[str, float]]:
    artifact = load_json(path)
    if artifact.get("evaluation_label") != EVALUATION_LABEL:
        raise ValueError("overall metrics are not labeled as machine-proposed development evaluation")
    systems = artifact.get("systems")
    if not isinstance(systems, Mapping) or set(systems) != set(SYSTEM_ORDER):
        raise ValueError("overall metrics must contain exactly the five formal systems")
    result: dict[str, dict[str, float]] = {}
    for system_id in SYSTEM_ORDER:
        raw = systems[system_id]
        if not isinstance(raw, Mapping):
            raise ValueError(f"overall metrics for {system_id!r} must be a mapping")
        result[system_id] = {
            metric: _number(raw.get(metric), f"{system_id}/{metric}")
            for metric in QUALITY_METRICS
        }
    return result


def _load_slices(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in _load_csv(path):
        system_id = _identifier(raw.get("system_id"), "slice system_id")
        slice_name = _identifier(raw.get("slice_name"), "slice_name")
        slice_value = _identifier(raw.get("slice_value"), "slice_value")
        if system_id not in SYSTEM_ORDER:
            raise ValueError(f"unknown system in slice metrics: {system_id!r}")
        key = (system_id, slice_name, slice_value)
        if key in result:
            raise ValueError(f"duplicate slice metrics row: {key!r}")
        result[key] = {
            "query_count": int(_number(raw.get("query_count"), f"{key}/query_count", nonnegative=True)),
            **{
                metric: _number(raw.get(metric), f"{key}/{metric}")
                for metric in QUALITY_METRICS
            },
        }
    required = {
        (system_id, "case_flag", slice_value)
        for system_id in SYSTEM_ORDER
        for slice_value in ("dialect-sensitive", "version-sensitive")
    } | {
        (system_id, "dialect", dialect)
        for system_id in SYSTEM_ORDER
        for dialect in DIALECTS
    }
    missing = sorted(required - set(result))
    if missing:
        raise ValueError(f"required slice metrics are missing: {missing[:3]!r}")
    return result


def _load_per_query(
    path: Path,
    query_ids: set[str],
) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for raw in _load_csv(path):
        system_id = _identifier(raw.get("system_id"), "per-query system_id")
        query_id = _identifier(raw.get("query_id"), "per-query query_id")
        if system_id not in SYSTEM_ORDER:
            raise ValueError(f"unknown system in per-query metrics: {system_id!r}")
        key = (system_id, query_id)
        if key in result:
            raise ValueError(f"duplicate per-query metrics row: {key!r}")
        result[key] = _number(raw.get("graded_nDCG@10"), f"{key}/graded_nDCG@10")
    expected = {(system_id, query_id) for system_id in SYSTEM_ORDER for query_id in query_ids}
    if set(result) != expected:
        missing = sorted(expected - set(result))
        extra = sorted(set(result) - expected)
        raise ValueError(
            f"per-query metric coverage differs; missing={missing[:3]!r}, extra={extra[:3]!r}"
        )
    return result


def _stats_from_mapping(raw: Mapping[str, Any], field: str) -> tuple[float, float, float]:
    mean = _number(raw.get("mean_ms"), f"{field}/mean_ms", nonnegative=True)
    p50_value = raw.get("p50_ms", raw.get("median_ms"))
    p50 = _number(p50_value, f"{field}/p50_ms", nonnegative=True)
    p95 = _number(raw.get("p95_ms"), f"{field}/p95_ms", nonnegative=True)
    return mean, p50, p95


def _optional_incremental(raw: Mapping[str, Any], field: str) -> tuple[float | None, float | None, float | None]:
    nested = next(
        (
            raw[key]
            for key in ("incremental", "incremental_latency", "incremental_stage")
            if isinstance(raw.get(key), Mapping)
        ),
        None,
    )
    if isinstance(nested, Mapping):
        return _stats_from_mapping(nested, f"{field}/incremental")
    direct = (
        raw.get("incremental_mean_ms"),
        raw.get("incremental_p50_ms"),
        raw.get("incremental_p95_ms"),
    )
    if all(value is None for value in direct):
        return None, None, None
    if any(value is None for value in direct):
        raise ValueError(f"{field} has incomplete incremental latency fields")
    return tuple(
        _number(value, f"{field}/incremental_{name}_ms")
        for value, name in zip(direct, ("mean", "p50", "p95"), strict=True)
    )  # type: ignore[return-value]


def _load_latency(path: Path) -> dict[str, _LatencyView]:
    artifact = load_json(path)
    systems = artifact.get("systems")
    if not isinstance(systems, Mapping):
        raise ValueError("latency artifact must contain a systems mapping")
    result: dict[str, _LatencyView] = {}
    for system_id in SYSTEM_ORDER:
        raw = systems.get(system_id)
        if not isinstance(raw, Mapping):
            raise ValueError(f"latency is missing for {system_id!r}")
        totals: Mapping[str, Any] = raw
        for key in ("total", "retrieval"):
            if not all(name in totals for name in ("mean_ms", "p95_ms")) and isinstance(raw.get(key), Mapping):
                totals = raw[key]
        mean, p50, p95 = _stats_from_mapping(totals, f"latency/{system_id}")
        incremental = _optional_incremental(raw, f"latency/{system_id}")
        method_value = raw.get("method", "measured total retrieval")
        method = str(method_value).strip() or "measured total retrieval"
        result[system_id] = _LatencyView(method, mean, p50, p95, *incremental)
    return result


def _validate_status(path: Path) -> dict[str, Any]:
    status = load_json(path)
    integrity = status.get("evaluation_integrity_status")
    judged = status.get("Judged@30")
    if integrity != "PASS" or judged is None or not math.isclose(float(judged), 1.0):
        raise ReportBlockedError(
            "formal report blocked: evaluation status does not establish Judged@30 = 1.0"
        )
    if status.get("machine_proposed_development_only") is not True:
        raise ValueError("evaluation status must mark the data as machine-proposed development only")
    return status


def _select_cases(
    per_query: Mapping[tuple[str, str], float],
    query_ids: set[str],
    *,
    count: int,
) -> tuple[list[_CaseDelta], list[_CaseDelta]]:
    if isinstance(count, bool) or not isinstance(count, int) or count < 3:
        raise ValueError("case_count must be an integer of at least 3")
    deltas = [
        _CaseDelta(
            query_id,
            per_query[(BASELINE_SYSTEM_ID, query_id)],
            per_query[(FINAL_SYSTEM_ID, query_id)],
        )
        for query_id in sorted(query_ids)
    ]
    successes = sorted(
        (row for row in deltas if row.delta > 0.0),
        key=lambda row: (-row.delta, row.query_id),
    )[:count]
    failures = sorted(
        (row for row in deltas if row.delta < 0.0),
        key=lambda row: (row.delta, row.query_id),
    )[:count]
    if len(successes) < count or len(failures) < count:
        raise ValueError(
            f"need at least {count} positive and {count} negative per-query nDCG deltas; "
            f"found {len(successes)} and {len(failures)}"
        )
    return successes, failures


def _clean(value: Any) -> str:
    if value is None:
        return "—"
    rendered = re.sub(r"\s+", " ", str(value)).strip()
    if not rendered:
        return "—"
    return rendered.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def _fmt_metric(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _fmt_ms(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    header = "| " + " | ".join(_clean(value) for value in headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_clean(value) for value in row) + " |" for row in rows]
    return "\n".join((header, rule, *body))


def _version_label(document: Mapping[str, str | None]) -> str:
    if document.get("version"):
        return str(document["version"])
    minimum = document.get("version_min")
    maximum = document.get("version_max")
    if minimum or maximum:
        return f"{minimum or '…'}–{maximum or '…'}"
    return document.get("version_status") or "unknown"


def _case_document_table(
    ranking: Sequence[RunEntry],
    corpus: Mapping[str, Mapping[str, str | None]],
    judgments: Mapping[str, int],
    *,
    depth: int,
) -> str:
    rows: list[list[str]] = []
    for entry in ranking[:depth]:
        document = corpus.get(entry.chunk_id)
        if document is None:
            raise ValueError(f"run refers to missing corpus chunk: {entry.chunk_id!r}")
        relevance = judgments.get(entry.chunk_id)
        rows.append(
            [
                str(entry.rank),
                entry.chunk_id,
                document.get("title") or "—",
                document.get("dialect") or "unknown",
                _version_label(document),
                "unjudged" if relevance is None else str(relevance),
            ]
        )
    return _table(
        ("Rank", "Chunk", "Title", "Dialect", "Version", "Offline relevance grade"),
        rows,
    )


def _render_cases(
    heading: str,
    cases: Sequence[_CaseDelta],
    queries: Mapping[str, _SafeQueryView],
    runs: Mapping[str, Mapping[str, Sequence[RunEntry]]],
    corpus: Mapping[str, Mapping[str, str | None]],
    qrels: Mapping[str, Mapping[str, int]],
    *,
    document_depth: int,
) -> str:
    sections = [f"## {heading}", ""]
    for index, case in enumerate(cases, start=1):
        query = queries[case.query_id]
        sections.extend(
            (
                f"### {heading.rstrip('s')} {index}: {_clean(case.query_id)}",
                "",
                f"- Selection: final − baseline per-query graded nDCG@10 = {case.delta:+.4f} "
                f"({case.baseline_ndcg:.4f} → {case.final_ndcg:.4f}).",
                f"- Safe problem: {_clean(query.problem)}",
                f"- Dialect / version: {_clean(query.dialect)} / {_clean(query.version)}",
                "- Baseline top documents (relevance is an offline development judgment):",
                "",
                _case_document_table(
                    runs[BASELINE_SYSTEM_ID][case.query_id],
                    corpus,
                    qrels.get(case.query_id, {}),
                    depth=document_depth,
                ),
                "",
                "- Final top documents (relevance is an offline development judgment):",
                "",
                _case_document_table(
                    runs[FINAL_SYSTEM_ID][case.query_id],
                    corpus,
                    qrels.get(case.query_id, {}),
                    depth=document_depth,
                ),
                "",
            )
        )
    return "\n".join(sections).rstrip()


def _render_acceptance(acceptance: Mapping[str, Any], status: Mapping[str, Any]) -> str:
    rows: list[list[str]] = []
    phase_status = acceptance.get("status")
    if not isinstance(phase_status, Mapping):
        raise ValueError("acceptance artifact has no status mapping")
    for phase in ("phase7", "phase8", "phase9", "final"):
        group = acceptance.get(phase)
        if not isinstance(group, Mapping):
            raise ValueError(f"acceptance artifact has no {phase!r} mapping")
        for gate_name, gate in group.items():
            if isinstance(gate, Mapping) and "passed" in gate:
                observed = gate.get("observed")
                required = gate.get("required_minimum")
                rows.append(
                    [
                        phase,
                        gate.get("description", str(gate_name)),
                        _fmt_metric(float(observed)) if observed is not None else "see component rows",
                        _fmt_metric(float(required)) if required is not None else "configured composite gate",
                        "PASS" if gate.get("passed") is True else "FAIL",
                    ]
                )
            elif isinstance(gate, (int, float)) and not isinstance(gate, bool):
                rows.append([phase, str(gate_name), _fmt_metric(float(gate)), "—", "reported"])
    phase_summary = ", ".join(
        f"{phase}={phase_status.get(phase, 'UNKNOWN')}"
        for phase in ("phase7", "phase8", "phase9", "final")
    )
    failed_targets = [
        phase
        for phase in ("phase7", "phase8", "phase9", "final")
        if phase_status.get(phase) != "PASS"
    ]
    return "\n".join(
        (
            "## Design and acceptance conclusions",
            "",
            "- Dialect awareness is a soft metadata ranking signal: matching dialects are "
            "preferred, explicitly incompatible dialects are penalized, unknown metadata is "
            "retained, and cross-dialect evidence is never categorically removed.",
            "- Version awareness applies the conservative order compatible, general without a "
            "known conflict, unknown, then explicitly incompatible. It uses only corpus-owned "
            "version metadata or explicit passage statements and does not invent support ranges.",
            "- The reranker uses only frozen safe-query fields and candidate passages. It blends "
            "corpus-IDF BM25 evidence from problem, SQL, and observed-error fields plus exact "
            "error-code/SQLSTATE/symbol matches into the dialect+version score with deterministic "
            "ties. This promotes passages that directly name the failing construct or observed "
            "error while retaining the compatibility prior, which explains the measured top-10 gain.",
            f"- Evaluation integrity: {status.get('evaluation_integrity_status', 'UNKNOWN')}; "
            f"Judged@30={_fmt_metric(float(status['Judged@30']))}. Qrels are used only in this "
            "offline report and never by online ranking.",
            f"- Acceptance summary: {phase_summary}; retrieval quality="
            f"{acceptance.get('retrieval_quality_status', 'UNKNOWN')}.",
            "- Targets not met: "
            + (
                ", ".join(failed_targets)
                if failed_targets
                else "none; every configured Phase 7/8/9 and final gate passed."
            ),
            "",
            _table(("Scope", "Gate", "Observed", "Required", "Result"), rows),
        )
    )


def _render_report(
    *,
    overall: Mapping[str, Mapping[str, float]],
    slices: Mapping[tuple[str, str, str], Mapping[str, Any]],
    per_query: Mapping[tuple[str, str], float],
    runs: Mapping[str, Mapping[str, Sequence[RunEntry]]],
    queries: Mapping[str, _SafeQueryView],
    corpus: Mapping[str, Mapping[str, str | None]],
    qrels: Mapping[str, Mapping[str, int]],
    latency: Mapping[str, _LatencyView],
    acceptance: Mapping[str, Any],
    status: Mapping[str, Any],
    case_count: int,
    case_document_depth: int,
) -> str:
    metric_headers = tuple(QUALITY_METRICS)
    overall_rows = [
        [system_id, *(_fmt_metric(overall[system_id][metric]) for metric in metric_headers)]
        for system_id in SYSTEM_ORDER
    ]

    sensitive_rows: list[list[str]] = []
    for slice_value in ("dialect-sensitive", "version-sensitive"):
        for system_id in SYSTEM_ORDER:
            row = slices[(system_id, "case_flag", slice_value)]
            sensitive_rows.append(
                [
                    slice_value,
                    system_id,
                    str(row["query_count"]),
                    *(_fmt_metric(float(row[metric])) for metric in metric_headers),
                ]
            )

    dialect_rows: list[list[str]] = []
    for dialect in DIALECTS:
        for system_id in SYSTEM_ORDER:
            row = slices[(system_id, "dialect", dialect)]
            dialect_rows.append(
                [
                    dialect,
                    system_id,
                    str(row["query_count"]),
                    *(_fmt_metric(float(row[metric])) for metric in metric_headers),
                ]
            )

    latency_rows = [
        [
            system_id,
            latency[system_id].method,
            _fmt_ms(latency[system_id].mean_ms),
            _fmt_ms(latency[system_id].p50_ms),
            _fmt_ms(latency[system_id].p95_ms),
            _fmt_ms(latency[system_id].incremental_mean_ms),
            _fmt_ms(latency[system_id].incremental_p50_ms),
            _fmt_ms(latency[system_id].incremental_p95_ms),
        ]
        for system_id in SYSTEM_ORDER
    ]
    final_latency = latency[FINAL_SYSTEM_ID]
    combined_latency = latency[COMBINED_SYSTEM_ID]
    overhead = (
        final_latency.mean_ms - combined_latency.mean_ms,
        final_latency.p50_ms - combined_latency.p50_ms,
        final_latency.p95_ms - combined_latency.p95_ms,
    )
    baseline_ds = slices[(BASELINE_SYSTEM_ID, "case_flag", "dialect-sensitive")]
    dialect_ds = slices[(DIALECT_SYSTEM_ID, "case_flag", "dialect-sensitive")]
    final_ds = slices[(FINAL_SYSTEM_ID, "case_flag", "dialect-sensitive")]
    baseline_vs = slices[(BASELINE_SYSTEM_ID, "case_flag", "version-sensitive")]
    version_vs = slices[(VERSION_SYSTEM_ID, "case_flag", "version-sensitive")]
    final_vs = slices[(FINAL_SYSTEM_ID, "case_flag", "version-sensitive")]
    dialect_denominator = int(baseline_ds["query_count"]) * 5
    version_denominator = int(baseline_vs["query_count"]) * 5

    def event_count(row: Mapping[str, Any], metric: str, denominator: int) -> int:
        return round(float(row[metric]) * denominator)

    successes, failures = _select_cases(per_query, set(queries), count=case_count)
    sections = [
        "# SQLMend-RAG Retrieval v1 development report",
        "",
        f"Schema: `{REPORT_SCHEMA_VERSION}`. All results below are {EVALUATION_LABEL} on "
        "the current 250-query development set. They are not human gold and are not a final "
        "held-out test result.",
        "",
        "Qrels are joined only for offline evaluation, judged-pool validation, and the displayed "
        "offline relevance grades. Online retrieval and reranking do not receive qrels or any "
        "reference fix, expected root cause, annotation evidence, or held-out label.",
        "",
        "## Overall five-system comparison",
        "",
        _table(("System ID", *metric_headers), overall_rows),
        "",
        "## Dialect-sensitive and version-sensitive slices",
        "",
        _table(("Slice", "System ID", "Queries", *metric_headers), sensitive_rows),
        "",
        "Compatibility event counts use the fixed Top-5 denominator. On the dialect-sensitive "
        f"slice, wrong-dialect results move from {event_count(baseline_ds, 'Wrong-Dialect@5', dialect_denominator)}/{dialect_denominator} "
        f"(baseline) to {event_count(dialect_ds, 'Wrong-Dialect@5', dialect_denominator)}/{dialect_denominator} "
        f"(Phase 7) and {event_count(final_ds, 'Wrong-Dialect@5', dialect_denominator)}/{dialect_denominator} (final). "
        "On the version-sensitive slice, explicitly wrong-version results move from "
        f"{event_count(baseline_vs, 'Wrong-Version@5', version_denominator)}/{version_denominator} to "
        f"{event_count(version_vs, 'Wrong-Version@5', version_denominator)}/{version_denominator} (version-only) and "
        f"{event_count(final_vs, 'Wrong-Version@5', version_denominator)}/{version_denominator} (final); unknown version metadata is reported separately and never counted as incompatible.",
        "",
        "## Per-dialect slices",
        "",
        _table(("Dialect", "System ID", "Queries", *metric_headers), dialect_rows),
        "",
        "## Retrieval latency",
        "",
        _table(
            (
                "System ID",
                "Method",
                "Mean ms",
                "P50 ms",
                "P95 ms",
                "Recorded incremental mean ms",
                "Recorded incremental P50 ms",
                "Recorded incremental P95 ms",
            ),
            latency_rows,
        ),
        "",
        "Frozen Hybrid is a measured end-to-end reference. New-system totals are explicitly "
        "componentwise estimates (frozen measured total plus separately measured increments); "
        "the reranker-only mean/P50/P95 increment is directly measured on all 250 queries.",
        "",
        "Reranking overhead versus dialect+version-aware retrieval, computed from total latency: "
        f"mean {_fmt_ms(overhead[0], signed=True)} ms, P50 {_fmt_ms(overhead[1], signed=True)} ms, "
        f"P95 {_fmt_ms(overhead[2], signed=True)} ms.",
        "",
        _render_cases(
            "Success cases",
            successes,
            queries,
            runs,
            corpus,
            qrels,
            document_depth=case_document_depth,
        ),
        "",
        _render_cases(
            "Failure cases",
            failures,
            queries,
            runs,
            corpus,
            qrels,
            document_depth=case_document_depth,
        ),
        "",
        _render_acceptance(acceptance, status),
        "",
    ]
    return "\n".join(sections)


def generate_retrieval_v1_report(
    sources: ReportSources,
    *,
    output_path: Path | None = None,
    case_count: int = 3,
    case_document_depth: int = 3,
    judged_depth: int = 30,
) -> str:
    """Build and optionally atomically write the retrieval-v1 Markdown report.

    The function refuses to render publishable metrics unless the supplied
    evaluation status and an independent qrel/run audit both establish full
    judgment coverage through ``judged_depth``.  No file is written on failure.
    """

    if isinstance(judged_depth, bool) or not isinstance(judged_depth, int) or judged_depth <= 0:
        raise ValueError("judged_depth must be a positive integer")
    if (
        isinstance(case_document_depth, bool)
        or not isinstance(case_document_depth, int)
        or case_document_depth <= 0
        or case_document_depth > judged_depth
    ):
        raise ValueError("case_document_depth must be between 1 and judged_depth")

    status = _validate_status(Path(sources.evaluation_status))
    queries = _load_safe_queries(Path(sources.serialized_queries))
    query_ids = set(queries)
    runs = _load_runs(sources.runs)
    qrels = read_qrels(Path(sources.qrels))
    _verify_judged_pool(runs, qrels, query_ids, depth=judged_depth)

    overall = _load_overall(Path(sources.overall_metrics))
    if any(
        not math.isclose(overall[system_id]["Judged@30"], 1.0)
        for system_id in SYSTEM_ORDER
    ):
        raise ReportBlockedError("formal report blocked: an overall system Judged@30 is below 1.0")
    slices = _load_slices(Path(sources.slice_metrics))
    per_query = _load_per_query(Path(sources.per_query_metrics), query_ids)
    corpus = _load_corpus(Path(sources.corpus))
    referenced_chunks = {
        entry.chunk_id
        for system_runs in runs.values()
        for ranking in system_runs.values()
        for entry in ranking
    }
    missing_chunks = sorted(referenced_chunks - set(corpus))
    if missing_chunks:
        raise ValueError(f"runs reference chunks absent from corpus: {missing_chunks[:3]!r}")
    latency = _load_latency(Path(sources.latency))
    acceptance = load_json(Path(sources.acceptance))

    report = _render_report(
        overall=overall,
        slices=slices,
        per_query=per_query,
        runs=runs,
        queries=queries,
        corpus=corpus,
        qrels=qrels,
        latency=latency,
        acceptance=acceptance,
        status=status,
        case_count=case_count,
        case_document_depth=case_document_depth,
    )
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(report, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    return report


__all__ = [
    "BASELINE_SYSTEM_ID",
    "DIALECT_SYSTEM_ID",
    "FINAL_SYSTEM_ID",
    "QUALITY_METRICS",
    "REPORT_SCHEMA_VERSION",
    "ReportBlockedError",
    "ReportSources",
    "SYSTEM_ORDER",
    "VERSION_SYSTEM_ID",
    "generate_retrieval_v1_report",
]
