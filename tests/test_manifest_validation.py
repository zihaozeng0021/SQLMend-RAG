from __future__ import annotations

import json
from pathlib import Path

import yaml

from sqlmend_pipeline.cli import main
from sqlmend_pipeline.manifest import ManifestError, load_source_manifest
from sqlmend_pipeline.validation import validate_corpus


def valid_source() -> dict:
    return {
        "id": "test_source",
        "source_name": "Test official docs",
        "source_type": "official_docs",
        "vendor_or_project": "Test project",
        "dialect": "sqlite",
        "base_url": "https://example.test/docs/",
        "retrieved_at": "2026-08-27T00:00:00Z",
        "license_or_terms_note": "Public test fixture.",
        "authority_class": "official_project_documentation",
        "version": "1.0",
        "version_status": "exact",
        "collector": {"type": "single", "url": "https://example.test/docs/index.html"},
    }


def write_project_config(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "config" / "sources.yaml").write_text(
        yaml.safe_dump({"sources": [valid_source()]}, sort_keys=False), encoding="utf-8"
    )
    (root / "config" / "chunking.yaml").write_text(
        yaml.safe_dump(
            {
                "validation": {
                    "minimum_chunks": 1,
                    "minimum_total_words": 1,
                    "minimum_chunks_per_dialect": 0,
                    "maximum_single_dialect_share": 1.0,
                    "minimum_version_known_ratio": 0.0,
                    "minimum_chunk_words": 1,
                    "maximum_residual_near_duplicate_rate": 1.0,
                }
            }
        ),
        encoding="utf-8",
    )


def test_repository_source_manifest_is_valid_and_has_all_five_dialects() -> None:
    manifest = load_source_manifest("config/sources.yaml")
    assert {source["dialect"] for source in manifest["sources"]} == {
        "postgresql",
        "mysql",
        "sqlite",
        "mariadb",
        "duckdb",
    }
    assert all(source["authority_class"] != "community_documentation" for source in manifest["sources"])


def test_manifest_rejects_invalid_dialect(tmp_path: Path) -> None:
    source = valid_source()
    source["dialect"] = "oracle"
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": [source]}), encoding="utf-8")
    import pytest

    with pytest.raises(ManifestError):
        load_source_manifest(path)


def test_validation_handles_malformed_schema_and_writes_failure_report(tmp_path: Path) -> None:
    write_project_config(tmp_path)
    corpus = tmp_path / "data" / "processed" / "corpus.jsonl"
    corpus.parent.mkdir(parents=True)
    corpus.write_text(json.dumps({"chunk_id": "broken", "dialect": "sqlite"}) + "\n", encoding="utf-8")
    report = validate_corpus(tmp_path)
    assert report["status"] == "FAIL"
    assert (tmp_path / "reports" / "validation_report.json").exists()
    required = next(check for check in report["checks"] if check["check"] == "required_fields_exist")
    assert required["status"] == "FAIL"


def test_validation_cli_returns_nonzero_on_critical_failure(tmp_path: Path) -> None:
    write_project_config(tmp_path)
    corpus = tmp_path / "data" / "processed" / "corpus.jsonl"
    corpus.parent.mkdir(parents=True)
    corpus.write_text("not json\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "validate"]) == 1


def test_source_manifest_consistency_failure_is_reported(tmp_path: Path) -> None:
    write_project_config(tmp_path)
    corpus = tmp_path / "data" / "processed" / "corpus.jsonl"
    corpus.parent.mkdir(parents=True)
    row = {
        "chunk_id": "c1",
        "document_id": "d1",
        "dialect": "sqlite",
        "vendor_or_project": "SQLite",
        "version": "1.0",
        "version_min": "1.0",
        "version_max": "1.0",
        "version_status": "exact",
        "source_type": "official_docs",
        "source_name": "Unknown",
        "source_url": "https://example.test/unknown",
        "title": "Title",
        "section": "Section",
        "text": "Title: Title\n\nA coherent SQL SELECT example with enough words for this small fixture.",
        "contains_sql": True,
        "contains_error_code": False,
        "retrieved_at": "2026-08-27T00:00:00Z",
        "content_hash": "abc",
        "source_id": "missing_source",
        "topic": "syntax",
    }
    corpus.write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = validate_corpus(tmp_path)
    check = next(item for item in report["checks"] if item["check"] == "source_manifest_consistency")
    assert check["status"] == "FAIL"

