from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from sqlmend_pipeline.cli import main
from sqlmend_pipeline.constants import ALLOWED_DIALECTS, REQUIRED_CORPUS_FIELDS
from sqlmend_pipeline.manifest import ManifestError, load_source_manifest, public_source_record, source_index
from sqlmend_pipeline.statistics import calculate_statistics
from sqlmend_pipeline.utils import write_jsonl_atomic
from sqlmend_pipeline.validation import MOJIBAKE_RE, validate_corpus


def test_mojibake_detector_allows_literal_a_tilde_but_catches_utf8_damage() -> None:
    assert not MOJIBAKE_RE.search('The tokenizer distinguishes "Ã" from "ã".')
    assert MOJIBAKE_RE.search("cafÃ©")


def _source(source_id: str, dialect: str) -> dict:
    return {
        "id": source_id,
        "source_name": f"{dialect} documentation",
        "source_type": "official_docs",
        "vendor_or_project": f"{dialect} project",
        "dialect": dialect,
        "base_url": f"https://example.test/{dialect}/",
        "retrieved_at": "2026-08-27T00:00:00Z",
        "license_or_terms_note": "Freely redistributable project documentation.",
        "collector_name": "test_collector",
        "collector": {"type": "single", "url": f"https://example.test/{dialect}/manual.md"},
        "authority_class": "official_project_documentation",
        "version": "1.2",
        "version_min": "1.2",
        "version_max": "1.2",
        "version_status": "exact",
    }


def _write_repo_config(root: Path, *, minimum_chunks: int = 1) -> None:
    (root / "config").mkdir(parents=True)
    (root / "reports").mkdir(parents=True)
    (root / "data" / "processed").mkdir(parents=True)
    manifest = {
        "manifest_version": 1,
        "sources": [_source(f"{dialect}_docs", dialect) for dialect in ALLOWED_DIALECTS],
    }
    (root / "config" / "sources.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    config = {
        "validation": {
            "minimum_chunks": minimum_chunks,
            "minimum_total_words": 1,
            "minimum_chunks_per_dialect": 1,
            "maximum_single_dialect_share": 0.35,
            "minimum_version_known_ratio": 0.90,
            "minimum_chunk_words": 1,
            "near_duplicate_threshold": 0.94,
            "maximum_residual_near_duplicate_rate": 0.03,
        }
    }
    (root / "config" / "chunking.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )


def test_statistics_calculation_covers_counts_percentages_and_duplicates(chunk_factory) -> None:
    chunks = [
        chunk_factory(1, dialect="postgresql", contains_error_code=True, topic="errors"),
        chunk_factory(
            2,
            dialect="mysql",
            version="8.0-8.4",
            version_min="8.0",
            version_max="8.4",
            version_status="range",
            contains_sql=False,
            contains_version_or_compatibility=True,
            topic="migration",
        ),
    ]
    raw = [{"document_id": "raw-1"}, {"document_id": "raw-2"}, {"document_id": "raw-3"}]

    stats = calculate_statistics(
        chunks,
        raw_documents=raw,
        cleaned_document_count=2,
        collection_report={"failed_source_count": 1, "failed_url_count": 2, "inaccessible_source_count": 1},
        document_duplicate_report={"exact_duplicate_count": 2, "near_duplicate_count": 3},
        chunk_duplicate_report={"exact_duplicate_count": 4, "near_duplicate_count": 5},
    )

    assert stats["raw_document_count"] == 3
    assert stats["cleaned_document_count"] == 2
    assert stats["final_chunk_count"] == 2
    assert stats["total_word_count"] > 0
    assert stats["minimum_chunk_word_count"] <= stats["median_chunk_word_count"] <= stats["maximum_chunk_word_count"]
    assert stats["chunks_per_dialect"]["postgresql"] == 1
    assert stats["chunks_per_dialect"]["mysql"] == 1
    assert stats["documents_per_dialect"]["duckdb"] == 0
    assert stats["version_known_percentage"] == 100.0
    assert stats["dialect_known_percentage"] == 100.0
    assert stats["sql_chunk_percentage"] == 50.0
    assert stats["error_chunk_percentage"] == 50.0
    assert stats["version_or_compatibility_chunk_percentage"] == 50.0
    assert stats["exact_duplicate_count_removed"] == 6
    assert stats["near_duplicate_count_removed"] == 8
    assert stats["failed_url_count"] == 2


def test_manifest_accepts_complete_sources_and_exposes_public_record(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    source = _source("sqlite_docs", "sqlite")
    path.write_text(yaml.safe_dump({"sources": [source]}, sort_keys=False), encoding="utf-8")

    manifest = load_source_manifest(path)
    indexed = source_index(manifest)
    public = public_source_record(indexed["sqlite_docs"])

    assert public["source_id"] == "sqlite_docs"
    assert public["dialect"] == "sqlite"
    assert public["collector"] == "test_collector"
    assert public["authority_class"] == "official_project_documentation"
    assert public["license_or_terms_note"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda sources: sources.append(dict(sources[0])),
        lambda sources: sources[0].update(dialect="oracle"),
        lambda sources: sources[0].pop("license_or_terms_note"),
        lambda sources: sources[0].update(authority_class="official_vendorish_docs"),
        lambda sources: sources[0].update(collector={"type": "unsupported"}),
    ],
)
def test_manifest_rejects_inconsistent_entries(tmp_path: Path, mutation) -> None:
    sources = [_source("postgresql_docs", "postgresql")]
    mutation(sources)
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": sources}, sort_keys=False), encoding="utf-8")

    with pytest.raises(ManifestError, match="Invalid source manifest"):
        load_source_manifest(path)


def test_validation_passes_a_complete_balanced_synthetic_corpus(
    tmp_path: Path, chunk_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_repo_config(tmp_path, minimum_chunks=100)
    chunks = []
    for dialect_index, dialect in enumerate(ALLOWED_DIALECTS):
        for local_index in range(20):
            global_index = dialect_index * 20 + local_index
            text = (
                f"Title: {dialect} reference {global_index}\n\n"
                f"This uniquely numbered passage {global_index} for {dialect} explains SELECT behavior and "
                f"predicate_{global_index} ordering_{global_index} grouping_{global_index} semantics. "
                "It preserves syntax examples, version context, result behavior, and compatibility notes for retrieval."
            )
            chunks.append(chunk_factory(global_index, dialect=dialect, text=text))
    write_jsonl_atomic(tmp_path / "data" / "processed" / "corpus.jsonl", chunks)
    write_jsonl_atomic(
        tmp_path / "reports" / "inspection_sample.jsonl",
        [{"chunk_id": chunk["chunk_id"]} for chunk in chunks],
    )
    monkeypatch.setattr("sqlmend_pipeline.validation.scan_secrets", lambda root: [])

    report = validate_corpus(tmp_path)

    assert report["status"] == "PASS", {
        check["check"]: check["observed"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert report["critical_failures"] == 0
    assert all(check.keys() >= {"check", "status", "observed", "required", "explanation", "recommended_remediation"} for check in report["checks"])


def test_validation_reports_schema_and_manifest_failures_without_crashing(
    tmp_path: Path, chunk_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_repo_config(tmp_path)
    malformed = chunk_factory(1, source_id="not_in_manifest")
    malformed.pop("vendor_or_project")
    write_jsonl_atomic(tmp_path / "data" / "processed" / "corpus.jsonl", [malformed])
    monkeypatch.setattr("sqlmend_pipeline.validation.scan_secrets", lambda root: [])

    report = validate_corpus(tmp_path)
    checks = {check["check"]: check for check in report["checks"]}

    assert report["status"] == "FAIL"
    assert checks["required_fields_exist"]["status"] == "FAIL"
    assert checks["source_manifest_consistency"]["status"] == "FAIL"
    assert (tmp_path / "reports" / "validation_report.json").exists()


def test_validation_handles_deeply_incomplete_chunk_as_a_fail_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_repo_config(tmp_path)
    write_jsonl_atomic(tmp_path / "data" / "processed" / "corpus.jsonl", [{"chunk_id": "broken"}])
    monkeypatch.setattr("sqlmend_pipeline.validation.scan_secrets", lambda root: [])

    report = validate_corpus(tmp_path)

    assert report["status"] == "FAIL"
    assert next(check for check in report["checks"] if check["check"] == "required_fields_exist")["status"] == "FAIL"
    assert json.loads((tmp_path / "reports" / "validation_report.json").read_text(encoding="utf-8"))["status"] == "FAIL"


def test_cli_returns_nonzero_for_missing_corpus_and_writes_report(tmp_path: Path) -> None:
    _write_repo_config(tmp_path)

    exit_code = main(["--root", str(tmp_path), "validate"])

    assert exit_code == 1
    report_path = tmp_path / "reports" / "validation_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert report["checks"][0]["check"] == "jsonl_parseable"
    assert report["checks"][0]["status"] == "FAIL"


def test_required_schema_declares_all_acceptance_fields() -> None:
    assert REQUIRED_CORPUS_FIELDS == (
        "chunk_id",
        "document_id",
        "dialect",
        "vendor_or_project",
        "version",
        "version_min",
        "version_max",
        "version_status",
        "source_type",
        "source_name",
        "source_url",
        "title",
        "section",
        "text",
        "contains_sql",
        "contains_error_code",
        "retrieved_at",
        "content_hash",
    )
