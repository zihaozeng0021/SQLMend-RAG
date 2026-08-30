"""Canonical, atomic I/O used by the generation online path."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


_SCORE_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{12}$")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw in enumerate(stream, start=1):
            if not raw.strip():
                raise ValueError(f"Blank JSONL line at {path}:{line_number}")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}")
            records.append(value)
    return records


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def write_bytes(path: Path, content: bytes) -> None:
    _atomic_replace(path, content)


def write_json(path: Path, value: Any) -> None:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_replace(path, rendered.encode("utf-8"))


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    rendered = "".join(
        json.dumps(dict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        for record in records
    )
    _atomic_replace(path, rendered.encode("utf-8"))


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        dict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
        stream.flush()
        os.fsync(stream.fileno())


def read_trec_run(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw in enumerate(stream, start=1):
            columns = raw.rstrip("\r\n").split()
            if len(columns) != 6 or columns[1] != "Q0":
                raise ValueError(f"Malformed TREC row at {path}:{line_number}")
            query_id, _, passage_id, rank_text, score_text, run_tag = columns
            if not _SCORE_RE.fullmatch(score_text):
                raise ValueError(f"Non-canonical score at {path}:{line_number}")
            try:
                rank = int(rank_text)
                score = float(score_text)
            except ValueError as exc:
                raise ValueError(f"Malformed rank or score at {path}:{line_number}") from exc
            if rank < 1 or not math.isfinite(score):
                raise ValueError(f"Illegal rank or score at {path}:{line_number}")
            rows.append(
                {
                    "query_id": query_id,
                    "passage_id": passage_id,
                    "rank": rank,
                    "score": score,
                    "run_tag": run_tag,
                }
            )
    return rows
