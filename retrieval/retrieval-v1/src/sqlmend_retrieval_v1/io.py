"""Canonical JSON/YAML/JSONL and six-column TREC I/O."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml

from .models import RunEntry


_SCORE_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{12}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(path)


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


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(dict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":")))
            stream.write("\n")
    temporary.replace(path)


def read_trec_run(path: Path) -> list[RunEntry]:
    entries: list[RunEntry] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw in enumerate(stream, start=1):
            columns = raw.rstrip("\r\n").split()
            if len(columns) != 6 or columns[1] != "Q0":
                raise ValueError(f"Malformed TREC row at {path}:{line_number}")
            query_id, _, chunk_id, rank_text, score_text, run_tag = columns
            if not _SCORE_RE.fullmatch(score_text):
                raise ValueError(f"Non-canonical score at {path}:{line_number}")
            score = float(score_text)
            if not math.isfinite(score):
                raise ValueError(f"Non-finite score at {path}:{line_number}")
            entries.append(RunEntry(query_id, chunk_id, int(rank_text), score, run_tag))
    return entries


def group_run(entries: Iterable[RunEntry]) -> dict[str, list[RunEntry]]:
    grouped: dict[str, list[RunEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.query_id].append(entry)
    return {query_id: sorted(rows, key=lambda row: row.rank) for query_id, rows in sorted(grouped.items())}


def validate_run(
    entries: Iterable[RunEntry],
    *,
    expected_query_ids: Iterable[str],
    known_chunk_ids: Iterable[str],
    expected_run_tag: str,
    depth: int = 30,
) -> list[RunEntry]:
    rows = list(entries)
    grouped = group_run(rows)
    expected_queries = set(expected_query_ids)
    if set(grouped) != expected_queries:
        raise ValueError("Run query coverage differs from the frozen query set")
    chunks = set(known_chunk_ids)
    observed_tags = {row.run_tag for row in rows}
    if observed_tags != {expected_run_tag}:
        raise ValueError(f"Unexpected run tags: {sorted(observed_tags)}")
    for query_id, query_rows in grouped.items():
        if len(query_rows) != depth:
            raise ValueError(f"{query_id} has {len(query_rows)} rows, expected {depth}")
        if [row.rank for row in query_rows] != list(range(1, depth + 1)):
            raise ValueError(f"{query_id} ranks are not continuous")
        ids = [row.chunk_id for row in query_rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{query_id} contains duplicate chunks")
        unknown = sorted(set(ids) - chunks)
        if unknown:
            raise ValueError(f"{query_id} contains unknown chunks: {unknown[:3]}")
        if not all(math.isfinite(row.score) for row in query_rows):
            raise ValueError(f"{query_id} contains a non-finite score")
    return sorted(rows, key=lambda row: (row.query_id, row.rank))


def render_trec_run(entries: Iterable[RunEntry]) -> str:
    rows = sorted(entries, key=lambda row: (row.query_id, row.rank))
    lines: list[str] = []
    for row in rows:
        score = 0.0 if row.score == 0.0 else row.score
        lines.append(
            f"{row.query_id} Q0 {row.chunk_id} {row.rank} {score:.12f} {row.run_tag}\n"
        )
    return "".join(lines)


def write_trec_run(path: Path, entries: Iterable[RunEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_trec_run(entries), encoding="utf-8", newline="\n")
    temporary.replace(path)


def read_qrels(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(dict)
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, raw in enumerate(stream, start=1):
            columns = raw.split()
            if len(columns) != 4 or columns[1] != "0":
                raise ValueError(f"Malformed qrel at {path}:{line_number}")
            query_id, _, chunk_id, relevance_text = columns
            relevance = int(relevance_text)
            if relevance not in {0, 1, 2}:
                raise ValueError(f"Illegal relevance at {path}:{line_number}")
            if chunk_id in result[query_id]:
                raise ValueError(f"Duplicate qrel for {(query_id, chunk_id)}")
            result[query_id][chunk_id] = relevance
    return {query_id: dict(sorted(values.items())) for query_id, values in sorted(result.items())}
