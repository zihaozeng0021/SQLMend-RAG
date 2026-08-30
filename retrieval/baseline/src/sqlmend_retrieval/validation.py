"""Final, machine-readable validation for a SQLMend retrieval release.

The validator is deliberately independent of model execution.  It validates
the frozen inputs and the artifacts already written by the pipeline, and it
never fills a missing judgment with relevance zero.  Every individual check
uses the same small six-field contract so that CI and the completion report can
consume the result without interpreting prose.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .corpus import (
    ALLOWED_DIALECTS,
    EXPECTED_CORPUS_RECORDS,
    EXPECTED_CORPUS_SHA256,
    validate_corpus,
)
from .hashing import (
    canonical_json_sha256,
    sha256_file,
    sha256_text,
    sha256_tree,
    snapshot_protected_paths,
    snapshot_release_source,
)
from .metrics import EVALUATION_LABEL, PRIMARY_BOOTSTRAP_METRICS, REQUIRED_METRIC_NAMES
from .paths import ProjectPaths
from .qrels import QrelEntry, load_qrels_jsonl, read_trec_qrels
from .queries import (
    ALLOWED_SOURCE_FIELDS,
    SERIALIZER_VERSION,
    load_queries,
    query_statistics,
    serialize_queries,
)
from .reporting import load_yaml, read_json, write_json
from .trec import TrecRunEntry, read_trec_run


PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
CHECK_STATUSES = frozenset({PASS, FAIL, BLOCKED})
CHECK_FIELDS = (
    "check_id",
    "status",
    "observed_value",
    "required_value",
    "explanation",
    "recommended_remediation",
)

EXPECTED_QUERY_COUNT = 250
EXPECTED_QUERY_DIALECT_COUNTS = {dialect: 50 for dialect in sorted(ALLOWED_DIALECTS)}
EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT = 174
EXPECTED_VERSION_SENSITIVE_QUERY_COUNT = 53
EXPECTED_CORPUS_DIALECT_COUNTS = {
    dialect: EXPECTED_CORPUS_RECORDS // len(ALLOWED_DIALECTS)
    for dialect in sorted(ALLOWED_DIALECTS)
}
EXPECTED_QREL_COUNT = 23_452
EXPECTED_QREL_LABEL_COUNTS = {0: 20_154, 1: 2_839, 2: 459}
EXPECTED_RESULTS_PER_QUERY = 30
EXPECTED_CONFIG_HASHES = {
    "bm25_baseline.yaml": "0588102a6b7fd1366f1cdf43a93c2a64962b872500e86d48c256fac8e063f04f",
    "dense_baseline.yaml": "7dcf4eb21a50607a6a8ef7b23a5125646fde08d17e8cf83ffa9d80602c419a68",
    "evaluation.yaml": "5f7afd8afca53b3a0f9e82bff5b6ea516487fca8ed80a4d367e29a18416d9c3e",
    "hybrid_rrf_baseline.yaml": "7ae8ab8daddb5dd047f0a5adea8649e5ce72a29800622a442659e8faad51d972",
    "query_serializer.yaml": "1cae7043486f46fd5992740f9cebf69b6e2ce9c16ec65e016a6e219b184717e4",
}

RUN_SPECS = (
    ("bm25", "bm25_run", "bm25_run_sha256", "bm25_formal_v1"),
    ("dense", "dense_run", "dense_run_sha256", "dense_formal_v1"),
    ("hybrid", "hybrid_run", "hybrid_run_sha256", "hybrid_rrf_formal_v1"),
)

# The manifest and validation report are intentionally absent here.  The
# orchestration layer may generate a preliminary manifest before validation
# and then regenerate it with the final statuses afterwards.
REQUIRED_ENGINEERING_FILES = (
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "config/bm25_baseline.yaml",
    "config/dense_baseline.yaml",
    "config/hybrid_rrf_baseline.yaml",
    "config/evaluation.yaml",
    "config/query_serializer.yaml",
    "serialized_queries/dev_250_queries.jsonl",
    "runs/bm25_formal_dev250.trec",
    "runs/dense_formal_dev250.trec",
    "runs/hybrid_rrf_formal_dev250.trec",
    "runs/hybrid_rrf_formal_dev250.provenance.jsonl",
    "qrels/qrels_machine_proposed_dev250.trec",
    "qrels/qrels_effective_dev250.trec",
    "pool_expansion/pool_expansion_required.jsonl",
    "pool_expansion/pool_expansion_summary.json",
    "evaluation/judged_coverage.json",
    "evaluation/latency.json",
    "reproduction/reproduction_report.json",
    "reports/baseline_report.md",
    "reports/failure_analysis.md",
    "reports/provenance_audit.md",
    "reports/completion_report.md",
    "reports/protected_paths_report.json",
    "reports/input_validation.json",
    "reports/effective_qrels.json",
    "evaluation/run_determinism.json",
    "reports/test_results.json",
    "manifest.json",
)

# These outputs must not be fabricated while judgment coverage is blocked.
# Once Judged@30 is complete, however, they are required release artifacts.
REQUIRED_COMPLETE_EVALUATION_FILES = (
    "evaluation/overall_metrics.json",
    "evaluation/per_query_metrics.csv",
    "evaluation/slice_metrics.csv",
    "evaluation/confidence_intervals.json",
    "evaluation/pairwise_differences.json",
    "evaluation/complementarity_report.json",
)

REQUIRED_REPORT_FILES = (
    "reports/baseline_report.md",
    "reports/failure_analysis.md",
    "reports/provenance_audit.md",
    "reports/completion_report.md",
)
REQUIRED_REPORT_MARKERS = {
    "reports/baseline_report.md": (
        "## 最终状态",
        "## 冻结输入身份",
        "## 正式配置",
        "## Run 与 index 身份",
        "## Pool completeness",
        "## Quality targets",
        "## Latency、throughput、build time 与 index size",
        "## 限制与后续工作",
    ),
    "reports/failure_analysis.md": (
        "## 类别覆盖",
        "BM25 成功而 dense 失败",
        "dense 成功而 BM25 失败",
        "hybrid 改善排名",
        "hybrid 损害排名",
        "dialect-sensitive",
        "version-sensitive",
        "SQL token",
        "语义匹配",
        "chunk 粒度",
        "pool expansion",
        "## 案例证据目录",
        "## Pool expansion 交接",
    ),
    "reports/provenance_audit.md": (
        "## 识别与审计方法",
        "## 可获得设置与独立复现结果",
        "## 缺失信息与限制",
        "## 与正式 baselines 的隔离",
        "## 现有 pool 之外的正式结果",
    ),
    "reports/completion_report.md": (
        "## 创建的精确文件",
        "## 执行的精确命令",
        "## Corpus、query 与 qrel 验证",
        "## 受保护目录前后验证",
        "## Annotation-reproduction 状态",
        "## 正式 BM25、dense 与 hybrid 配置",
        "## Run 与 index hashes",
        "## Metric summary、slice summary、CI、pairwise 与 complementarity",
        "## Performance summary",
        "## Pool-expansion 状态",
        "## Test evidence",
        "## 所有未通过检查",
        "## 所有限制与下一推荐阶段",
        "## 最终 status object",
    ),
}
POOLED_RECALL_LABEL = "pooled Recall"


def _record(
    check_id: str,
    status: str,
    observed_value: Any,
    required_value: Any,
    explanation: str,
    recommended_remediation: str,
) -> dict[str, Any]:
    """Construct one exact validation-check record."""

    if status not in CHECK_STATUSES:
        raise ValueError(f"Unsupported validation status: {status!r}")
    record = {
        "check_id": check_id,
        "status": status,
        "observed_value": observed_value,
        "required_value": required_value,
        "explanation": explanation,
        "recommended_remediation": recommended_remediation,
    }
    assert tuple(record) == CHECK_FIELDS
    return record


def _error(exc: BaseException) -> dict[str, str]:
    return {"error_type": type(exc).__name__, "message": str(exc)}


def _load_serialized_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank serialized-query line {line_number}")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"malformed serialized-query JSON at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(f"serialized-query line {line_number} is not an object")
            records.append(record)
    return records


def _serialized_violations(
    source_queries: list[dict[str, Any]],
    serialized_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    expected = {item.query_id: item.to_dict() for item in serialize_queries(source_queries)}
    observed: dict[str, dict[str, Any]] = {}
    violations: list[str] = []
    required_fields = {
        "query_id",
        "source_fields_used",
        "serialized_text",
        "serialized_text_sha256",
        "serializer_version",
    }

    for position, record in enumerate(serialized_records, start=1):
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            violations.append(f"record {position}: missing non-empty query_id")
            continue
        if query_id in observed:
            violations.append(f"record {position}: duplicate query_id {query_id}")
            continue
        observed[query_id] = record

        fields = set(record)
        if fields != required_fields:
            violations.append(
                f"{query_id}: audit fields differ; missing={sorted(required_fields - fields)}, "
                f"extra={sorted(fields - required_fields)}"
            )
        source_fields = record.get("source_fields_used")
        if not isinstance(source_fields, list) or any(
            not isinstance(field, str) for field in source_fields
        ):
            violations.append(f"{query_id}: source_fields_used must be a string list")
        elif (
            len(source_fields) != len(set(source_fields))
            or not set(source_fields).issubset(ALLOWED_SOURCE_FIELDS)
        ):
            violations.append(f"{query_id}: source_fields_used violates the whitelist")

        text = record.get("serialized_text")
        digest = record.get("serialized_text_sha256")
        if not isinstance(text, str) or not text.strip():
            violations.append(f"{query_id}: serialized_text is empty or non-string")
        elif digest != sha256_text(text):
            violations.append(f"{query_id}: serialized_text_sha256 does not match text")
        if record.get("serializer_version") != SERIALIZER_VERSION:
            violations.append(f"{query_id}: serializer_version is not {SERIALIZER_VERSION}")

    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if missing:
        violations.append(f"missing serialized query IDs: {missing[:10]}")
    if extra:
        violations.append(f"unexpected serialized query IDs: {extra[:10]}")
    for query_id in sorted(set(expected) & set(observed)):
        if observed[query_id] != expected[query_id]:
            violations.append(
                f"{query_id}: serialized audit record differs from the whitelist serializer"
            )

    summary = {
        "record_count": len(serialized_records),
        "unique_query_ids": len(observed),
        "expected_query_ids_matched": not missing and not extra,
        "violation_count": len(violations),
        "sample_violations": violations[:20],
    }
    return summary, violations


def _run_summary(entries: Iterable[TrecRunEntry], path: Path) -> dict[str, Any]:
    rows = list(entries)
    counts = Counter(entry.query_id for entry in rows)
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "query_count": len(counts),
        "result_count": len(rows),
        "minimum_results_per_query": min(counts.values(), default=0),
        "maximum_results_per_query": max(counts.values(), default=0),
        "all_scores_finite": all(math.isfinite(entry.score) for entry in rows),
    }


def _manifest_hash_values(value: Any) -> list[str]:
    """Flatten supported single- and repeated-run hash metadata."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, Mapping):
        values: list[str] = []
        for key in sorted(value):
            item = value[key]
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, (list, tuple)):
                values.extend(part for part in item if isinstance(part, str))
        return values
    return []


def _recorded_run_hashes(manifest: Mapping[str, Any], system: str, direct_key: str) -> list[str]:
    values = _manifest_hash_values(manifest.get(direct_key))
    for container_key in ("run_hashes", "deterministic_run_hashes", "repeated_run_hashes"):
        container = manifest.get(container_key)
        if not isinstance(container, Mapping):
            continue
        for key in (system, f"{system}_run", direct_key):
            if key in container:
                values.extend(_manifest_hash_values(container[key]))
    return values


def _canonical_system(name: str) -> str | None:
    normalized = name.casefold().replace("-", "_")
    if "hybrid" in normalized or "rrf" in normalized:
        return "hybrid"
    if "bm25" in normalized:
        return "bm25"
    if "dense" in normalized or "e5" in normalized:
        return "dense"
    return None


def _judged_at_30_values(payload: Any) -> dict[str, float]:
    if not isinstance(payload, Mapping):
        return {}
    candidates: list[Mapping[str, Any]] = []
    for key in ("per_system", "systems", "retrievers"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)
    candidates.append(payload)

    result: dict[str, float] = {}
    metric_keys = ("Judged@30", "judged@30", "judged_at_30", "judged_coverage_at_30")
    for container in candidates:
        for raw_name, raw_metrics in container.items():
            if not isinstance(raw_name, str) or not isinstance(raw_metrics, Mapping):
                continue
            system = _canonical_system(raw_name)
            if system is None:
                continue
            metrics = raw_metrics.get("overall", raw_metrics)
            if not isinstance(metrics, Mapping):
                continue
            for key in metric_keys:
                value = metrics.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    result[system] = float(value)
                    break
    return result


def _direct_judgment_coverage(
    runs: Mapping[str, list[TrecRunEntry]],
    qrels: Iterable[QrelEntry],
) -> tuple[dict[str, float], dict[str, int]]:
    judged = {(qrel.query_id, qrel.chunk_id) for qrel in qrels}
    coverage: dict[str, float] = {}
    unjudged: dict[str, int] = {}
    for system, entries in runs.items():
        top = [entry for entry in entries if entry.rank <= EXPECTED_RESULTS_PER_QUERY]
        missing = sum((entry.query_id, entry.chunk_id) not in judged for entry in top)
        unjudged[system] = missing
        coverage[system] = (len(top) - missing) / len(top) if top else 0.0
    return coverage, unjudged


def _validate_latency_payload(payload: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate benchmark evidence rather than accepting an empty JSON object."""

    violations: list[str] = []
    if not isinstance(payload, Mapping):
        return {"payload_type": type(payload).__name__}, ["latency payload is not an object"]

    def finite_number(value: Any, *, positive: bool = False) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and (float(value) > 0.0 if positive else float(value) >= 0.0)
        )

    query_count = payload.get("query_count")
    repetitions = payload.get("repetitions")
    warmups = payload.get("warmup_queries")
    if query_count != EXPECTED_QUERY_COUNT:
        violations.append(f"query_count is {query_count!r}, expected {EXPECTED_QUERY_COUNT}")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        violations.append("repetitions must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 3:
        violations.append("warmup_queries must be an integer of at least 3")
    if payload.get("evaluation_label") != EVALUATION_LABEL:
        violations.append("latency payload lacks the exact evaluation label")
    if payload.get("machine_proposed_development_only") is not True:
        violations.append("latency payload must identify development-only data")
    if not isinstance(payload.get("cold_start_scope"), str) or not payload["cold_start_scope"].strip():
        violations.append("cold_start_scope must describe what the timer includes")

    expected_samples = (
        query_count * repetitions
        if isinstance(query_count, int)
        and not isinstance(query_count, bool)
        and isinstance(repetitions, int)
        and not isinstance(repetitions, bool)
        else None
    )
    required_stats = (
        "maximum_ms",
        "mean_ms",
        "median_ms",
        "p50_ms",
        "p95_ms",
        "queries_per_second",
    )

    def check_summary(path: str, value: Any) -> None:
        if not isinstance(value, Mapping):
            violations.append(f"{path} is not a latency-summary object")
            return
        for field in required_stats:
            positive = field == "queries_per_second"
            if not finite_number(value.get(field), positive=positive):
                violations.append(f"{path}.{field} is not finite and {'positive' if positive else 'non-negative'}")
        if value.get("sample_count") != expected_samples:
            violations.append(
                f"{path}.sample_count is {value.get('sample_count')!r}, expected {expected_samples!r}"
            )
        if finite_number(value.get("median_ms")) and finite_number(value.get("p50_ms")):
            if not math.isclose(
                float(value["median_ms"]), float(value["p50_ms"]), rel_tol=0.0, abs_tol=1e-12
            ):
                violations.append(f"{path}.median_ms and p50_ms differ")

    warm = payload.get("warm_query_latency")
    if not isinstance(warm, Mapping):
        violations.append("warm_query_latency is not an object")
    else:
        check_summary("warm_query_latency.bm25", warm.get("bm25"))
        dense = warm.get("dense")
        if not isinstance(dense, Mapping):
            violations.append("warm_query_latency.dense is not an object")
        else:
            for component in ("query_encoding", "vector_search", "total"):
                check_summary(f"warm_query_latency.dense.{component}", dense.get(component))
        hybrid = warm.get("hybrid")
        if not isinstance(hybrid, Mapping):
            violations.append("warm_query_latency.hybrid is not an object")
        else:
            for component in ("bm25_component", "dense_component", "rrf_fusion", "total"):
                check_summary(f"warm_query_latency.hybrid.{component}", hybrid.get(component))

    cold = payload.get("cold_start")
    if not isinstance(cold, Mapping):
        violations.append("cold_start is not an object")
    else:
        for field in ("bm25_seconds", "dense_seconds"):
            if not finite_number(cold.get(field)):
                violations.append(f"cold_start.{field} is not finite and non-negative")

    build = payload.get("build_performance")
    build_fields = (
        "bm25_index_build_seconds",
        "bm25_index_size_bytes",
        "dense_corpus_encoding_seconds",
        "dense_embedding_index_size_bytes",
        "dense_index_build_seconds",
        "dense_model_cache_size_bytes",
        "dense_model_load_or_download_seconds",
    )
    if not isinstance(build, Mapping):
        violations.append("build_performance is not an object")
    else:
        for field in build_fields:
            if not finite_number(build.get(field)):
                violations.append(f"build_performance.{field} is not finite and non-negative")

    environment = payload.get("environment")
    environment_fields = (
        "clock",
        "corpus_chunks",
        "cpu",
        "device_used_for_official_run",
        "embedding_dimension",
        "logical_cpu_count",
        "operating_system",
        "package_versions",
        "physical_cpu_count",
        "python_version",
        "ram_bytes",
    )
    if not isinstance(environment, Mapping):
        violations.append("environment is not an object")
    else:
        for field in environment_fields:
            if field not in environment or environment.get(field) in (None, "", {}):
                violations.append(f"environment.{field} is missing or empty")
        if environment.get("corpus_chunks") != EXPECTED_CORPUS_RECORDS:
            violations.append("environment.corpus_chunks does not match the frozen corpus")

    return {
        "query_count": query_count,
        "warmup_queries": warmups,
        "repetitions": repetitions,
        "expected_sample_count": expected_samples,
        "violation_count": len(violations),
        "violations": violations[:50],
    }, violations


def _system_metrics(payload: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    containers: list[Mapping[str, Any]] = []
    for key in ("systems", "retrievers", "results"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    containers.append(payload)
    result: dict[str, Mapping[str, Any]] = {}
    for container in containers:
        for raw_name, raw_value in container.items():
            if not isinstance(raw_name, str) or not isinstance(raw_value, Mapping):
                continue
            system = _canonical_system(raw_name)
            if system is None:
                continue
            metrics = raw_value.get("overall", raw_value.get("metrics", raw_value))
            if isinstance(metrics, Mapping) and any(
                name in metrics for name in REQUIRED_METRIC_NAMES
            ):
                result[system] = metrics
    return result


def _dialect_regressions(path: Path, failure_analysis: str) -> list[str]:
    """Return >.05 hybrid regressions lacking case-level evidence.

    Missing or malformed slice evidence is a validation error, never an
    implicit claim that no regression exists.
    """

    if not path.is_file():
        raise ValueError("slice metrics file is missing")
    by_dialect: dict[str, dict[str, float]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("slice_name") != "dialect":
                continue
            system = _canonical_system(row.get("retriever", ""))
            dialect = row.get("slice_value")
            if system is None or dialect not in EXPECTED_QUERY_DIALECT_COUNTS:
                raise ValueError("dialect slice has an unknown system or dialect")
            try:
                value = float(row["graded_nDCG@10"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("dialect slice has a malformed graded_nDCG@10") from exc
            if not math.isfinite(value):
                raise ValueError("dialect slice has a non-finite graded_nDCG@10")
            if system in by_dialect[dialect]:
                raise ValueError(f"duplicate dialect/system slice: {dialect}/{system}")
            by_dialect[dialect][system] = value

    expected_dialects = set(EXPECTED_QUERY_DIALECT_COUNTS)
    if set(by_dialect) != expected_dialects or any(
        set(values) != {"bm25", "dense", "hybrid"} for values in by_dialect.values()
    ):
        raise ValueError("dialect slices must cover every dialect and all three systems")

    unexplained: list[str] = []
    analysis = failure_analysis.casefold()
    for dialect, values in sorted(by_dialect.items()):
        regression = max(values["bm25"], values["dense"]) - values["hybrid"]
        if regression > 0.05:
            dialect_position = analysis.find(dialect.casefold())
            evidence_window = (
                analysis[max(0, dialect_position - 800) : dialect_position + 2400]
                if dialect_position >= 0
                else ""
            )
            has_query = bool(re.search(r"\b(?:dev\d{4}|q\d+)\b", evidence_window))
            has_passage = "passage" in evidence_window
            has_component_ranks = "component rank" in evidence_window or "组件排名" in evidence_window
            has_follow_up = "follow-up" in evidence_window or "后续" in evidence_window
            if not (has_query and has_passage and has_component_ranks and has_follow_up):
                unexplained.append(dialect)
    return unexplained


def validate_release(paths: ProjectPaths) -> dict[str, Any]:
    """Validate an already-produced release and write ``validation_report.json``.

    A malformed or missing artifact becomes a check failure rather than an
    uncaught exception.  The only expected exception from this function is an
    inability to write the final report itself.
    """

    checks: list[dict[str, Any]] = []
    engineering_checks: list[dict[str, Any]] = []
    evaluation_checks: list[dict[str, Any]] = []

    def add_engineering(record: dict[str, Any]) -> None:
        checks.append(record)
        engineering_checks.append(record)

    def add_evaluation(record: dict[str, Any]) -> None:
        checks.append(record)
        evaluation_checks.append(record)

    # The baseline was frozen before inspecting formal metrics.  Exact config
    # bytes are therefore part of the acceptance boundary, not merely advice.
    observed_config_hashes: dict[str, Any] = {}
    config_hashes_ok = True
    for name, expected_hash in EXPECTED_CONFIG_HASHES.items():
        try:
            observed = sha256_file(paths.config / name)
        except OSError as exc:
            observed = _error(exc)
        observed_config_hashes[name] = observed
        config_hashes_ok = config_hashes_ok and observed == expected_hash
    add_engineering(
        _record(
            "engineering.configs.frozen_hashes",
            PASS if config_hashes_ok else FAIL,
            observed_config_hashes,
            EXPECTED_CONFIG_HASHES,
            "All formal baseline, serializer, and evaluation configs match the frozen bytes."
            if config_hashes_ok
            else "One or more fixed baseline configs changed after the freeze.",
            "Restore the pinned config bytes; do not tune on the 250-query development snapshot."
            if not config_hashes_ok
            else "No remediation required.",
        )
    )

    try:
        test_result = read_json(paths.reports / "test_results.json", None)
        if not isinstance(test_result, Mapping):
            raise ValueError("test result must be a JSON object")
        current_source = snapshot_release_source(paths)
        tests_ok = (
            test_result.get("status") == PASS
            and test_result.get("returncode") == 0
            and test_result.get("source_stable_during_tests") is True
            and test_result.get("source_tree_sha256") == current_source["tree_sha256"]
            and test_result.get("source_tree_sha256_after") == current_source["tree_sha256"]
            and test_result.get("source_file_count") == current_source["file_count"]
        )
        add_engineering(
            _record(
                "engineering.tests.current_source",
                PASS if tests_ok else FAIL,
                {
                    "reported_status": test_result.get("status"),
                    "returncode": test_result.get("returncode"),
                    "tested_source_tree_sha256": test_result.get("source_tree_sha256"),
                    "tested_source_tree_sha256_after": test_result.get(
                        "source_tree_sha256_after"
                    ),
                    "source_stable_during_tests": test_result.get(
                        "source_stable_during_tests"
                    ),
                    "current_source_tree_sha256": current_source["tree_sha256"],
                },
                {
                    "reported_status": PASS,
                    "returncode": 0,
                    "tested_source_equals_current_source": True,
                },
                "The full automated test suite passed against the current source/config/test bytes."
                if tests_ok
                else "Test evidence is missing, failed, or belongs to different source bytes.",
                "Run `python -m sqlmend_retrieval.cli test` after the final source edit."
                if not tests_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.tests.current_source",
                FAIL,
                _error(exc),
                {"reported_status": PASS, "returncode": 0, "tested_source_equals_current_source": True},
                "Current-source test evidence could not be validated.",
                "Run `python -m sqlmend_retrieval.cli test` after the final source edit.",
            )
        )

    # Frozen corpus identity and schema.
    corpus_records: list[dict[str, Any]] | None = None
    corpus_summary: dict[str, Any] | None = None
    try:
        corpus_hash = sha256_file(paths.corpus)
        add_engineering(
            _record(
                "engineering.corpus.sha256",
                PASS if corpus_hash == EXPECTED_CORPUS_SHA256 else FAIL,
                corpus_hash,
                EXPECTED_CORPUS_SHA256,
                "The corpus byte hash matches the frozen production snapshot."
                if corpus_hash == EXPECTED_CORPUS_SHA256
                else "The corpus bytes differ from the frozen production snapshot.",
                "Restore construction/data/processed/corpus.jsonl from the frozen snapshot."
                if corpus_hash != EXPECTED_CORPUS_SHA256
                else "No remediation required.",
            )
        )
    except (OSError, ValueError) as exc:
        add_engineering(
            _record(
                "engineering.corpus.sha256",
                FAIL,
                _error(exc),
                EXPECTED_CORPUS_SHA256,
                "The frozen corpus hash could not be computed.",
                "Restore the required corpus file and rerun validation.",
            )
        )

    try:
        corpus = validate_corpus(paths.corpus, expected_sha256="", expected_records=0)
        corpus_records = corpus["records"]
        corpus_summary = {key: value for key, value in corpus.items() if key != "records"}
        count_ok = corpus["record_count"] == EXPECTED_CORPUS_RECORDS
        add_engineering(
            _record(
                "engineering.corpus.record_count",
                PASS if count_ok else FAIL,
                corpus["record_count"],
                EXPECTED_CORPUS_RECORDS,
                "The corpus contains the required number of valid, uniquely identified chunks."
                if count_ok
                else "The valid corpus chunk count differs from the frozen requirement.",
                "Restore or rebuild the frozen corpus; do not patch it inside retrieval."
                if not count_ok
                else "No remediation required.",
            )
        )
        dialect_counts = corpus["dialect_counts"]
        dialect_ok = dialect_counts == EXPECTED_CORPUS_DIALECT_COUNTS
        add_engineering(
            _record(
                "engineering.corpus.dialects",
                PASS if dialect_ok else FAIL,
                dialect_counts,
                EXPECTED_CORPUS_DIALECT_COUNTS,
                "All five allowed dialects have the required frozen distribution."
                if dialect_ok
                else "The corpus dialect set or distribution is not the required frozen distribution.",
                "Restore the frozen corpus and rerun the corpus construction validator."
                if not dialect_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        failure = _error(exc)
        add_engineering(
            _record(
                "engineering.corpus.record_count",
                FAIL,
                failure,
                EXPECTED_CORPUS_RECORDS,
                "The corpus could not be parsed as a valid unique-chunk JSONL snapshot.",
                "Restore a parseable corpus with unique chunk IDs and non-empty text.",
            )
        )
        add_engineering(
            _record(
                "engineering.corpus.dialects",
                FAIL,
                failure,
                EXPECTED_CORPUS_DIALECT_COUNTS,
                "The corpus dialect distribution could not be validated.",
                "Fix the corpus validation error using the protected construction workflow.",
            )
        )

    # Loading an index validates its payload hashes; the explicit binding
    # check then proves that rows, rendered passages, and configuration still
    # correspond to the current frozen corpus rather than another build.
    try:
        if corpus_records is None:
            raise ValueError("validated corpus records are required for index binding")
        from .bm25 import load_bm25_index, verify_bm25_index_binding
        from .dense import load_dense_index, verify_dense_index_binding

        bm25_config = load_yaml(paths.config / "bm25_baseline.yaml")
        dense_config = load_yaml(paths.config / "dense_baseline.yaml")
        bm25_index = load_bm25_index(paths.bm25_index)
        dense_index = load_dense_index(paths.dense_index)
        verify_bm25_index_binding(bm25_index, corpus_records, bm25_config)
        verify_dense_index_binding(dense_index, corpus_records, dense_config)
        index_observed = {
            "bm25_document_count": len(bm25_index.chunk_ids),
            "bm25_payload_sha256": bm25_index.metadata.get("payload_sha256"),
            "dense_document_count": len(dense_index.chunk_ids),
            "dense_embeddings_sha256": dense_index.metadata.get("embeddings_sha256"),
            "dense_chunk_ids_sha256": dense_index.metadata.get("chunk_ids_sha256"),
            "dense_shape": list(dense_index.embeddings.shape),
        }
        add_engineering(
            _record(
                "engineering.indices.current_corpus_config_binding",
                PASS,
                index_observed,
                {
                    "bm25_document_count": EXPECTED_CORPUS_RECORDS,
                    "dense_document_count": EXPECTED_CORPUS_RECORDS,
                    "payload_hashes_match": True,
                    "corpus_and_config_bindings_match": True,
                },
                "Both index payloads are hash-valid and bound to the current corpus rendering and frozen configs.",
                "No remediation required.",
            )
        )
    except Exception as exc:
        add_engineering(
            _record(
                "engineering.indices.current_corpus_config_binding",
                FAIL,
                _error(exc),
                "Hash-valid BM25 and dense indices bound to the current frozen corpus and configs.",
                "One or both formal indices are missing, tampered, stale, or bound to different inputs.",
                "Rebuild both indices from the frozen corpus and configs, then rerun formal searches.",
            )
        )

    # Query snapshot.
    query_records: list[dict[str, Any]] | None = None
    query_ids: set[str] = set()
    query_stats: dict[str, Any] | None = None
    try:
        query_records = load_queries(paths.queries)
        query_stats = query_statistics(query_records)
        query_ids = {record["query_id"] for record in query_records}
        dialect_counts = dict(sorted(Counter(record.get("dialect") for record in query_records).items()))
        dialect_sensitive_count = sum(
            record.get("case_flags", {}).get("requires_dialect_reasoning") is True
            for record in query_records
        )
        version_sensitive_count = sum(
            record.get("case_flags", {}).get("requires_version_reasoning") is True
            for record in query_records
        )
        query_ok = (
            len(query_records) == EXPECTED_QUERY_COUNT
            and len(query_ids) == EXPECTED_QUERY_COUNT
            and dialect_counts == EXPECTED_QUERY_DIALECT_COUNTS
            and dialect_sensitive_count == EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT
            and version_sensitive_count == EXPECTED_VERSION_SENSITIVE_QUERY_COUNT
        )
        add_engineering(
            _record(
                "engineering.queries.snapshot",
                PASS if query_ok else FAIL,
                {
                    "record_count": len(query_records),
                    "unique_query_ids": len(query_ids),
                    "dialect_counts": dialect_counts,
                    "dialect_sensitive_count": dialect_sensitive_count,
                    "version_sensitive_count": version_sensitive_count,
                },
                {
                    "record_count": EXPECTED_QUERY_COUNT,
                    "unique_query_ids": EXPECTED_QUERY_COUNT,
                    "dialect_counts": EXPECTED_QUERY_DIALECT_COUNTS,
                    "dialect_sensitive_count": EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT,
                    "version_sensitive_count": EXPECTED_VERSION_SENSITIVE_QUERY_COUNT,
                },
                "The complete frozen development-query snapshot is present."
                if query_ok
                else "The development-query count, ID coverage, or dialect distribution differs.",
                "Restore annotation/codex/dev_250.jsonl; do not synthesize replacement queries."
                if not query_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        add_engineering(
            _record(
                "engineering.queries.snapshot",
                FAIL,
                _error(exc),
                {
                    "record_count": EXPECTED_QUERY_COUNT,
                    "unique_query_ids": EXPECTED_QUERY_COUNT,
                    "dialect_counts": EXPECTED_QUERY_DIALECT_COUNTS,
                    "dialect_sensitive_count": EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT,
                    "version_sensitive_count": EXPECTED_VERSION_SENSITIVE_QUERY_COUNT,
                },
                "The frozen query snapshot could not be loaded and validated.",
                "Restore the protected query JSONL and correct its schema upstream.",
            )
        )

    # Source qrels and the converted TREC qrels.
    qrel_entries: list[QrelEntry] | None = None
    qrel_label_counts: dict[int, int] | None = None
    known_chunk_ids = (
        {record["chunk_id"] for record in corpus_records}
        if corpus_records is not None
        else None
    )
    try:
        qrel_entries = load_qrels_jsonl(
            paths.qrels_source,
            known_chunk_ids=known_chunk_ids,
            require_all_labels=False,
        )
        label_counts = dict(sorted(Counter(item.relevance for item in qrel_entries).items()))
        qrel_label_counts = label_counts
        observed_qids = {item.query_id for item in qrel_entries}
        qrel_count_ok = len(qrel_entries) == EXPECTED_QREL_COUNT
        label_ok = label_counts == EXPECTED_QREL_LABEL_COUNTS
        add_engineering(
            _record(
                "engineering.qrels.count_and_labels",
                PASS if qrel_count_ok and label_ok else FAIL,
                {"record_count": len(qrel_entries), "label_counts": label_counts},
                {
                    "record_count": EXPECTED_QREL_COUNT,
                    "label_counts": EXPECTED_QREL_LABEL_COUNTS,
                },
                "All explicit qrels, including relevance zero, match the frozen counts."
                if qrel_count_ok and label_ok
                else "The qrel count or relevance-label distribution differs from the frozen snapshot.",
                "Restore the protected qrel snapshot; do not manufacture or relabel judgments."
                if not (qrel_count_ok and label_ok)
                else "No remediation required.",
            )
        )
        rel2_queries = {item.query_id for item in qrel_entries if item.relevance == 2}
        unknown_qids = sorted(observed_qids - query_ids)
        missing_qids = sorted(query_ids - observed_qids)
        missing_rel2 = sorted(query_ids - rel2_queries)
        coverage_ok = (
            query_records is not None
            and not unknown_qids
            and not missing_qids
            and not missing_rel2
        )
        add_engineering(
            _record(
                "engineering.qrels.query_coverage",
                PASS if coverage_ok else FAIL,
                {
                    "judged_query_count": len(observed_qids),
                    "queries_with_relevance_2": len(rel2_queries & query_ids),
                    "unknown_query_ids": unknown_qids[:20],
                    "missing_query_ids": missing_qids[:20],
                    "missing_relevance_2_query_ids": missing_rel2[:20],
                },
                {
                    "judged_query_count": EXPECTED_QUERY_COUNT,
                    "queries_with_relevance_2": EXPECTED_QUERY_COUNT,
                    "unknown_query_ids": [],
                    "missing_query_ids": [],
                    "missing_relevance_2_query_ids": [],
                },
                "Every known query has explicit judgments and at least one relevance-2 qrel."
                if coverage_ok
                else "Qrel query coverage is incomplete, unknown, or lacks direct evidence.",
                "Obtain a valid upstream judgment for each missing query; never infer relevance 2."
                if not coverage_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        failure = _error(exc)
        add_engineering(
            _record(
                "engineering.qrels.count_and_labels",
                FAIL,
                failure,
                {
                    "record_count": EXPECTED_QREL_COUNT,
                    "label_counts": EXPECTED_QREL_LABEL_COUNTS,
                },
                "The source qrels could not be parsed or validated.",
                "Restore valid, duplicate-free qrels using only labels 0, 1, and 2.",
            )
        )
        add_engineering(
            _record(
                "engineering.qrels.query_coverage",
                FAIL,
                failure,
                {
                    "judged_query_count": EXPECTED_QUERY_COUNT,
                    "queries_with_relevance_2": EXPECTED_QUERY_COUNT,
                },
                "Per-query qrel coverage could not be established.",
                "Repair the source qrel validation failure, then rerun validation.",
            )
        )

    # The generated input report is release evidence, not an arbitrary note.
    # Reconstruct its exact successful payload from the bytes validated above.
    try:
        if (
            corpus_summary is None
            or query_stats is None
            or qrel_entries is None
            or qrel_label_counts is None
        ):
            raise ValueError("validated corpus, queries, and qrels are required")
        rel2_query_count = len(
            {item.query_id for item in qrel_entries if item.relevance == 2}
        )
        required_input_values = {
            "query_count": EXPECTED_QUERY_COUNT,
            "dialect_counts": EXPECTED_QUERY_DIALECT_COUNTS,
            "dialect_sensitive_count": EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT,
            "version_sensitive_count": EXPECTED_VERSION_SENSITIVE_QUERY_COUNT,
            "qrel_count": EXPECTED_QREL_COUNT,
            "qrel_label_counts": EXPECTED_QREL_LABEL_COUNTS,
            "total_word_count": corpus_summary["total_word_count"],
            "approximate_unique_word_count": corpus_summary[
                "approximate_unique_word_count"
            ],
        }
        with paths.candidate_pools.open("rb") as candidate_pool_stream:
            candidate_pool_record_count = sum(
                1 for line in candidate_pool_stream if line.strip()
            )
        observed_input_values = {
            **query_stats,
            "qrel_count": len(qrel_entries),
            "qrel_label_counts": qrel_label_counts,
            "queries_with_relevance_2": rel2_query_count,
            "candidate_pool_sha256": sha256_file(paths.candidate_pools),
            "candidate_pool_record_count": candidate_pool_record_count,
            "total_word_count": corpus_summary["total_word_count"],
            "approximate_unique_word_count": corpus_summary[
                "approximate_unique_word_count"
            ],
        }
        expected_input_report = {
            "status": PASS,
            "corpus": corpus_summary,
            "observed": observed_input_values,
            "required": required_input_values,
            "failures": [],
            "machine_proposed_development_only": True,
        }
        # JSON object keys are strings in the persisted contract.
        expected_input_report = json.loads(
            json.dumps(expected_input_report, ensure_ascii=False)
        )
        observed_input_report = read_json(
            paths.reports / "input_validation.json", None
        )
        input_report_ok = observed_input_report == expected_input_report
        add_engineering(
            _record(
                "engineering.input_validation.exact_artifact",
                PASS if input_report_ok else FAIL,
                observed_input_report,
                expected_input_report,
                "The input-validation report exactly records the independently validated frozen inputs."
                if input_report_ok
                else "The input-validation report is stale, malformed, or inconsistent with current bytes.",
                "Rerun `verify-inputs` from the current frozen inputs."
                if not input_report_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.input_validation.exact_artifact",
                FAIL,
                _error(exc),
                "An exact successful input-validation report bound to current inputs.",
                "Input-validation evidence could not be reconstructed and checked.",
                "Rerun `verify-inputs` after repairing any upstream input failure.",
            )
        )

    try:
        trec_qrels = read_trec_qrels(
            paths.qrels,
            known_chunk_ids=known_chunk_ids,
            require_all_labels=True,
        )
        source_tuples = (
            sorted((item.query_id, item.chunk_id, item.relevance) for item in qrel_entries)
            if qrel_entries is not None
            else None
        )
        trec_tuples = sorted((item.query_id, item.chunk_id, item.relevance) for item in trec_qrels)
        converted_ok = source_tuples is not None and trec_tuples == source_tuples
        add_engineering(
            _record(
                "engineering.qrels.trec_conversion",
                PASS if converted_ok else FAIL,
                {"record_count": len(trec_qrels), "matches_source": converted_ok},
                {"record_count": EXPECTED_QREL_COUNT, "matches_source": True},
                "The TREC qrels preserve every source judgment exactly."
                if converted_ok
                else "The TREC qrels are stale or differ from the protected source qrels.",
                "Regenerate retrieval/baseline/qrels from the source qrels without changing labels."
                if not converted_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        add_engineering(
            _record(
                "engineering.qrels.trec_conversion",
                FAIL,
                _error(exc),
                {"record_count": EXPECTED_QREL_COUNT, "matches_source": True},
                "The converted TREC qrels could not be validated.",
                "Regenerate the TREC qrels from the protected JSONL snapshot.",
            )
        )

    # Whitelist serializer audit.
    serialized_ok = False
    try:
        serialized_records = _load_serialized_jsonl(paths.serialized_queries)
        if query_records is None:
            raise ValueError("source queries are unavailable")
        summary, violations = _serialized_violations(query_records, serialized_records)
        serialized_ok = (
            summary["record_count"] == EXPECTED_QUERY_COUNT
            and summary["unique_query_ids"] == EXPECTED_QUERY_COUNT
            and not violations
        )
        add_engineering(
            _record(
                "engineering.queries.serialized_whitelist",
                PASS if serialized_ok else FAIL,
                summary,
                {
                    "record_count": EXPECTED_QUERY_COUNT,
                    "unique_query_ids": EXPECTED_QUERY_COUNT,
                    "expected_query_ids_matched": True,
                    "violation_count": 0,
                },
                "Every serialized query exactly matches the deterministic user-field whitelist."
                if serialized_ok
                else "Serialized query records are missing, stale, malformed, or leak non-whitelisted data.",
                "Regenerate serialized queries with sqlmend-query-v1 from dev_250.jsonl only."
                if not serialized_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        add_engineering(
            _record(
                "engineering.queries.serialized_whitelist",
                FAIL,
                _error(exc),
                {
                    "record_count": EXPECTED_QUERY_COUNT,
                    "unique_query_ids": EXPECTED_QUERY_COUNT,
                    "violation_count": 0,
                },
                "Serialized query records could not be loaded and audited.",
                "Run the strict query serializer before retrieval and validation.",
            )
        )

    # Formal TREC runs.  read_trec_run enforces finite scores, unique
    # query/chunk pairs, continuous ranks, stable tags, and known chunk IDs.
    valid_runs: dict[str, list[TrecRunEntry]] = {}
    run_hashes: dict[str, str] = {}
    for system, path_attribute, _manifest_key, expected_tag in RUN_SPECS:
        run_path = getattr(paths, path_attribute)
        try:
            entries = read_trec_run(
                run_path,
                known_chunk_ids=known_chunk_ids,
                exact_results_per_query=EXPECTED_RESULTS_PER_QUERY,
                expected_run_tag=expected_tag,
            )
            summary = _run_summary(entries, run_path)
            observed_query_ids = {entry.query_id for entry in entries}
            run_ok = (
                query_records is not None
                and observed_query_ids == query_ids
                and summary["query_count"] == EXPECTED_QUERY_COUNT
                and summary["result_count"]
                == EXPECTED_QUERY_COUNT * EXPECTED_RESULTS_PER_QUERY
                and summary["minimum_results_per_query"] == EXPECTED_RESULTS_PER_QUERY
                and summary["maximum_results_per_query"] == EXPECTED_RESULTS_PER_QUERY
                and summary["all_scores_finite"]
            )
            summary["missing_query_ids"] = sorted(query_ids - observed_query_ids)[:20]
            summary["unexpected_query_ids"] = sorted(observed_query_ids - query_ids)[:20]
            if run_ok:
                valid_runs[system] = entries
                run_hashes[system] = summary["sha256"]
            add_engineering(
                _record(
                    f"engineering.run.{system}",
                    PASS if run_ok else FAIL,
                    summary,
                    {
                        "query_count": EXPECTED_QUERY_COUNT,
                        "result_count": EXPECTED_QUERY_COUNT * EXPECTED_RESULTS_PER_QUERY,
                        "results_per_query": EXPECTED_RESULTS_PER_QUERY,
                        "known_unique_chunk_ids": True,
                        "finite_scores": True,
                        "continuous_ranks": True,
                    },
                    f"The formal {system} run is a complete valid top-{EXPECTED_RESULTS_PER_QUERY} run."
                    if run_ok
                    else f"The formal {system} run does not cover the exact frozen query set.",
                    f"Regenerate the formal {system} run from frozen inputs with deterministic ranking."
                    if not run_ok
                    else "No remediation required.",
                )
            )
        except (OSError, UnicodeError, ValueError) as exc:
            add_engineering(
                _record(
                    f"engineering.run.{system}",
                    FAIL,
                    _error(exc),
                    {
                        "query_count": EXPECTED_QUERY_COUNT,
                        "results_per_query": EXPECTED_RESULTS_PER_QUERY,
                        "known_unique_chunk_ids": True,
                        "finite_scores": True,
                        "continuous_ranks": True,
                    },
                    f"The formal {system} TREC run is missing or invalid.",
                    f"Regenerate the formal {system} run and preserve canonical TREC formatting.",
                )
            )

    try:
        if set(valid_runs) != {"bm25", "dense", "hybrid"}:
            raise ValueError("all three valid component/formal runs are required")
        from .rrf import fuse_ranked_lists

        grouped: dict[str, dict[str, list[TrecRunEntry]]] = {
            system: defaultdict(list) for system in ("bm25", "dense")
        }
        for system in ("bm25", "dense"):
            for entry in valid_runs[system]:
                grouped[system][entry.query_id].append(entry)
        expected_hybrid: list[tuple[str, str, int, float]] = []
        expected_provenance: list[dict[str, Any]] = []
        for query_id in sorted(grouped["bm25"]):
            fused = fuse_ranked_lists(
                sorted(grouped["bm25"][query_id], key=lambda item: item.rank),
                sorted(grouped["dense"][query_id], key=lambda item: item.rank),
            )
            for item in fused:
                expected_hybrid.append((query_id, item.chunk_id, item.rank, item.rrf_score))
                expected_provenance.append(
                    {
                        "query_id": query_id,
                        "chunk_id": item.chunk_id,
                        "rank": item.rank,
                        "rrf_score": item.rrf_score,
                        "bm25_rank": item.bm25_rank,
                        "dense_rank": item.dense_rank,
                    }
                )
        observed_hybrid = [
            (item.query_id, item.chunk_id, item.rank, item.score)
            for item in valid_runs["hybrid"]
        ]
        hybrid_matches = len(observed_hybrid) == len(expected_hybrid) and all(
            observed[:3] == expected[:3]
            and math.isclose(observed[3], expected[3], rel_tol=0.0, abs_tol=5e-13)
            for observed, expected in zip(observed_hybrid, expected_hybrid, strict=True)
        )
        observed_provenance: list[Any] = []
        with paths.hybrid_provenance.open("r", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"blank hybrid provenance line {line_number}")
                observed_provenance.append(json.loads(line))
        provenance_matches = observed_provenance == expected_provenance
        add_engineering(
            _record(
                "engineering.hybrid.exact_rrf_and_provenance",
                PASS if hybrid_matches and provenance_matches else FAIL,
                {
                    "expected_rows": len(expected_hybrid),
                    "observed_rows": len(observed_hybrid),
                    "hybrid_run_exact_rrf": hybrid_matches,
                    "component_rank_provenance_exact": provenance_matches,
                },
                {
                    "hybrid_run_exact_rrf": True,
                    "component_rank_provenance_exact": True,
                },
                "Hybrid run and provenance are the exact fixed RRF of the validated components."
                if hybrid_matches and provenance_matches
                else "Hybrid results or component-rank provenance differ from fixed RRF recomputation.",
                "Regenerate hybrid from the validated BM25/dense runs with fixed RRF (60,30,30)."
                if not (hybrid_matches and provenance_matches)
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.hybrid.exact_rrf_and_provenance",
                FAIL,
                _error(exc),
                {"hybrid_run_exact_rrf": True, "component_rank_provenance_exact": True},
                "Hybrid RRF or its provenance could not be independently validated.",
                "Regenerate the hybrid run and provenance from validated component runs.",
            )
        )

    try:
        determinism = read_json(paths.evaluation / "run_determinism.json", None)
        if not isinstance(determinism, Mapping):
            raise ValueError("run determinism artifact must be a JSON object")
        deterministic_systems: dict[str, Any] = {}
        deterministic_ok = set(determinism) == {"bm25", "dense", "hybrid"}
        for system in ("bm25", "dense", "hybrid"):
            record = determinism.get(system)
            actual = run_hashes.get(system)
            valid = (
                isinstance(record, Mapping)
                and record.get("byte_identical") is True
                and record.get("first_sha256") == actual
                and record.get("second_sha256") == actual
            )
            deterministic_systems[system] = {"actual": actual, "evidence": record, "valid": valid}
            deterministic_ok = deterministic_ok and valid
        add_engineering(
            _record(
                "engineering.runs.repeated_byte_identity",
                PASS if deterministic_ok else FAIL,
                deterministic_systems,
                "For all three systems, first_sha256 == second_sha256 == current run hash and byte_identical is true.",
                "Repeated full official runs are byte-identical and match current artifacts."
                if deterministic_ok
                else "Repeated-run evidence is absent, incomplete, or does not match current run bytes.",
                "Rerun each official system twice through its run command and retain run_determinism.json."
                if not deterministic_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.runs.repeated_byte_identity",
                FAIL,
                _error(exc),
                "Complete byte-identical rerun evidence for bm25, dense, and hybrid.",
                "Repeated-run evidence could not be validated.",
                "Run all three formal run commands twice and retain their deterministic hashes.",
            )
        )

    # Every manifest run hash and embedded repetition must equal current bytes.
    manifest_path = paths.retrieval / "manifest.json"
    try:
        manifest = read_json(manifest_path, None)
        if not isinstance(manifest, Mapping):
            raise ValueError("manifest must be a JSON object")
        else:
            mismatches: dict[str, Any] = {}
            observed: dict[str, Any] = {"manifest_present": True, "systems": {}}
            for system, _path_attribute, direct_key, _expected_tag in RUN_SPECS:
                recorded = _recorded_run_hashes(manifest, system, direct_key)
                actual = run_hashes.get(system)
                observed["systems"][system] = {"actual": actual, "recorded": recorded}
                if actual is None or not recorded or any(value != actual for value in recorded):
                    mismatches[system] = observed["systems"][system]
            add_engineering(
                _record(
                    "engineering.runs.recorded_hashes",
                    PASS if not mismatches else FAIL,
                    observed,
                    "All present run-hash metadata (including repeated hashes) equals the current run bytes.",
                    "Recorded run hashes agree with the formal TREC artifacts."
                    if not mismatches
                    else "Manifest run hashes are incomplete, stale, or demonstrate nondeterministic reruns.",
                    "Repeat each formal run on frozen inputs and regenerate the manifest; investigate any byte mismatch."
                    if mismatches
                    else "No remediation required.",
                )
            )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.runs.recorded_hashes",
                FAIL,
                _error(exc),
                "Valid run-hash metadata when a manifest is present.",
                "The present manifest could not be used to validate run hashes.",
                "Repair or regenerate retrieval/baseline/manifest.json from the current artifacts.",
            )
        )

    # Build the authoritative evaluation qrels from the immutable base plus an
    # optional, distinct external judgment file.  The generated effective TREC
    # file must be an exact rendering of that merge.
    evaluation_qrels: list[QrelEntry] | None = None
    try:
        if qrel_entries is None or set(valid_runs) != {item[0] for item in RUN_SPECS}:
            raise ValueError("valid base qrels and all formal runs are required")
        from .qrels import merge_supplemental_qrels

        evaluation_qrels, merge_metadata = merge_supplemental_qrels(
            qrel_entries,
            paths.supplemental_qrels,
            valid_runs,
            known_chunk_ids=known_chunk_ids,
        )
        recorded_effective = read_trec_qrels(
            paths.effective_qrels,
            known_chunk_ids=known_chunk_ids,
            require_all_labels=True,
        )
        expected_tuples = sorted(
            (item.query_id, item.chunk_id, item.relevance) for item in evaluation_qrels
        )
        recorded_tuples = sorted(
            (item.query_id, item.chunk_id, item.relevance) for item in recorded_effective
        )
        expected_merge_report = {
            **merge_metadata,
            "base_qrels_sha256": sha256_file(paths.qrels),
            "effective_qrels_sha256": sha256_file(paths.effective_qrels),
            "protected_source_unchanged": True,
        }
        observed_merge_report = read_json(
            paths.reports / "effective_qrels.json", None
        )
        trec_matches = expected_tuples == recorded_tuples
        metadata_matches = observed_merge_report == expected_merge_report
        matches = trec_matches and metadata_matches
        add_engineering(
            _record(
                "engineering.qrels.effective_merge",
                PASS if matches else FAIL,
                {
                    **merge_metadata,
                    "matches_generated_trec": trec_matches,
                    "metadata_matches": metadata_matches,
                    "observed_metadata": observed_merge_report,
                },
                {
                    "matches_generated_trec": True,
                    "metadata_matches": True,
                    "expected_metadata": expected_merge_report,
                },
                "Effective qrels and their metadata exactly record the frozen-base plus supplemental merge."
                if matches
                else "The generated effective qrels or merge metadata are stale or inconsistent.",
                "Regenerate effective qrels; never edit protected qrels or the request artifact."
                if not matches
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        evaluation_qrels = None
        add_engineering(
            _record(
                "engineering.qrels.effective_merge",
                FAIL,
                _error(exc),
                {"matches_generated_trec": True},
                "The effective evaluation-qrels merge could not be validated.",
                "Provide duplicate-free supplemental judgments only for current unjudged top-30 pairs and regenerate.",
            )
        )

    # Direct judgment coverage is authoritative; summary files must agree.
    direct_coverage: dict[str, float] | None = None
    direct_unjudged: dict[str, int] | None = None
    if evaluation_qrels is not None and set(valid_runs) == {item[0] for item in RUN_SPECS}:
        direct_coverage, direct_unjudged = _direct_judgment_coverage(valid_runs, evaluation_qrels)

    expected_pool_summary: dict[str, Any] | None = None
    try:
        if evaluation_qrels is None or corpus_records is None or set(valid_runs) != {"bm25", "dense", "hybrid"}:
            raise ValueError("validated corpus, effective qrels, and all formal runs are required")
        from .pool_audit import audit_pool

        expected_pool = audit_pool(
            {
                "bm25_formal": valid_runs["bm25"],
                "dense_formal": valid_runs["dense"],
                "hybrid_rrf_formal": valid_runs["hybrid"],
            },
            evaluation_qrels,
            corpus_records,
        )
        expected_records = expected_pool["pool_expansion_records"]
        expected_summary = {
            key: value for key, value in expected_pool.items() if key != "pool_expansion_records"
        }
        expected_pool_summary = expected_summary
        observed_records: list[Any] = []
        with (paths.pool_expansion / "pool_expansion_required.jsonl").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"blank pool-expansion line {line_number}")
                observed_records.append(json.loads(line))
        observed_summary = read_json(
            paths.pool_expansion / "pool_expansion_summary.json", None
        )
        pool_artifacts_ok = (
            observed_records == expected_records
            and observed_summary == expected_summary
        )
        add_engineering(
            _record(
                "engineering.pool_expansion.exact_artifacts",
                PASS if pool_artifacts_ok else FAIL,
                {
                    "expected_unique_request_count": len(expected_records),
                    "observed_unique_request_count": len(observed_records),
                    "request_records_exact": observed_records == expected_records,
                    "summary_exact": observed_summary == expected_summary,
                },
                {
                    "request_records_exact": True,
                    "summary_exact": True,
                },
                "Pool-expansion requests and all Judged@K summaries exactly match current formal runs."
                if pool_artifacts_ok
                else "Pool-expansion artifacts are missing, stale, malformed, or incomplete.",
                "Rerun check-pool; place completed judgments only in the distinct supplemental-qrels file."
                if not pool_artifacts_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.pool_expansion.exact_artifacts",
                FAIL,
                _error(exc),
                {"request_records_exact": True, "summary_exact": True},
                "Pool-expansion artifacts could not be recomputed and validated.",
                "Rerun check-pool from valid formal runs and effective qrels.",
            )
        )

    if direct_coverage is None or direct_unjudged is None:
        add_evaluation(
            _record(
                "evaluation.judged_at_30",
                FAIL,
                {"direct_coverage": direct_coverage, "direct_unjudged": direct_unjudged},
                {"bm25": 1.0, "dense": 1.0, "hybrid": 1.0},
                "Judged@30 could not be established directly from all formal runs and qrels.",
                "Repair the formal runs/qrels, then rerun the pool audit and evaluation.",
            )
        )
    else:
        has_unjudged = any(direct_unjudged.values())
        coverage_complete = all(
            math.isclose(value, 1.0, abs_tol=1e-12)
            for value in direct_coverage.values()
        )
        direct_status = BLOCKED if has_unjudged else (PASS if coverage_complete else FAIL)
        add_evaluation(
            _record(
                "evaluation.judged_at_30",
                direct_status,
                {
                    "computed": direct_coverage,
                    "unjudged_top30_occurrences": direct_unjudged,
                },
                {"bm25": 1.0, "dense": 1.0, "hybrid": 1.0},
                "At least one formal top-30 result is unjudged; evaluation publication is blocked."
                if has_unjudged
                else "All formal top-30 results are explicitly judged."
                if direct_status == PASS
                else "Direct Judged@30 coverage is internally inconsistent.",
                "Request judgments using pool_expansion_required.jsonl; never map missing qrels to zero."
                if has_unjudged
                else "No remediation required."
                if direct_status == PASS
                else "Repair the formal runs/qrels and recompute direct coverage.",
            )
        )

    # The persisted coverage report must agree exactly with the direct result.
    # Artifact corruption is an engineering failure even when incompleteness is
    # also a legitimate evaluation blocker.
    judged_path = paths.evaluation / "judged_coverage.json"
    try:
        if direct_unjudged is None or expected_pool_summary is None:
            raise ValueError("direct coverage and recomputed pool summary are required")
        expected_judged_payload = {
            "evaluation_label": EVALUATION_LABEL,
            "unjudged_documents_are_not_relevance_zero": True,
            "per_system": expected_pool_summary["per_system"],
            "evaluation_integrity_status": (
                BLOCKED if any(direct_unjudged.values()) else PASS
            ),
        }
        judged_payload = read_json(judged_path, None)
        judged_artifact_ok = judged_payload == expected_judged_payload
        add_engineering(
            _record(
                "engineering.judged_coverage.exact_artifact",
                PASS if judged_artifact_ok else FAIL,
                judged_payload,
                expected_judged_payload,
                "The judged-coverage artifact exactly matches direct formal-run coverage."
                if judged_artifact_ok
                else "The judged-coverage artifact is malformed, stale, or inconsistent.",
                "Rerun `evaluate` to regenerate judged_coverage.json from current runs and qrels."
                if not judged_artifact_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.judged_coverage.exact_artifact",
                FAIL,
                _error(exc),
                "An exact judged-coverage artifact generated from current formal runs and qrels.",
                "The judged-coverage artifact could not be validated.",
                "Rerun the pool audit and `evaluate`.",
            )
        )

    pool_summary_path = paths.pool_expansion / "pool_expansion_summary.json"
    try:
        pool = read_json(pool_summary_path, None)
        if not isinstance(pool, Mapping):
            raise ValueError("pool expansion summary must be a JSON object")
        if direct_unjudged is None:
            raise ValueError("direct top-30 judgment coverage is unavailable")
        computed_count = sum(direct_unjudged.values())
        computed_blocked = computed_count > 0
        reported_count = pool.get("unjudged_top30_occurrence_count")
        reported_required = pool.get("pool_expansion_required")
        reported_status = pool.get("evaluation_integrity_status")
        consistent = (
            reported_count == computed_count
            and reported_required is computed_blocked
            and reported_status == (BLOCKED if computed_blocked else PASS)
        )
        status = BLOCKED if computed_blocked else (PASS if consistent else FAIL)
        add_evaluation(
            _record(
                "evaluation.pool_summary",
                status,
                {
                    "computed_unjudged_top30_occurrences": computed_count,
                    "reported_unjudged_top30_occurrences": reported_count,
                    "pool_expansion_required": reported_required,
                    "reported_status": reported_status,
                    "summary_consistent": consistent,
                },
                {
                    "computed_unjudged_top30_occurrences": 0,
                    "pool_expansion_required": False,
                    "reported_status": PASS,
                    "summary_consistent": True,
                },
                "Pool expansion is required before formal evaluation metrics may be published."
                if computed_blocked
                else (
                    "The pool summary confirms complete explicit judgment coverage."
                    if status == PASS
                    else "The pool summary is stale or inconsistent with formal runs."
                ),
                "Complete the requested judgments externally and rerun the pool audit."
                if computed_blocked
                else (
                    "Regenerate pool_expansion_summary.json from the current runs and qrels."
                    if status == FAIL
                    else "No remediation required."
                ),
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_evaluation(
            _record(
                "evaluation.pool_summary",
                BLOCKED if direct_unjudged and any(direct_unjudged.values()) else FAIL,
                _error(exc),
                {
                    "unjudged_top30_occurrences": 0,
                    "pool_expansion_required": False,
                    "reported_status": PASS,
                },
                "The pool summary is unavailable; direct unjudged results still block evaluation."
                if direct_unjudged and any(direct_unjudged.values())
                else "The required pool-expansion summary could not be validated.",
                "Regenerate both pool-expansion artifacts from current formal runs and qrels.",
            )
        )

    coverage_is_blocked = bool(direct_unjudged and any(direct_unjudged.values()))

    # Historical annotation retrieval is provenance evidence only, but the
    # evidence itself must be coherent: current inputs, explicit outcome, and
    # a hash-valid run exactly when a system says it was reproduced.
    try:
        reproduction_report = read_json(
            paths.reproduction / "reproduction_report.json", None
        )
        if not isinstance(reproduction_report, Mapping):
            raise ValueError("reproduction report must be a JSON object")
        model_provenance = read_json(
            paths.annotation / "provenance" / "embedding_model.json", None
        )
        if not isinstance(model_provenance, Mapping):
            raise ValueError("embedding-model provenance must be a JSON object")
        expected_reproduction_inputs = {
            "implementation_sha256": sha256_file(
                Path(__file__).with_name("reproduction.py")
            ),
            "corpus_sha256": sha256_file(paths.corpus),
            "queries_sha256": sha256_file(paths.queries),
            "stored_runs_sha256": sha256_file(
                paths.annotation / "provenance" / "retrieval_runs.jsonl"
            ),
            "candidate_pools_sha256": sha256_file(paths.candidate_pools),
            "retrieval_config_sha256": sha256_file(
                paths.annotation / "provenance" / "retrieval_config.json"
            ),
            "embedding_model_sha256": sha256_file(
                paths.annotation / "provenance" / "embedding_model.json"
            ),
            "snapshot_manifest_sha256": model_provenance.get(
                "snapshot_manifest_sha256"
            ),
        }
        reproduction_run_paths = {
            "bm25": paths.reproduction / "bm25_annotation_reproduced.trec",
            "dense": paths.reproduction / "dense_annotation_reproduced.trec",
            "hybrid_rrf": paths.reproduction
            / "hybrid_annotation_reproduced.trec",
        }
        legacy_run_paths = (
            paths.reproduction / "annotation_bm25_reproduced.trec",
            paths.reproduction / "annotation_dense_reproduced.trec",
            paths.reproduction / "annotation_hybrid_reproduced.trec",
            paths.reproduction / "annotation_hybrid_rrf_reproduced.trec",
        )
        reproduction_violations: list[str] = []
        if reproduction_report.get("schema_version") != "sqlmend-annotation-reproduction-v1":
            reproduction_violations.append("schema_version is invalid")
        if reproduction_report.get("attempt_completed") is not True:
            reproduction_violations.append("attempt_completed is not true")
        if reproduction_report.get("evaluation_label") != EVALUATION_LABEL:
            reproduction_violations.append("development evaluation label is missing")
        if reproduction_report.get("machine_proposed_development_only") is not True:
            reproduction_violations.append("development-only flag is missing")
        if reproduction_report.get("historical_query_contains_annotation_only_fields") is not True:
            reproduction_violations.append("historical-query risk is not disclosed")
        if reproduction_report.get("historical_query_is_never_used_by_formal_baselines") is not True:
            reproduction_violations.append("formal-baseline isolation is not asserted")
        independence = reproduction_report.get("formal_baseline_independence")
        if not isinstance(independence, Mapping) or (
            independence.get("uses_candidate_pool_ranks") is not False
            or independence.get("uses_qrels_during_search") is not False
        ):
            reproduction_violations.append("formal-baseline independence is malformed")
        if reproduction_report.get("inputs") != expected_reproduction_inputs:
            reproduction_violations.append("input hashes do not match current bytes")
        if reproduction_report.get("annotation_reproduction_status") not in {
            "PARTIAL",
            "NOT_REPRODUCIBLE",
        }:
            reproduction_violations.append("annotation reproduction status is invalid")
        if reproduction_report.get("provenance_completeness_status") not in {
            "PARTIAL",
            "NOT_REPRODUCIBLE",
        }:
            reproduction_violations.append("provenance completeness status is invalid")
        preflight = reproduction_report.get("preflight_validation")
        if not isinstance(preflight, Mapping) or preflight.get("status") not in {
            PASS,
            FAIL,
        }:
            reproduction_violations.append("preflight validation is missing or invalid")
        systems = reproduction_report.get("systems")
        if not isinstance(systems, Mapping) or set(systems) != set(
            reproduction_run_paths
        ):
            reproduction_violations.append("system evidence must cover exactly three retrievers")
            systems = {}
        for system, run_path in reproduction_run_paths.items():
            detail = systems.get(system)
            if not isinstance(detail, Mapping):
                reproduction_violations.append(f"{system}: evidence is not an object")
                continue
            status = detail.get("status")
            if status not in {PASS, "PARTIAL", "NOT_REPRODUCIBLE"}:
                reproduction_violations.append(f"{system}: status is invalid")
                continue
            recorded_hash = detail.get("reproduced_run_sha256")
            if status == "NOT_REPRODUCIBLE":
                if run_path.is_file() or recorded_hash is not None:
                    reproduction_violations.append(
                        f"{system}: stale run exists for NOT_REPRODUCIBLE status"
                    )
                continue
            if not run_path.is_file() or recorded_hash != sha256_file(run_path):
                reproduction_violations.append(
                    f"{system}: reproduced run is missing or hash-mismatched"
                )
                continue
            parsed_reproduction = read_trec_run(
                run_path,
                known_chunk_ids=known_chunk_ids,
                exact_results_per_query=EXPECTED_RESULTS_PER_QUERY,
            )
            if {item.query_id for item in parsed_reproduction} != query_ids:
                reproduction_violations.append(
                    f"{system}: reproduced run query universe is incomplete"
                )
        stale_legacy = [
            path.name for path in legacy_run_paths if path.is_file()
        ]
        if stale_legacy:
            reproduction_violations.append(
                f"legacy reproduced-run names remain: {stale_legacy}"
            )
        add_engineering(
            _record(
                "engineering.annotation_reproduction.evidence_contract",
                PASS if not reproduction_violations else FAIL,
                {
                    "annotation_reproduction_status": reproduction_report.get(
                        "annotation_reproduction_status"
                    ),
                    "system_statuses": {
                        name: detail.get("status")
                        for name, detail in systems.items()
                        if isinstance(detail, Mapping)
                    },
                    "violations": reproduction_violations[:50],
                },
                {
                    "inputs_match": True,
                    "system_run_hash_or_absence_matches_status": True,
                    "legacy_run_files": [],
                    "violations": [],
                },
                "Annotation-retriever provenance evidence is coherent and isolated from formal baselines."
                if not reproduction_violations
                else "Annotation-retriever provenance evidence is stale, malformed, or internally inconsistent.",
                "Rerun `audit-annotation-retrievers` from current protected inputs."
                if reproduction_violations
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.annotation_reproduction.evidence_contract",
                FAIL,
                _error(exc),
                "A complete current-input reproduction report with matching run hashes or explicit absence.",
                "Annotation-retriever provenance evidence could not be validated.",
                "Rerun `audit-annotation-retrievers`; do not use historical rankings in formal baselines.",
            )
        )

    # The release manifest must bind all inputs and generated evidence to the
    # current bytes.  A Git commit alone is insufficient because retrieval/baseline/
    # may be dirty or untracked during development.
    release_manifest_payload: Mapping[str, Any] | None = None
    try:
        release_manifest = read_json(paths.retrieval / "manifest.json", None)
        if not isinstance(release_manifest, Mapping):
            raise ValueError("manifest must be a JSON object")
        release_manifest_payload = release_manifest
        manifest_violations: list[str] = []
        current_source_snapshot = snapshot_release_source(paths)
        bm25_index_metadata = read_json(paths.bm25_index / "metadata.json", None)
        dense_index_metadata = read_json(paths.dense_index / "metadata.json", None)
        if not isinstance(bm25_index_metadata, Mapping) or not isinstance(
            dense_index_metadata, Mapping
        ):
            raise ValueError("index metadata must be JSON objects")
        dense_content_identity = {
            "embeddings_sha256": dense_index_metadata.get("embeddings_sha256"),
            "chunk_ids_sha256": dense_index_metadata.get("chunk_ids_sha256"),
            "configuration": dense_index_metadata.get("configuration"),
        }

        def optional_hash(path: Path) -> str | None:
            return sha256_file(path) if path.is_file() else None

        expected_manifest_hashes = {
            "corpus_sha256": sha256_file(paths.corpus),
            "query_sha256": sha256_file(paths.queries),
            "qrels_source_sha256": sha256_file(paths.qrels_source),
            "base_trec_qrels_sha256": sha256_file(paths.qrels),
            "effective_qrels_sha256": sha256_file(paths.effective_qrels),
            "effective_qrels_metadata_sha256": sha256_file(
                paths.reports / "effective_qrels.json"
            ),
            "candidate_pool_sha256": sha256_file(paths.candidate_pools),
            "query_serializer_config_sha256": sha256_file(
                paths.config / "query_serializer.yaml"
            ),
            "serialized_queries_sha256": sha256_file(paths.serialized_queries),
            "bm25_config_sha256": sha256_file(paths.config / "bm25_baseline.yaml"),
            "dense_config_sha256": sha256_file(paths.config / "dense_baseline.yaml"),
            "hybrid_config_sha256": sha256_file(
                paths.config / "hybrid_rrf_baseline.yaml"
            ),
            "evaluation_config_sha256": sha256_file(paths.config / "evaluation.yaml"),
            "bm25_index_sha256": bm25_index_metadata.get("payload_sha256"),
            "dense_index_sha256": canonical_json_sha256(dense_content_identity),
            "dense_model_snapshot_sha256": (
                sha256_tree(paths.dense_index / "model_cache")
                if (paths.dense_index / "model_cache").is_dir()
                else None
            ),
            "bm25_run_sha256": sha256_file(paths.bm25_run),
            "dense_run_sha256": sha256_file(paths.dense_run),
            "hybrid_run_sha256": sha256_file(paths.hybrid_run),
            "hybrid_provenance_sha256": sha256_file(paths.hybrid_provenance),
            "protected_paths_report_sha256": sha256_file(paths.protected_report),
            "run_determinism_sha256": sha256_file(
                paths.evaluation / "run_determinism.json"
            ),
            "test_results_sha256": sha256_file(paths.reports / "test_results.json"),
            "input_validation_sha256": sha256_file(
                paths.reports / "input_validation.json"
            ),
            "annotation_reproduction_sha256": sha256_file(
                paths.reproduction / "reproduction_report.json"
            ),
            "latency_sha256": sha256_file(paths.evaluation / "latency.json"),
            "judged_coverage_sha256": sha256_file(
                paths.evaluation / "judged_coverage.json"
            ),
            "overall_metrics_sha256": sha256_file(
                paths.evaluation / "overall_metrics.json"
            ),
            "per_query_metrics_sha256": optional_hash(
                paths.evaluation / "per_query_metrics.csv"
            ),
            "slice_metrics_sha256": optional_hash(
                paths.evaluation / "slice_metrics.csv"
            ),
            "confidence_intervals_sha256": optional_hash(
                paths.evaluation / "confidence_intervals.json"
            ),
            "pairwise_differences_sha256": optional_hash(
                paths.evaluation / "pairwise_differences.json"
            ),
            "complementarity_report_sha256": optional_hash(
                paths.evaluation / "complementarity_report.json"
            ),
            "baseline_report_sha256": sha256_file(
                paths.reports / "baseline_report.md"
            ),
            "failure_analysis_sha256": sha256_file(
                paths.reports / "failure_analysis.md"
            ),
            "provenance_audit_sha256": sha256_file(
                paths.reports / "provenance_audit.md"
            ),
            "completion_report_sha256": sha256_file(
                paths.reports / "completion_report.md"
            ),
            "pool_expansion_summary_sha256": sha256_file(
                paths.pool_expansion / "pool_expansion_summary.json"
            ),
            "pool_expansion_requests_sha256": sha256_file(
                paths.pool_expansion / "pool_expansion_required.jsonl"
            ),
        }
        expected_supplemental_hash = (
            sha256_file(paths.supplemental_qrels)
            if paths.supplemental_qrels.is_file()
            else None
        )
        expected_manifest_hashes["supplemental_qrels_sha256"] = expected_supplemental_hash
        for field, expected_value in expected_manifest_hashes.items():
            if release_manifest.get(field) != expected_value:
                manifest_violations.append(f"{field} does not match current bytes")
        if release_manifest.get("retrieval_source_tree_sha256") != current_source_snapshot["tree_sha256"]:
            manifest_violations.append("retrieval_source_tree_sha256 does not match current source")
        if release_manifest.get("retrieval_source_file_count") != current_source_snapshot["file_count"]:
            manifest_violations.append("retrieval_source_file_count does not match current source")
        if release_manifest.get("schema_version") != "sqlmend-retrieval-manifest-v1":
            manifest_violations.append("manifest schema_version is invalid")
        if release_manifest.get("module") != "sqlmend-retrieval-baseline":
            manifest_violations.append("manifest module is invalid")
        if release_manifest.get("machine_proposed_development_only") is not True:
            manifest_violations.append("manifest does not preserve the development-only label")
        recorded_engineering = release_manifest.get("engineering_status")
        recorded_evaluation = release_manifest.get("evaluation_integrity_status")
        expected_release = (
            "retrieval-baseline-v1"
            if recorded_engineering == PASS and recorded_evaluation == PASS
            else "retrieval-baseline-v1-candidate"
            if recorded_engineering == PASS and recorded_evaluation == BLOCKED
            else "retrieval-baseline-v1-invalid"
        )
        if release_manifest.get("release") != expected_release:
            manifest_violations.append("release name is inconsistent with recorded statuses")
        if coverage_is_blocked and (
            recorded_evaluation not in {BLOCKED, FAIL}
            or release_manifest.get("retrieval_quality_status") != "NOT_EVALUATED"
        ):
            manifest_violations.append("manifest statuses contradict direct unjudged coverage")
        if not coverage_is_blocked and recorded_evaluation == BLOCKED:
            manifest_violations.append("manifest reports BLOCKED despite complete direct coverage")
        add_engineering(
            _record(
                "engineering.manifest.current_artifact_binding",
                PASS if not manifest_violations else FAIL,
                {
                    "release": release_manifest.get("release"),
                    "recorded_engineering_status": recorded_engineering,
                    "recorded_evaluation_status": recorded_evaluation,
                    "bound_hash_count": len(expected_manifest_hashes),
                    "violations": manifest_violations[:50],
                },
                {
                    "all_current_artifact_hashes_match": True,
                    "source_tree_matches": True,
                    "release_and_statuses_consistent": True,
                    "violations": [],
                },
                "Manifest hashes, source identity, release name, and statuses bind the current candidate."
                if not manifest_violations
                else "Manifest is stale, incomplete, or inconsistent with current artifacts.",
                "Regenerate manifest only after all artifacts, tests, and audits are final."
                if manifest_violations
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.manifest.current_artifact_binding",
                FAIL,
                _error(exc),
                "A complete manifest whose hashes and statuses bind all current release artifacts.",
                "Manifest artifact binding could not be validated.",
                "Regenerate the manifest after the final test and protected-path audit.",
            )
        )

    if coverage_is_blocked:
        try:
            blocked_payload = read_json(paths.evaluation / "overall_metrics.json", None)
            if not isinstance(blocked_payload, Mapping):
                raise ValueError("blocked overall artifact must be a JSON object")
            stale_publishable = [
                name
                for name in (
                    "per_query_metrics.csv",
                    "slice_metrics.csv",
                    "confidence_intervals.json",
                    "pairwise_differences.json",
                    "complementarity_report.json",
                )
                if (paths.evaluation / name).is_file()
            ]
            expected_blocked_payload = {
                "evaluation_label": EVALUATION_LABEL,
                "status": BLOCKED,
                "reason": "At least one formal top-30 document is unjudged.",
                "metrics_published": False,
                "unjudged_documents_are_not_relevance_zero": True,
                "required_action": "Obtain external judgments for pool_expansion_required.jsonl, merge them into a separately versioned evaluation qrels file without editing protected inputs, then rerun the pool audit and evaluation.",
            }
            sentinel_ok = blocked_payload == expected_blocked_payload and not stale_publishable
            add_engineering(
                _record(
                    "engineering.blocked_metric_suppression",
                    PASS if sentinel_ok else FAIL,
                    {
                        "overall_status": blocked_payload.get("status"),
                        "metrics_published": blocked_payload.get("metrics_published"),
                        "payload_exact": blocked_payload == expected_blocked_payload,
                        "unexpected_or_missing_fields": sorted(
                            set(blocked_payload) ^ set(expected_blocked_payload)
                        ),
                        "stale_publishable_artifacts": stale_publishable,
                    },
                    {
                        "overall_status": BLOCKED,
                        "metrics_published": False,
                        "payload_exact": True,
                        "unexpected_or_missing_fields": [],
                        "stale_publishable_artifacts": [],
                    },
                    "Blocked evaluation publishes only an explicit sentinel and no stale metric bundle."
                    if sentinel_ok
                    else "Blocked evaluation coexists with a malformed sentinel or stale publishable metrics.",
                    "Rerun evaluate to replace overall_metrics.json and remove stale metric artifacts."
                    if not sentinel_ok
                    else "No remediation required.",
                )
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            add_engineering(
                _record(
                    "engineering.blocked_metric_suppression",
                    FAIL,
                    _error(exc),
                    {"overall_status": BLOCKED, "metrics_published": False, "stale_publishable_artifacts": []},
                    "Blocked metric-suppression evidence could not be validated.",
                    "Rerun evaluate after the pool audit.",
                )
            )

    # Benchmark evidence is part of engineering reproducibility.  Presence of
    # latency.json alone is insufficient because an empty object used to pass.
    try:
        latency_payload = read_json(paths.evaluation / "latency.json", None)
        latency_observed, latency_violations = _validate_latency_payload(latency_payload)
        add_engineering(
            _record(
                "engineering.latency.complete_evidence",
                PASS if not latency_violations else FAIL,
                latency_observed,
                {
                    "query_count": EXPECTED_QUERY_COUNT,
                    "warmup_queries_minimum": 3,
                    "all_component_sample_counts": EXPECTED_QUERY_COUNT,
                    "all_statistics_finite_non_negative": True,
                    "environment_and_build_metadata_present": True,
                },
                "Latency evidence covers every formal query, all serving components, build sizes, and the runtime environment."
                if not latency_violations
                else "Latency evidence is absent, incomplete, or internally inconsistent.",
                "Rerun benchmark with the frozen 250 queries and retain the complete latency artifact."
                if latency_violations
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.latency.complete_evidence",
                FAIL,
                _error(exc),
                "Complete finite benchmark evidence for all systems and components.",
                "Latency evidence could not be validated.",
                "Rerun benchmark and regenerate evaluation/latency.json.",
            )
        )

    # Required artifact existence is an engineering gate.  Full metric outputs
    # become required only after the judgment pool is complete.
    required_files = list(REQUIRED_ENGINEERING_FILES)
    if direct_unjudged is not None and not coverage_is_blocked:
        required_files.extend(REQUIRED_COMPLETE_EVALUATION_FILES)
    missing_files = [
        relative for relative in required_files if not (paths.retrieval / relative).is_file()
    ]
    add_engineering(
        _record(
            "engineering.required_files",
            PASS if not missing_files else FAIL,
            {
                "required_file_count": len(required_files),
                "present_file_count": len(required_files) - len(missing_files),
                "missing_files": missing_files,
            },
            {"missing_files": []},
            "All artifacts required at this judgment-coverage state exist."
            if not missing_files
            else "One or more required release artifacts are absent.",
            "Run the missing pipeline stages and regenerate the named artifacts."
            if missing_files
            else "No remediation required.",
        )
    )

    # Protected-path before/after snapshot.
    try:
        protected = read_json(paths.protected_report, None)
        if not isinstance(protected, Mapping):
            raise ValueError("protected-path report must be a JSON object")
        before = protected.get("before")
        after = protected.get("after")
        differences = protected.get("differences")
        differences_empty = isinstance(differences, Mapping) and not any(
            differences.get(name) for name in ("added", "removed", "changed")
        )
        current = snapshot_protected_paths(paths)
        current_matches_after = (
            isinstance(after, Mapping)
            and after.get("tree_sha256") == current.get("tree_sha256")
            and after.get("file_count") == current.get("file_count")
            and after.get("files") == current.get("files")
        )
        protected_ok = (
            isinstance(before, Mapping)
            and isinstance(after, Mapping)
            and before.get("tree_sha256") == after.get("tree_sha256")
            and before.get("file_count") == after.get("file_count")
            and before.get("files") == after.get("files")
            and differences_empty
            and protected.get("protected_paths_unchanged") is True
            and current_matches_after
        )
        add_engineering(
            _record(
                "engineering.protected_paths",
                PASS if protected_ok else FAIL,
                {
                    "before_tree_sha256": before.get("tree_sha256") if isinstance(before, Mapping) else None,
                    "after_tree_sha256": after.get("tree_sha256") if isinstance(after, Mapping) else None,
                    "differences": differences,
                    "protected_paths_unchanged": protected.get("protected_paths_unchanged"),
                    "current_tree_sha256": current.get("tree_sha256"),
                    "current_matches_recorded_after": current_matches_after,
                },
                {
                    "before_equals_after": True,
                    "differences": {"added": [], "removed": [], "changed": []},
                    "protected_paths_unchanged": True,
                },
                "Protected construction and annotation files are byte-identical before and after work."
                if protected_ok
                else "The protected-path audit is incomplete, internally inconsistent, or stale relative to current bytes.",
                "Restore all protected bytes and rerun both protected-path audit phases."
                if not protected_ok
                else "No remediation required.",
            )
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        add_engineering(
            _record(
                "engineering.protected_paths",
                FAIL,
                _error(exc),
                {"protected_paths_unchanged": True, "after_snapshot_present": True},
                "The protected-path after-audit could not be validated.",
                "Run audit-protected-paths before and after the pipeline, then rerun validation.",
            )
        )

    # Exact development-data and pooled-recall wording in human-facing reports.
    phrase_observed: dict[str, Any] = {}
    phrases_ok = True
    for relative in REQUIRED_REPORT_FILES:
        path = paths.retrieval / relative
        try:
            text = path.read_text(encoding="utf-8")
            has_development_label = EVALUATION_LABEL in text
            has_pooled_recall = POOLED_RECALL_LABEL in text
            recall_ok = has_pooled_recall
            missing_markers = [
                marker
                for marker in REQUIRED_REPORT_MARKERS.get(relative, ())
                if marker not in text
            ]
            phrase_observed[relative] = {
                "machine_proposed_development_evaluation": has_development_label,
                "pooled_recall_wording": recall_ok,
                "missing_required_sections": missing_markers,
            }
            phrases_ok = (
                phrases_ok
                and has_development_label
                and recall_ok
                and not missing_markers
            )
        except (OSError, UnicodeError) as exc:
            phrase_observed[relative] = _error(exc)
            phrases_ok = False
    add_engineering(
        _record(
            "engineering.required_report_phrases",
            PASS if phrases_ok else FAIL,
            phrase_observed,
            {
                "all_reports_contain": EVALUATION_LABEL,
                "recall_wording": POOLED_RECALL_LABEL,
                "all_required_report_sections_present": True,
            },
            "All required reports preserve the development-data label and pooled-Recall wording."
            if phrases_ok
            else "A required report omits the exact development-data or pooled-Recall wording.",
            f"Add the exact phrases '{EVALUATION_LABEL}' and '{POOLED_RECALL_LABEL}' where required."
            if not phrases_ok
            else "No remediation required.",
        )
    )

    # Metric schema is checked only when publication is not pool-blocked.
    metric_payload: Any = None
    metrics_by_system: dict[str, Mapping[str, Any]] = {}
    if direct_unjudged is not None and not coverage_is_blocked:
        try:
            metric_payload = read_json(paths.evaluation / "overall_metrics.json", None)
            metrics_by_system = _system_metrics(metric_payload)
            violations: list[str] = []
            if set(metrics_by_system) != {"bm25", "dense", "hybrid"}:
                violations.append(
                    f"systems are {sorted(metrics_by_system)}; bm25, dense, and hybrid required"
                )
            for system, metrics in sorted(metrics_by_system.items()):
                missing = [name for name in REQUIRED_METRIC_NAMES if name not in metrics]
                if missing:
                    violations.append(f"{system}: missing metrics {missing}")
                for name, value in metrics.items():
                    if "recall" in name.casefold() and "pooled" not in name.casefold():
                        violations.append(f"{system}: Recall metric is not explicitly pooled: {name}")
                    if name in REQUIRED_METRIC_NAMES and (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                    ):
                        violations.append(f"{system}: metric {name} is not finite numeric")
            label = metric_payload.get("evaluation_label") if isinstance(metric_payload, Mapping) else None
            if label != EVALUATION_LABEL:
                violations.append("overall metric artifact lacks the exact evaluation label")
            add_evaluation(
                _record(
                    "evaluation.metric_schema",
                    PASS if not violations else FAIL,
                    {
                        "systems": sorted(metrics_by_system),
                        "evaluation_label": label,
                        "violations": violations[:30],
                    },
                    {
                        "systems": ["bm25", "dense", "hybrid"],
                        "metrics": list(REQUIRED_METRIC_NAMES),
                        "evaluation_label": EVALUATION_LABEL,
                        "all_recall_is_pooled": True,
                    },
                    "All required finite overall metrics use explicit pooled-Recall names."
                    if not violations
                    else "The publishable overall metric artifact is incomplete or mislabeled.",
                    "Regenerate evaluation artifacts from validated runs and metric fixtures."
                    if violations
                    else "No remediation required.",
                )
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            add_evaluation(
                _record(
                    "evaluation.metric_schema",
                    FAIL,
                    _error(exc),
                    {
                        "systems": ["bm25", "dense", "hybrid"],
                        "metrics": list(REQUIRED_METRIC_NAMES),
                        "evaluation_label": EVALUATION_LABEL,
                    },
                    "The publishable overall metric artifact could not be validated.",
                    "Regenerate overall_metrics.json after all top-30 results are judged.",
                )
            )

        # Full evaluation artifacts must contain the complete query/system,
        # slice, CI, paired-comparison, and complementarity schemas.  File
        # existence alone is not evidence of a valid evaluation.
        try:
            if query_records is None or evaluation_qrels is None:
                raise ValueError("validated queries and effective qrels are required")
            systems = {"bm25", "dense", "hybrid"}
            from .bootstrap import (
                bootstrap_metric_confidence_intervals,
                required_pairwise_comparisons,
            )
            from .metrics import evaluate_run
            from .reporting import compute_complementarity
            from .slices import build_query_slices, evaluate_slices

            nested_qrels: dict[str, dict[str, int]] = defaultdict(dict)
            for qrel in evaluation_qrels:
                nested_qrels[qrel.query_id][qrel.chunk_id] = qrel.relevance
            expected_evaluations = {
                system: evaluate_run(valid_runs[system], nested_qrels)
                for system in sorted(systems)
            }
            expected_query_pairs = {
                (query_id, system) for query_id in query_ids for system in systems
            }
            artifact_violations: list[str] = []

            def compare_float(label: str, observed: Any, expected: Any) -> None:
                try:
                    observed_number = float(observed)
                    expected_number = float(expected)
                except (TypeError, ValueError):
                    artifact_violations.append(f"{label} is not numeric")
                    return
                if (
                    not math.isfinite(observed_number)
                    or not math.isfinite(expected_number)
                    or not math.isclose(
                        observed_number,
                        expected_number,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                ):
                    artifact_violations.append(
                        f"{label} differs from recomputation: observed={observed!r}, expected={expected!r}"
                    )

            for system in sorted(systems):
                observed_metrics = metrics_by_system.get(system, {})
                expected_metrics = expected_evaluations[system]["overall"]
                for metric in REQUIRED_METRIC_NAMES:
                    compare_float(
                        f"overall {system}/{metric}",
                        observed_metrics.get(metric),
                        expected_metrics[metric],
                    )

            per_query_rows: list[dict[str, str]] = []
            with (paths.evaluation / "per_query_metrics.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                per_query_rows = list(csv.DictReader(handle))
            observed_query_pairs = {
                (row.get("query_id", ""), row.get("retriever", ""))
                for row in per_query_rows
            }
            if observed_query_pairs != expected_query_pairs or len(per_query_rows) != len(expected_query_pairs):
                artifact_violations.append("per-query CSV does not contain each query/system exactly once")
            for row in per_query_rows:
                for metric in REQUIRED_METRIC_NAMES:
                    try:
                        value = float(row[metric])
                        if not math.isfinite(value):
                            raise ValueError
                    except (KeyError, TypeError, ValueError):
                        artifact_violations.append(f"invalid per-query metric {metric}")
                        break
                    expected_system = expected_evaluations.get(row.get("retriever", ""))
                    expected_value = (
                        expected_system["per_query"]
                        .get(row.get("query_id", ""), {})
                        .get(metric)
                        if expected_system is not None
                        else None
                    )
                    compare_float(
                        f"per-query {row.get('retriever')}/{row.get('query_id')}/{metric}",
                        row.get(metric),
                        expected_value,
                    )

            expected_slice_pairs = {
                (item.slice_name, item.slice_value, system)
                for item in build_query_slices(query_records)
                for system in systems
            }
            with (paths.evaluation / "slice_metrics.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                slice_rows = list(csv.DictReader(handle))
            observed_slice_pairs = {
                (row.get("slice_name", ""), row.get("slice_value", ""), row.get("retriever", ""))
                for row in slice_rows
            }
            if observed_slice_pairs != expected_slice_pairs or len(slice_rows) != len(expected_slice_pairs):
                artifact_violations.append("slice CSV does not contain every required slice/system exactly once")
            required_slice_columns = {
                "slice_name", "slice_value", "source_field", "query_count", "retriever", *REQUIRED_METRIC_NAMES
            }
            if slice_rows and not required_slice_columns.issubset(slice_rows[0]):
                artifact_violations.append("slice CSV is missing required metric columns")
            expected_slice_rows: dict[tuple[str, str, str], Mapping[str, Any]] = {}
            for system in sorted(systems):
                for row in evaluate_slices(
                    valid_runs[system], nested_qrels, query_records, retriever=system
                ):
                    expected_slice_rows[
                        (row["slice_name"], row["slice_value"], row["retriever"])
                    ] = row
            for row in slice_rows:
                key = (
                    row.get("slice_name", ""),
                    row.get("slice_value", ""),
                    row.get("retriever", ""),
                )
                expected_row = expected_slice_rows.get(key)
                if expected_row is None:
                    continue
                if row.get("source_field", "") != str(expected_row.get("source_field") or ""):
                    artifact_violations.append(f"slice {key} source_field differs from recomputation")
                try:
                    observed_count = int(row.get("query_count", ""))
                except (TypeError, ValueError):
                    observed_count = -1
                if observed_count != expected_row["query_count"]:
                    artifact_violations.append(f"slice {key} query_count differs from recomputation")
                for metric in REQUIRED_METRIC_NAMES:
                    expected_value = expected_row[metric]
                    if expected_value is None:
                        if row.get(metric) not in (None, "", "None"):
                            artifact_violations.append(f"slice {key}/{metric} should be undefined")
                    else:
                        compare_float(f"slice {key}/{metric}", row.get(metric), expected_value)
                expected_warning = expected_row.get("estimate_warning")
                observed_warning = row.get("estimate_warning")
                if observed_warning not in (str(expected_warning), "" if expected_warning is None else None):
                    artifact_violations.append(f"slice {key} estimate_warning differs from recomputation")

            ci_payload = read_json(paths.evaluation / "confidence_intervals.json", None)
            if not isinstance(ci_payload, Mapping) or set(ci_payload) != systems:
                artifact_violations.append("confidence intervals must cover bm25, dense, and hybrid")
            else:
                expected_ci_payload = {
                    system: bootstrap_metric_confidence_intervals(
                        expected_evaluations[system]["per_query"],
                        n_samples=10_000,
                        seed=42,
                        confidence_level=0.95,
                    )
                    for system in sorted(systems)
                }
                for system in sorted(systems):
                    intervals = ci_payload.get(system)
                    if not isinstance(intervals, Mapping) or set(intervals) != set(PRIMARY_BOOTSTRAP_METRICS):
                        artifact_violations.append(f"{system} confidence intervals have wrong metrics")
                        continue
                    for metric, interval in intervals.items():
                        if not isinstance(interval, Mapping):
                            artifact_violations.append(f"{system}/{metric} CI is not an object")
                            continue
                        if (
                            interval.get("bootstrap_samples") != 10_000
                            or interval.get("random_seed") != 42
                            or interval.get("confidence_level") != 0.95
                            or interval.get("query_count") != EXPECTED_QUERY_COUNT
                        ):
                            artifact_violations.append(f"{system}/{metric} CI metadata is invalid")
                        for field in ("mean", "ci95_lower", "ci95_upper"):
                            value = interval.get(field)
                            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                                artifact_violations.append(f"{system}/{metric} CI field {field} is invalid")
                            else:
                                compare_float(
                                    f"CI {system}/{metric}/{field}",
                                    value,
                                    expected_ci_payload[system][metric][field],
                                )

            pairwise = read_json(paths.evaluation / "pairwise_differences.json", None)
            expected_comparisons = {
                (left, right, metric)
                for left, right in (("dense", "bm25"), ("hybrid", "bm25"), ("hybrid", "dense"))
                for metric in PRIMARY_BOOTSTRAP_METRICS
            }
            if not isinstance(pairwise, list):
                artifact_violations.append("paired comparisons must be a list")
            else:
                expected_pairwise_rows = required_pairwise_comparisons(
                    {
                        system: expected_evaluations[system]["per_query"]
                        for system in sorted(systems)
                    },
                    n_samples=10_000,
                    seed=42,
                    confidence_level=0.95,
                )
                expected_pairwise = {
                    (row["system_a"], row["system_b"], row["metric"]): row
                    for row in expected_pairwise_rows
                }
                observed_comparisons = {
                    (row.get("system_a"), row.get("system_b"), row.get("metric"))
                    for row in pairwise if isinstance(row, Mapping)
                }
                if len(pairwise) != 12 or observed_comparisons != expected_comparisons:
                    artifact_violations.append("paired comparisons must contain the required 3x4 rows")
                for row in pairwise:
                    if not isinstance(row, Mapping) or (
                        row.get("bootstrap_samples") != 10_000
                        or row.get("random_seed") != 42
                        or row.get("confidence_level") != 0.95
                        or row.get("query_count") != EXPECTED_QUERY_COUNT
                    ):
                        artifact_violations.append("paired-comparison metadata is invalid")
                        break
                    counts = [
                        row.get("queries_a_wins"),
                        row.get("queries_b_wins"),
                        row.get("ties"),
                    ]
                    if any(
                        isinstance(value, bool) or not isinstance(value, int) or value < 0
                        for value in counts
                    ) or sum(counts) != EXPECTED_QUERY_COUNT:
                        artifact_violations.append(
                            "paired-comparison wins, losses, and ties must partition every query"
                        )
                    for field in ("mean_difference", "ci95_lower", "ci95_upper"):
                        value = row.get(field)
                        if (
                            isinstance(value, bool)
                            or not isinstance(value, (int, float))
                            or not math.isfinite(float(value))
                        ):
                            artifact_violations.append(
                                f"paired-comparison field {field} is not finite numeric"
                            )
                    lower = row.get("ci95_lower")
                    upper = row.get("ci95_upper")
                    if isinstance(lower, (int, float)) and isinstance(upper, (int, float)) and lower > upper:
                        artifact_violations.append("paired-comparison CI lower bound exceeds upper bound")
                    key = (row.get("system_a"), row.get("system_b"), row.get("metric"))
                    expected_row = expected_pairwise.get(key)
                    if expected_row is not None:
                        for field in (
                            "mean_difference",
                            "ci95_lower",
                            "ci95_upper",
                        ):
                            compare_float(
                                f"paired comparison {key}/{field}",
                                row.get(field),
                                expected_row[field],
                            )
                        for field in ("queries_a_wins", "queries_b_wins", "ties"):
                            if row.get(field) != expected_row[field]:
                                artifact_violations.append(
                                    f"paired comparison {key}/{field} differs from recomputation"
                                )

            complementarity = read_json(paths.evaluation / "complementarity_report.json", None)
            complementarity_fields = {
                "evaluation_label",
                "relevance_definition",
                "query_count",
                "BM25_only_relevance_2_query_hits_at_20",
                "Dense_only_relevance_2_query_hits_at_20",
                "queries_hit_by_both_at_20",
                "queries_missed_by_both_at_20",
                "mean_Jaccard@10",
                "mean_Jaccard@20",
                "median_Jaccard@10",
                "median_Jaccard@20",
                "oracle_union_HitRate@5",
                "oracle_union_HitRate@10",
                "oracle_union_HitRate@20",
                "BM25_HitRate@20_rel2",
                "Dense_HitRate@20_rel2",
                "oracle_union_HitRate@20_delta_over_best_single",
                "unique_relevance_2_chunks_only_BM25",
                "unique_relevance_2_chunks_only_dense",
                "unique_relevance_2_chunks_found_by_both",
                "diagnostic_target_status",
                "diagnostic_targets",
                "diagnostic_investigation",
            }
            if (
                not isinstance(complementarity, Mapping)
                or not complementarity_fields.issubset(complementarity)
                or complementarity.get("evaluation_label") != EVALUATION_LABEL
                or complementarity.get("query_count") != EXPECTED_QUERY_COUNT
            ):
                artifact_violations.append("complementarity report is incomplete or mislabeled")
            elif isinstance(complementarity, Mapping):
                expected_complementarity = compute_complementarity(
                    valid_runs["bm25"], valid_runs["dense"], nested_qrels
                )
                partition_fields = (
                    "BM25_only_relevance_2_query_hits_at_20",
                    "Dense_only_relevance_2_query_hits_at_20",
                    "queries_hit_by_both_at_20",
                    "queries_missed_by_both_at_20",
                )
                partition_values = [complementarity.get(field) for field in partition_fields]
                if any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                    for value in partition_values
                ) or sum(partition_values) != EXPECTED_QUERY_COUNT:
                    artifact_violations.append(
                        "complementarity query-hit partition must be non-negative integers summing to query_count"
                    )
                rate_fields = (
                    "mean_Jaccard@10",
                    "mean_Jaccard@20",
                    "median_Jaccard@10",
                    "median_Jaccard@20",
                    "oracle_union_HitRate@5",
                    "oracle_union_HitRate@10",
                    "oracle_union_HitRate@20",
                    "BM25_HitRate@20_rel2",
                    "Dense_HitRate@20_rel2",
                    "oracle_union_HitRate@20_delta_over_best_single",
                )
                for field in rate_fields:
                    value = complementarity.get(field)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        or not 0.0 <= float(value) <= 1.0
                    ):
                        artifact_violations.append(f"complementarity field {field} is outside [0, 1]")
                for field in (
                    "unique_relevance_2_chunks_only_BM25",
                    "unique_relevance_2_chunks_only_dense",
                    "unique_relevance_2_chunks_found_by_both",
                ):
                    value = complementarity.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        artifact_violations.append(
                            f"complementarity field {field} is not a non-negative integer"
                        )
                for field, expected_value in expected_complementarity.items():
                    observed_value = complementarity.get(field)
                    if isinstance(expected_value, float):
                        compare_float(
                            f"complementarity/{field}", observed_value, expected_value
                        )
                    elif observed_value != expected_value:
                        artifact_violations.append(
                            f"complementarity/{field} differs from recomputation"
                        )

            add_evaluation(
                _record(
                    "evaluation.complete_artifact_schemas",
                    PASS if not artifact_violations else FAIL,
                    {
                        "per_query_row_count": len(per_query_rows),
                        "slice_row_count": len(slice_rows),
                        "pairwise_row_count": len(pairwise) if isinstance(pairwise, list) else None,
                        "violations": artifact_violations[:50],
                    },
                    {
                        "per_query_row_count": EXPECTED_QUERY_COUNT * 3,
                        "required_slice_system_pairs": len(expected_slice_pairs),
                        "pairwise_row_count": 12,
                        "violations": [],
                    },
                    "All complete-evaluation artifacts contain the required schemas and coverage."
                    if not artifact_violations
                    else "One or more complete-evaluation artifacts are empty, incomplete, or malformed.",
                    "Regenerate all metric, slice, CI, pairwise, and complementarity artifacts from the effective qrels."
                    if artifact_violations
                    else "No remediation required.",
                )
            )
        except (OSError, UnicodeError, ValueError, csv.Error, json.JSONDecodeError) as exc:
            add_evaluation(
                _record(
                    "evaluation.complete_artifact_schemas",
                    FAIL,
                    _error(exc),
                    "Complete non-empty metric, slice, CI, pairwise, and complementarity artifacts.",
                    "Complete evaluation artifacts could not be validated.",
                    "Regenerate the evaluation bundle from current formal runs and effective qrels.",
                )
            )

    engineering_status = (
        FAIL if any(record["status"] != PASS for record in engineering_checks) else PASS
    )
    if any(record["status"] == FAIL for record in evaluation_checks):
        evaluation_integrity_status = FAIL
    elif any(record["status"] == BLOCKED for record in evaluation_checks):
        evaluation_integrity_status = BLOCKED
    else:
        evaluation_integrity_status = PASS

    retrieval_quality_status = "NOT_EVALUATED"
    quality_record: dict[str, Any] | None = None
    if evaluation_integrity_status == PASS and set(metrics_by_system) == {
        "bm25",
        "dense",
        "hybrid",
    }:
        try:
            bm25 = metrics_by_system["bm25"]
            dense = metrics_by_system["dense"]
            hybrid = metrics_by_system["hybrid"]
            ndcg_target = max(float(bm25["graded_nDCG@10"]), float(dense["graded_nDCG@10"])) + 0.01
            recall_floor = max(
                float(bm25["pooled_Recall@10_rel2"]),
                float(dense["pooled_Recall@10_rel2"]),
            ) - 0.01
            hit_floor = max(
                float(bm25["HitRate@5_rel2"]), float(dense["HitRate@5_rel2"])
            ) - 0.01
            try:
                failure_analysis = (paths.reports / "failure_analysis.md").read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                failure_analysis = ""
            regressions = _dialect_regressions(
                paths.evaluation / "slice_metrics.csv", failure_analysis
            )
            targets_ok = (
                float(hybrid["graded_nDCG@10"]) + 1e-12 >= ndcg_target
                and float(hybrid["pooled_Recall@10_rel2"]) + 1e-12 >= recall_floor
                and float(hybrid["HitRate@5_rel2"]) + 1e-12 >= hit_floor
                and not regressions
            )
            retrieval_quality_status = PASS if targets_ok else FAIL
            quality_record = _record(
                "quality.hybrid_targets",
                retrieval_quality_status,
                {
                    "hybrid_graded_nDCG@10": float(hybrid["graded_nDCG@10"]),
                    "hybrid_pooled_Recall@10_rel2": float(hybrid["pooled_Recall@10_rel2"]),
                    "hybrid_HitRate@5_rel2": float(hybrid["HitRate@5_rel2"]),
                    "unexplained_dialect_regressions_over_0.05": regressions,
                },
                {
                    "graded_nDCG@10_minimum": ndcg_target,
                    "pooled_Recall@10_rel2_minimum": recall_floor,
                    "HitRate@5_rel2_minimum": hit_floor,
                    "unexplained_dialect_regressions_over_0.05": [],
                },
                "The fixed hybrid meets all declared retrieval-quality targets."
                if targets_ok
                else "The fixed hybrid misses one or more declared quality targets.",
                "Report the measured failure and analyze affected queries; do not tune on this snapshot."
                if not targets_ok
                else "No remediation required.",
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            retrieval_quality_status = FAIL
            quality_record = _record(
                "quality.hybrid_targets",
                FAIL,
                _error(exc),
                "Finite BM25, dense, and hybrid quality-target metrics.",
                "Retrieval-quality targets could not be evaluated from the metric artifact.",
                "Regenerate the required overall metrics without changing the fixed baselines.",
            )

    if quality_record is not None:
        checks.append(quality_record)

    if release_manifest_payload is not None:
        try:
            reproduction_payload = read_json(
                paths.reproduction / "reproduction_report.json", None
            )
            expected_annotation_status = (
                reproduction_payload.get("annotation_reproduction_status")
                if isinstance(reproduction_payload, Mapping)
                else None
            )
            expected_statuses = {
                "engineering_status": engineering_status,
                "evaluation_integrity_status": evaluation_integrity_status,
                "retrieval_quality_status": retrieval_quality_status,
                "annotation_reproduction_status": expected_annotation_status,
            }
            recorded_statuses = {
                key: release_manifest_payload.get(key) for key in expected_statuses
            }
            status_binding_ok = recorded_statuses == expected_statuses
            add_engineering(
                _record(
                    "engineering.manifest.final_status_binding",
                    PASS if status_binding_ok else FAIL,
                    recorded_statuses,
                    expected_statuses,
                    "Manifest statuses exactly match this validation pass and the reproduction evidence."
                    if status_binding_ok
                    else "Manifest statuses are stale or disagree with this validation pass.",
                    "Regenerate reports and manifest from the returned validation statuses, then validate again."
                    if not status_binding_ok
                    else "No remediation required.",
                )
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            add_engineering(
                _record(
                    "engineering.manifest.final_status_binding",
                    FAIL,
                    _error(exc),
                    "Manifest statuses matching the final validation and reproduction report.",
                    "Final manifest status binding could not be checked.",
                    "Regenerate the final manifest and rerun validation.",
                )
            )
        engineering_status = (
            FAIL
            if any(record["status"] != PASS for record in engineering_checks)
            else PASS
        )

    # Per section 30, quality FAIL is an honest measured outcome and does not
    # invalidate an otherwise complete engineering/integrity release.
    overall_success = engineering_status == PASS and evaluation_integrity_status == PASS
    report = {
        "schema_version": "sqlmend-validation-v1",
        "evaluation_label": EVALUATION_LABEL,
        "machine_proposed_development_only": True,
        "engineering_status": engineering_status,
        "evaluation_integrity_status": evaluation_integrity_status,
        "retrieval_quality_status": retrieval_quality_status,
        "overall_success": overall_success,
        "checks": checks,
    }
    # JSON object keys are strings.  Round-trip once before writing so callers
    # receive exactly the same data contract that is persisted (notably for
    # relevance-label count mappings whose natural in-memory keys are ints).
    report = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    write_json(paths.reports / "validation_report.json", report)
    return report


__all__ = [
    "BLOCKED",
    "CHECK_FIELDS",
    "CHECK_STATUSES",
    "EXPECTED_CORPUS_DIALECT_COUNTS",
    "EXPECTED_DIALECT_SENSITIVE_QUERY_COUNT",
    "EXPECTED_QUERY_COUNT",
    "EXPECTED_QUERY_DIALECT_COUNTS",
    "EXPECTED_QREL_COUNT",
    "EXPECTED_QREL_LABEL_COUNTS",
    "EXPECTED_RESULTS_PER_QUERY",
    "EXPECTED_VERSION_SENSITIVE_QUERY_COUNT",
    "FAIL",
    "PASS",
    "REQUIRED_COMPLETE_EVALUATION_FILES",
    "REQUIRED_ENGINEERING_FILES",
    "REQUIRED_REPORT_FILES",
    "validate_release",
]
