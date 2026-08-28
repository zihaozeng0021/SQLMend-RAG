from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("sqlmendrag_pooling", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pooling helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind completed retrieval artifacts to the exact cached FastEmbed snapshot. "
            "This is an idempotent compatibility step for a run started before the builder "
            "wrote embedding_model.json itself."
        )
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument(
        "--capture-stage",
        choices=("bootstrap_before_derived_refresh", "final_after_derived_refresh"),
        default="final_after_derived_refresh",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    annotation = root / "annotation" / "codex"
    provenance_dir = annotation / "provenance"
    builder_path = annotation / "scripts" / "build_candidate_pools.py"
    config_path = provenance_dir / "retrieval_config.json"
    metrics_path = annotation / "reports" / "retrieval_metrics.json"
    model_path = provenance_dir / "embedding_model.json"
    binding_path = provenance_dir / "retrieval_provenance_binding.json"
    corpus_path = root / "construction" / "data" / "processed" / "corpus.jsonl"
    cases_path = annotation / "dev_250.jsonl"
    completed_outputs = {
        "cases": cases_path,
        "pools": annotation / "candidate_pools.jsonl",
        "qrels": annotation / "qrels_machine_proposed.jsonl",
        "leakage": annotation / "query_source_leakage.jsonl",
        "runs": provenance_dir / "retrieval_runs.jsonl",
        "metrics": metrics_path,
        "config": config_path,
    }
    missing = [str(path) for path in completed_outputs.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Retrieval build is incomplete; missing: {missing}")
    expected_counts = {
        "cases": 250,
        "pools": 250,
        "runs": 250,
    }
    for name, expected in expected_counts.items():
        observed = count_jsonl(completed_outputs[name])
        if observed != expected:
            raise ValueError(f"{name}: expected {expected} JSONL rows, observed {observed}")

    helpers = load_module(builder_path)
    cases = load_jsonl(cases_path)
    pools = load_jsonl(completed_outputs["pools"])
    runs = load_jsonl(completed_outputs["runs"])
    case_ids = [str(case.get("query_id")) for case in cases]
    pool_ids = [str(pool.get("query_id")) for pool in pools]
    run_ids = [str(run.get("query_id")) for run in runs]
    if len(set(case_ids)) != 250 or pool_ids != case_ids or run_ids != case_ids:
        raise ValueError("Cases, pools, and retrieval runs must have the same ordered 250 query IDs")
    query_hash_rows = [
        {
            "query_id": case["query_id"],
            "query_text_hash": hashlib.sha256(
                helpers.query_text(case).encode("utf-8")
            ).hexdigest(),
        }
        for case in sorted(cases, key=lambda row: row["query_id"])
    ]
    pool_by_id = {pool["query_id"]: pool for pool in pools}
    for row in query_hash_rows:
        if pool_by_id[row["query_id"]].get("query_text_hash") != row["query_text_hash"]:
            raise ValueError(
                f"{row['query_id']}: candidate-pool query hash differs from current case"
            )
    corpus_count = count_jsonl(corpus_path)
    input_bindings = {
        "corpus_path": "construction/data/processed/corpus.jsonl",
        "corpus_sha256": helpers.sha256_file(corpus_path),
        "corpus_chunk_count": corpus_count,
        "cases_path": "annotation/codex/dev_250.jsonl",
        "cases_sha256": helpers.sha256_file(cases_path),
        "case_count": len(cases),
        "query_hash_set_sha256": sha256_json(query_hash_rows),
    }
    corpus_snapshot_path = provenance_dir / "corpus_snapshot.json"
    corpus_snapshot = load_json(corpus_snapshot_path)
    if (
        corpus_snapshot.get("path") != input_bindings["corpus_path"]
        or corpus_snapshot.get("sha256") != input_bindings["corpus_sha256"]
        or corpus_snapshot.get("chunk_count") != input_bindings["corpus_chunk_count"]
    ):
        raise ValueError("corpus_snapshot.json differs from the current retrieval corpus")
    config = load_json(config_path)
    dense = config.get("dense")
    if not isinstance(dense, dict):
        raise ValueError("retrieval_config.json has no dense configuration object")
    if dense.get("method") != "fastembed_neural_text_embedding_cosine":
        raise ValueError(f"Refusing to bind non-FastEmbed retrieval: {dense.get('method')!r}")
    if dense.get("top_k") != 30:
        raise ValueError(f"Refusing to bind unexpected dense top_k: {dense.get('top_k')!r}")
    requested_model = str(dense.get("model_name") or "")
    if not requested_model:
        raise ValueError("Dense retrieval config does not identify model_name")

    cache_dir = annotation / "work" / "model_cache"
    model_provenance = helpers.embedding_model_provenance(cache_dir, requested_model)
    existing_model = load_json(model_path) if model_path.is_file() else None
    if existing_model is not None and existing_model != model_provenance:
        raise ValueError("Existing embedding_model.json differs from the resolved cached snapshot")

    ranking_output_mtimes = {
        name: completed_outputs[name].stat().st_mtime
        for name in ("pools", "runs", "metrics", "config")
    }
    snapshot_files = [cache_dir / item["path"] for item in model_provenance["files"]]
    latest_snapshot_mtime = max(path.stat().st_mtime for path in snapshot_files)
    earliest_output_mtime = min(ranking_output_mtimes.values())
    if latest_snapshot_mtime > earliest_output_mtime:
        raise ValueError(
            "Cached model snapshot was modified after retrieval outputs began to be written; "
            "the completed ranking cannot be safely bound to this snapshot"
        )

    artifact_hashes_before = {
        name: helpers.sha256_file(path) for name, path in completed_outputs.items()
    }
    for field in (
        "cache_dir",
        "resolved_repository",
        "resolved_revision",
        "snapshot_manifest_sha256",
    ):
        dense[field] = model_provenance[field]
    config["dense"] = dense
    config["inputs"] = input_bindings
    write_json(model_path, model_provenance)
    write_json(config_path, config)

    metrics = load_json(metrics_path)
    metrics["dense_metadata"] = dense
    write_json(metrics_path, metrics)
    artifact_hashes_after = {
        name: helpers.sha256_file(path) for name, path in completed_outputs.items()
    }
    write_json(
        binding_path,
        {
            "binding_method": "post_build_exact_cached_snapshot_capture",
            "capture_stage": args.capture_stage,
            "captured_at_utc": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "inputs": input_bindings,
            "builder_file_sha256_at_binding": helpers.sha256_file(builder_path),
            "finalizer_file_sha256_at_binding": helpers.sha256_file(Path(__file__).resolve()),
            "requested_model": requested_model,
            "resolved_repository": model_provenance["resolved_repository"],
            "resolved_revision": model_provenance["resolved_revision"],
            "snapshot_manifest_sha256": model_provenance["snapshot_manifest_sha256"],
            "snapshot_file_count": model_provenance["snapshot_file_count"],
            "embedding_model_json_sha256": helpers.sha256_file(model_path),
            "latest_snapshot_mtime_not_after_earliest_output": True,
            "latest_snapshot_mtime_utc": datetime.fromtimestamp(
                latest_snapshot_mtime, timezone.utc
            )
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "earliest_ranking_output_mtime_utc": datetime.fromtimestamp(
                earliest_output_mtime, timezone.utc
            )
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "artifact_sha256_before_binding": artifact_hashes_before,
            "artifact_sha256_after_binding": artifact_hashes_after,
            "note": (
                "Rankings and scores are unchanged; this step only records the exact cached "
                "model snapshot and mirrors that identity into config/metric metadata. The "
                "ranking process was already running when provenance-writing code was upgraded, "
                "so this is the builder file hash at binding time, not a claim about its prior "
                "in-memory source image. Query hashes, saved ranks/scores, model-cache timing, "
                "and all deterministic judgments are independently checked downstream."
            ),
        },
    )
    print(
        json.dumps(
            {
                "model": requested_model,
                "revision": model_provenance["resolved_revision"],
                "snapshot_manifest_sha256": model_provenance["snapshot_manifest_sha256"],
                "binding_report": str(binding_path.relative_to(root)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
