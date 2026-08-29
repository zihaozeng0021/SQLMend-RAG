from __future__ import annotations

import json
from pathlib import Path

import yaml

from sqlmend_retrieval.paths import ProjectPaths
from sqlmend_retrieval.reporting import (
    _contract_inventory,
    audit_annotation_retrievers,
    compute_complementarity,
    generate_failure_analysis,
    generate_manifest,
    generate_reports,
)
from sqlmend_retrieval.hashing import sha256_file
from sqlmend_retrieval.trec import TrecRunEntry


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=True), encoding="utf-8")


def test_failure_analysis_anchors_hybrid_harm_to_a_single_system_hit(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path)
    serialized = [
        {
            "query_id": "Q1",
            "serialized_text": "Dialect: sqlite\n\nQuestion:\nno retrieved evidence",
            "serialized_text_sha256": "q1-hash",
        },
        {
            "query_id": "Q2",
            "serialized_text": "Dialect: sqlite\n\nQuestion:\nwhy does foo() fail?",
            "serialized_text_sha256": "q2-hash",
        },
    ]
    paths.serialized_queries.parent.mkdir(parents=True, exist_ok=True)
    paths.serialized_queries.write_text(
        "".join(json.dumps(row) + "\n" for row in serialized), encoding="utf-8"
    )
    _write_json(
        paths.pool_expansion / "pool_expansion_summary.json",
        {
            "evaluation_integrity_status": "BLOCKED",
            "pool_expansion_record_count": 2,
            "unjudged_top30_occurrence_count": 4,
        },
    )
    queries = [
        {
            "query_id": "Q1",
            "dialect": "sqlite",
            "version": "3.45",
            "sql": "SELECT 1",
            "case_flags": {},
        },
        {
            "query_id": "Q2",
            "dialect": "sqlite",
            "version": "3.45",
            "sql": "SELECT foo()",
            "case_flags": {},
        },
    ]
    corpus = [
        {
            "chunk_id": "rel-q1",
            "dialect": "sqlite",
            "version": "3.45",
            "title": "Q1 evidence",
            "section": "Functions",
            "text": "Evidence never retrieved.",
        },
        {
            "chunk_id": "rel-q2",
            "dialect": "sqlite",
            "version": "3.45",
            "title": "foo function",
            "section": "Functions > foo",
            "text": "The foo() function is documented here.",
        },
        {
            "chunk_id": "other",
            "dialect": "mysql",
            "version": "8",
            "title": "Other",
            "section": "Other",
            "text": "An unrelated passage.",
        },
    ]
    runs = {
        "bm25": [
            TrecRunEntry("Q1", "other", 1, 1.0, "bm"),
            TrecRunEntry("Q2", "rel-q2", 1, 1.0, "bm"),
        ],
        "dense": [
            TrecRunEntry("Q1", "other", 1, 1.0, "de"),
            TrecRunEntry("Q2", "other", 1, 1.0, "de"),
        ],
        "hybrid": [
            TrecRunEntry("Q1", "other", 1, 1.0, "hy"),
            TrecRunEntry("Q2", "other", 1, 1.0, "hy"),
        ],
    }
    qrels = {"Q1": {"rel-q1": 2}, "Q2": {"rel-q2": 2}}

    generate_failure_analysis(paths, runs, qrels, queries, corpus)

    report = (paths.reports / "failure_analysis.md").read_text(encoding="utf-8")
    harm_section = report.split("### hybrid 损害排名", 1)[1].split("### dialect-sensitive", 1)[0]
    assert "实际可识别：1" in harm_section
    assert "案例：Q2" in harm_section
    assert "案例：Q1" not in harm_section
    assert "why does foo() fail?" in report
    assert "The foo() function is documented here." in report
    assert "component ranks BM25=`1` / dense=`None`" in report
    assert "NOT_PUBLISHED (BLOCKED)" in report
    assert "pooled Recall" in report


def test_manifest_never_names_engineering_failure_as_candidate(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path)
    paths.config.mkdir(parents=True)
    _write_yaml(
        paths.config / "dense_baseline.yaml",
        {"model_id": "model", "model_revision": "revision"},
    )
    _write_yaml(paths.config / "evaluation.yaml", {"random_seed": 42})
    paths.corpus.parent.mkdir(parents=True)
    paths.corpus.write_text("{}\n", encoding="utf-8")
    paths.queries.parent.mkdir(parents=True)
    paths.queries.write_text("{}\n", encoding="utf-8")
    paths.qrels_source.write_text("{}\n", encoding="utf-8")

    manifest = generate_manifest(
        paths,
        {
            "engineering_status": "FAIL",
            "evaluation_integrity_status": "BLOCKED",
            "retrieval_quality_status": "NOT_EVALUATED",
        },
    )

    assert manifest["release"] == "retrieval-baseline-v1-invalid"


def test_blocked_reports_use_candidate_not_completion_language(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path)
    _write_yaml(paths.config / "bm25_baseline.yaml", {"k1": 1.5, "b": 0.75})
    _write_yaml(
        paths.config / "dense_baseline.yaml",
        {"model_id": "intfloat/e5-base-v2", "model_revision": "fixed"},
    )
    _write_yaml(
        paths.config / "hybrid_rrf_baseline.yaml",
        {"rrf_k": 60, "fusion_depth": 30, "output_depth": 30},
    )
    _write_yaml(paths.config / "evaluation.yaml", {"random_seed": 42})
    _write_json(
        paths.pool_expansion / "pool_expansion_summary.json",
        {
            "evaluation_integrity_status": "BLOCKED",
            "pool_expansion_required": True,
            "pool_expansion_record_count": 7,
        },
    )
    statuses = {
        "engineering_status": "PASS",
        "evaluation_integrity_status": "BLOCKED",
        "retrieval_quality_status": "NOT_EVALUATED",
        "annotation_reproduction_status": "PARTIAL",
    }
    manifest = {
        "release": "retrieval-baseline-v1-candidate",
        "corpus_path": "construction/data/processed/corpus.jsonl",
        "query_path": "annotation/codex/dev_250.jsonl",
    }

    generate_reports(paths, statuses, manifest)

    baseline = (paths.reports / "baseline_report.md").read_text(encoding="utf-8")
    completion = (paths.reports / "completion_report.md").read_text(encoding="utf-8")
    assert baseline.startswith("# SQLMend-RAG 正式基线候选状态报告——尚未完成")
    assert completion.startswith("# 阶段 5–6 候选状态报告——尚未完成")
    assert "NOT_PUBLISHED (BLOCKED)" in baseline
    assert "pooled Recall" in baseline
    assert "pooled Recall" in completion
    assert "`evaluate` 写入 BLOCKED sentinel 并返回 0" in completion
    assert "python -m sqlmend_retrieval.cli test" in completion
    assert completion.index("audit-protected-paths --phase before") < completion.index(
        "verify-inputs"
    )
    assert completion.index("audit-annotation-retrievers") < completion.index(
        "build-bm25"
    )
    assert completion.index("audit-protected-paths --phase after") < completion.index(
        "finalize"
    )


def test_pool_incompleteness_suppresses_metrics_even_when_integrity_is_fail(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths(tmp_path)
    _write_yaml(paths.config / "bm25_baseline.yaml", {"k1": 1.5, "b": 0.75})
    _write_yaml(
        paths.config / "dense_baseline.yaml",
        {"model_id": "intfloat/e5-base-v2", "model_revision": "fixed"},
    )
    _write_yaml(paths.config / "hybrid_rrf_baseline.yaml", {"rrf_k": 60})
    _write_yaml(paths.config / "evaluation.yaml", {"random_seed": 42})
    _write_json(
        paths.pool_expansion / "pool_expansion_summary.json",
        {"pool_expansion_required": True, "evaluation_integrity_status": "BLOCKED"},
    )
    statuses = {
        "engineering_status": "FAIL",
        "evaluation_integrity_status": "FAIL",
        "retrieval_quality_status": "NOT_EVALUATED",
        "annotation_reproduction_status": "PARTIAL",
    }

    generate_reports(paths, statuses, {"release": "retrieval-baseline-v1-invalid"})

    baseline = (paths.reports / "baseline_report.md").read_text(encoding="utf-8")
    assert "NOT_PUBLISHED (BLOCKED)" in baseline
    assert "pooled Recall" in baseline


def test_contract_inventory_is_recursive_and_excludes_runtime_caches(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths(tmp_path)
    owned_files = {
        paths.retrieval / ".gitignore": "*.tmp\n",
        paths.retrieval / "nested" / "assets" / ".gitkeep": "",
        paths.retrieval / "src" / "package" / "nested" / "module.py": "value = 1\n",
        paths.retrieval / "indices" / "bm25" / ".gitkeep": "",
    }
    excluded_files = {
        paths.retrieval / ".pytest_cache" / "v" / "cache" / "nodeids": "[]\n",
        paths.retrieval / "src" / "package" / "__pycache__" / "module.pyc": "cache\n",
        paths.retrieval / "indices" / "dense" / "model_cache" / "vendor.bin": "model\n",
        paths.retrieval / "reproduction" / "model_cache" / "vendor.bin": "model\n",
        paths.retrieval / "reproduction" / "annotation_bm25_reproduced.trec": "old\n",
        paths.retrieval / "reproduction" / "annotation_dense_reproduced.trec": "old\n",
        paths.retrieval / "reproduction" / "annotation_hybrid_reproduced.trec": "old\n",
        paths.retrieval
        / "reproduction"
        / "annotation_hybrid_rrf_reproduced.trec": "old\n",
    }
    for path, content in {**owned_files, **excluded_files}.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    inventory = dict(_contract_inventory(paths, blocked=False))

    assert inventory[".gitignore"] == "CREATED"
    assert inventory["nested/assets/.gitkeep"] == "CREATED"
    assert inventory["src/package/nested/module.py"] == "CREATED"
    assert inventory["indices/bm25/.gitkeep"] == "CREATED"
    assert ".pytest_cache/v/cache/nodeids" not in inventory
    assert "src/package/__pycache__/module.pyc" not in inventory
    assert "indices/dense/model_cache/vendor.bin" not in inventory
    assert "reproduction/model_cache/vendor.bin" not in inventory
    assert "reproduction/annotation_bm25_reproduced.trec" not in inventory
    assert "reproduction/annotation_dense_reproduced.trec" not in inventory
    assert "reproduction/annotation_hybrid_reproduced.trec" not in inventory
    assert "reproduction/annotation_hybrid_rrf_reproduced.trec" not in inventory
    assert inventory["indices/dense/model_cache/"].startswith(
        "EXCLUDED_DOWNLOADED_MODEL_CACHE"
    )
    assert inventory["reproduction/model_cache/"].startswith(
        "EXCLUDED_DOWNLOADED_MODEL_CACHE"
    )
    assert inventory["**/__pycache__/ and *.py[co]"] == "EXCLUDED_BYTECODE_CACHE"
    assert inventory["**/.pytest_cache/"] == "EXCLUDED_TEST_CACHE"


def test_manifest_binds_complete_evaluation_and_human_reports_without_cycle(
    tmp_path: Path,
) -> None:
    paths = ProjectPaths(tmp_path)
    _write_yaml(paths.config / "bm25_baseline.yaml", {"k1": 1.5, "b": 0.75})
    _write_yaml(
        paths.config / "dense_baseline.yaml",
        {"model_id": "intfloat/e5-base-v2", "model_revision": "fixed"},
    )
    _write_yaml(
        paths.config / "hybrid_rrf_baseline.yaml",
        {"rrf_k": 60, "fusion_depth": 30, "output_depth": 30},
    )
    _write_yaml(paths.config / "evaluation.yaml", {"random_seed": 42})
    (paths.retrieval / "README.md").write_text("fixture\n", encoding="utf-8")

    evaluation_files = {
        "per_query_metrics_sha256": "per_query_metrics.csv",
        "slice_metrics_sha256": "slice_metrics.csv",
        "confidence_intervals_sha256": "confidence_intervals.json",
        "pairwise_differences_sha256": "pairwise_differences.json",
        "complementarity_report_sha256": "complementarity_report.json",
    }
    (paths.evaluation / "per_query_metrics.csv").parent.mkdir(
        parents=True, exist_ok=True
    )
    (paths.evaluation / "per_query_metrics.csv").write_text(
        "query_id,retriever\nQ1,bm25\n", encoding="utf-8"
    )
    (paths.evaluation / "slice_metrics.csv").write_text(
        "retriever,slice_name,slice_value,graded_nDCG@10\n"
        "bm25,dialect,sqlite,1.0\n",
        encoding="utf-8",
    )
    for filename in (
        "confidence_intervals.json",
        "pairwise_differences.json",
        "complementarity_report.json",
    ):
        _write_json(paths.evaluation / filename, {"fixture": filename})
    _write_json(paths.evaluation / "overall_metrics.json", {"status": "PUBLISHED"})

    paths.reports.mkdir(parents=True, exist_ok=True)
    (paths.reports / "failure_analysis.md").write_text(
        "# Failure\n\npooled Recall\n", encoding="utf-8"
    )
    (paths.reports / "provenance_audit.md").write_text(
        "# Provenance\n\npooled Recall\n", encoding="utf-8"
    )
    statuses = {
        "engineering_status": "PASS",
        "evaluation_integrity_status": "PASS",
        "retrieval_quality_status": "PASS",
        "annotation_reproduction_status": "NOT_REPRODUCIBLE",
    }

    preliminary = generate_manifest(paths, statuses)
    for field, filename in evaluation_files.items():
        assert preliminary[field] == sha256_file(paths.evaluation / filename)
    assert preliminary["baseline_report_sha256"] is None
    assert preliminary["completion_report_sha256"] is None

    generate_reports(paths, statuses, preliminary)
    report_files = {
        "baseline_report_sha256": "baseline_report.md",
        "failure_analysis_sha256": "failure_analysis.md",
        "provenance_audit_sha256": "provenance_audit.md",
        "completion_report_sha256": "completion_report.md",
    }
    first_report_bytes = {
        filename: (paths.reports / filename).read_bytes()
        for filename in report_files.values()
    }

    final_manifest = generate_manifest(paths, statuses)
    for field, filename in {**evaluation_files, **report_files}.items():
        base = paths.evaluation if field in evaluation_files else paths.reports
        assert final_manifest[field] == sha256_file(base / filename)

    # The final manifest is generated after reports.  Reports render selected
    # identity/status fields, not their own hash fields, so this second report
    # generation is byte-stable and does not create a self-reference cycle.
    generate_reports(paths, statuses, final_manifest)
    assert {
        filename: (paths.reports / filename).read_bytes()
        for filename in report_files.values()
    } == first_report_bytes
    stable_manifest = generate_manifest(paths, statuses)
    assert stable_manifest == final_manifest


def test_annotation_reproduction_cache_binds_all_three_run_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    paths = ProjectPaths(tmp_path)
    provenance = paths.annotation / "provenance"
    provenance.mkdir(parents=True)
    paths.corpus.parent.mkdir(parents=True)
    paths.corpus.write_text("corpus\n", encoding="utf-8")
    paths.queries.write_text("queries\n", encoding="utf-8")
    paths.candidate_pools.write_text("pools\n", encoding="utf-8")
    (provenance / "retrieval_runs.jsonl").write_text("runs\n", encoding="utf-8")
    _write_json(
        provenance / "retrieval_config.json",
        {"bm25": {}, "dense": {}, "pooling": "union"},
    )
    _write_json(
        provenance / "embedding_model.json",
        {
            "resolved_repository": "repo",
            "resolved_revision": "revision",
            "snapshot_manifest_sha256": "snapshot-hash",
        },
    )
    paths.reproduction.mkdir(parents=True)
    run_names = {
        "bm25": "bm25_annotation_reproduced.trec",
        "dense": "dense_annotation_reproduced.trec",
        "hybrid_rrf": "hybrid_annotation_reproduced.trec",
    }
    systems = {}
    for system, filename in run_names.items():
        run_path = paths.reproduction / filename
        run_path.write_text(f"{system}\n", encoding="utf-8")
        systems[system] = {
            "status": "PASS",
            "comparison_metrics": {},
            "reproduced_run_sha256": sha256_file(run_path),
        }
    # Cache keys bind the active package implementation, not a temp copy.
    from sqlmend_retrieval import reproduction

    inputs = {
        "implementation_sha256": sha256_file(Path(reproduction.__file__)),
        "corpus_sha256": sha256_file(paths.corpus),
        "queries_sha256": sha256_file(paths.queries),
        "stored_runs_sha256": sha256_file(provenance / "retrieval_runs.jsonl"),
        "candidate_pools_sha256": sha256_file(paths.candidate_pools),
        "retrieval_config_sha256": sha256_file(provenance / "retrieval_config.json"),
        "embedding_model_sha256": sha256_file(provenance / "embedding_model.json"),
        "snapshot_manifest_sha256": "snapshot-hash",
    }
    cached = {
        "schema_version": "test",
        "attempt_completed": True,
        "annotation_reproduction_status": "PARTIAL",
        "empirical_ranking_reproduction_status": "PASS",
        "provenance_completeness_status": "PARTIAL",
        "provenance_limitations": [],
        "historical_query_contains_annotation_only_fields": True,
        "historical_query_is_never_used_by_formal_baselines": True,
        "preflight_validation": {"status": "PASS"},
        "reproduction_runtime": {"python_version": "fixture"},
        "inputs": inputs,
        "systems": systems,
    }
    _write_json(paths.reproduction / "reproduction_report.json", cached)

    calls = 0

    def unexpected_recompute(_: ProjectPaths):
        nonlocal calls
        calls += 1
        return cached

    monkeypatch.setattr(reproduction, "reproduce_annotation_retrievers", unexpected_recompute)
    audit_annotation_retrievers(paths)
    assert calls == 0
    assert "pooled Recall" in (
        paths.reports / "provenance_audit.md"
    ).read_text(encoding="utf-8")

    (paths.reproduction / run_names["dense"]).write_text("tampered\n", encoding="utf-8")
    audit_annotation_retrievers(paths)
    assert calls == 1

    # NOT_REPRODUCIBLE is a completed system attempt only when no formal
    # reproduced run exists.  A leftover new-name TREC file invalidates cache.
    (paths.reproduction / run_names["dense"]).write_text("dense\n", encoding="utf-8")
    hybrid_path = paths.reproduction / run_names["hybrid_rrf"]
    hybrid_path.unlink()
    cached["systems"]["hybrid_rrf"] = {
        "status": "NOT_REPRODUCIBLE",
        "error": "fixture dependency unavailable",
    }
    _write_json(paths.reproduction / "reproduction_report.json", cached)
    audit_annotation_retrievers(paths)
    assert calls == 1

    hybrid_path.write_text("stale formal output\n", encoding="utf-8")
    audit_annotation_retrievers(paths)
    assert calls == 2


def test_annotation_reproduction_exception_removes_all_stale_new_name_runs(
    tmp_path: Path, monkeypatch
) -> None:
    paths = ProjectPaths(tmp_path)
    provenance = paths.annotation / "provenance"
    provenance.mkdir(parents=True)
    paths.corpus.parent.mkdir(parents=True)
    paths.corpus.write_text("corpus\n", encoding="utf-8")
    paths.queries.write_text("queries\n", encoding="utf-8")
    paths.candidate_pools.write_text("pools\n", encoding="utf-8")
    (provenance / "retrieval_runs.jsonl").write_text("runs\n", encoding="utf-8")
    _write_json(provenance / "retrieval_config.json", {"bm25": {}, "dense": {}})
    _write_json(
        provenance / "embedding_model.json",
        {"snapshot_manifest_sha256": "fixture"},
    )
    paths.reproduction.mkdir(parents=True)
    run_paths = [
        paths.reproduction / "bm25_annotation_reproduced.trec",
        paths.reproduction / "dense_annotation_reproduced.trec",
        paths.reproduction / "hybrid_annotation_reproduced.trec",
    ]
    for run_path in run_paths:
        run_path.write_text("stale\n", encoding="utf-8")

    from sqlmend_retrieval import reproduction

    monkeypatch.setattr(
        reproduction,
        "reproduce_annotation_retrievers",
        lambda _paths: (_ for _ in ()).throw(RuntimeError("fixture failure")),
    )
    result = audit_annotation_retrievers(paths)

    assert result["annotation_reproduction_status"] == "NOT_REPRODUCIBLE"
    assert all(not run_path.exists() for run_path in run_paths)


def test_complementarity_reports_diagnostic_targets_and_best_single_delta() -> None:
    bm25 = []
    dense = []
    qrels = {}
    for index in range(10):
        query_id = f"Q{index}"
        relevant = f"rel{index}"
        qrels[query_id] = {relevant: 2}
        bm25.append(
            TrecRunEntry(
                query_id,
                relevant if index < 5 else f"bm-other-{index}",
                1,
                1.0,
                "bm",
            )
        )
        dense.append(
            TrecRunEntry(
                query_id,
                relevant if index >= 5 else f"de-other-{index}",
                1,
                1.0,
                "de",
            )
        )

    result = compute_complementarity(bm25, dense, qrels)

    assert result["BM25_only_relevance_2_query_hits_at_20"] == 5
    assert result["Dense_only_relevance_2_query_hits_at_20"] == 5
    assert result["BM25_HitRate@20_rel2"] == 0.5
    assert result["Dense_HitRate@20_rel2"] == 0.5
    assert result["oracle_union_HitRate@20"] == 1.0
    assert result["oracle_union_HitRate@20_delta_over_best_single"] == 0.5
    assert result["diagnostic_target_status"] == "PASS"
