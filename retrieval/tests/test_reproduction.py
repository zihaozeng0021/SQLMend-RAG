from __future__ import annotations

import hashlib
import io

import pytest

from sqlmend_retrieval.reproduction import (
    _download_and_verify_model,
    compare_rankings,
)


def test_ranking_comparison_reports_exact_and_out_of_pool_evidence():
    reproduced = {"Q1": ["c1", "c2", "c3"], "Q2": ["c3", "c2", "c1"]}
    stored = {"Q1": ["c1", "c2", "c3"], "Q2": ["c3", "c1", "c2"]}
    pool = {("Q1", chunk) for chunk in reproduced["Q1"]} | {
        ("Q2", "c3"),
        ("Q2", "c1"),
    }

    result = compare_rankings(reproduced, stored, pool, {"c1", "c2", "c3"})

    assert result["query_count"] == 2
    assert result["exact_top30_sequence_match_count"] == 1
    assert result["exact_top30_set_match_count"] == 2
    assert result["out_of_pool_query_chunk_pair_count"] == 1
    assert result["missing_stored_documents"] == 0


def test_historical_model_download_uses_plain_hash_verified_files(tmp_path, monkeypatch):
    files = {"config.json": b"config", "model.onnx": b"model bytes"}
    provenance = {
        "resolved_repository": "owner/model",
        "resolved_revision": "abc123",
        "snapshot_file_count": len(files),
        "files": [
            {
                "path": f"snapshot/{name}",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in files.items()
        ],
    }
    calls: list[str] = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        name = request.full_url.rsplit("/", 1)[-1]
        return io.BytesIO(files[name])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    snapshot = _download_and_verify_model(tmp_path, provenance)
    assert {path.name: path.read_bytes() for path in snapshot.iterdir()} == files
    assert len(calls) == 2

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("verified cached files must not redownload"),
    )
    assert _download_and_verify_model(tmp_path, provenance) == snapshot
