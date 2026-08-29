"""Monotonic latency summaries and reproducible environment metadata."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import statistics
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import psutil


def summarize_latencies(seconds: Iterable[float]) -> dict[str, float | int]:
    values = np.asarray(list(seconds), dtype=np.float64)
    if values.size == 0:
        return {
            "sample_count": 0,
            "mean_ms": 0.0,
            "median_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "maximum_ms": 0.0,
            "queries_per_second": 0.0,
        }
    if np.any(values < 0) or not np.all(np.isfinite(values)):
        raise ValueError("Latency samples must be finite and non-negative")
    total = float(values.sum())
    return {
        "sample_count": int(values.size),
        "mean_ms": float(values.mean() * 1000.0),
        "median_ms": float(np.median(values) * 1000.0),
        "p50_ms": float(np.percentile(values, 50) * 1000.0),
        "p95_ms": float(np.percentile(values, 95) * 1000.0),
        "maximum_ms": float(values.max() * 1000.0),
        "queries_per_second": float(values.size / total) if total > 0 else 0.0,
    }


def directory_size(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def _cpu_name() -> str:
    value = platform.processor().strip()
    if value:
        return value
    return os.environ.get("PROCESSOR_IDENTIFIER", "unknown")


def environment_metadata(*, device: str, corpus_chunks: int, embedding_dimension: int) -> dict[str, Any]:
    package_names = (
        "numpy",
        "rank-bm25",
        "sentence-transformers",
        "transformers",
        "torch",
        "huggingface-hub",
        "PyYAML",
        "psutil",
    )
    versions: dict[str, str | None] = {}
    for name in package_names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    gpu: str | None = None
    try:
        import torch

        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
    except ImportError:
        pass
    return {
        "operating_system": platform.platform(),
        "python_version": platform.python_version(),
        "cpu": _cpu_name(),
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "ram_bytes": int(psutil.virtual_memory().total),
        "gpu": gpu,
        "device_used_for_official_run": device,
        "package_versions": versions,
        "corpus_chunks": corpus_chunks,
        "embedding_dimension": embedding_dimension,
        "clock": "time.perf_counter monotonic high-resolution clock",
    }
