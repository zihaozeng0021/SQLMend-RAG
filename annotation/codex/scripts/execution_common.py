from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


EXECUTION_INPUT_FIELDS = (
    "schema_version",
    "query_id",
    "dialect",
    "version",
    "version_min",
    "version_max",
    "version_status",
    "setup_sql",
    "seed_data",
    "sql",
    "reference_fix_sql",
)


def execution_input_payload(case: dict[str, Any]) -> dict[str, Any]:
    payload = {field: case.get(field) for field in EXECUTION_INPUT_FIELDS}
    payload["semantic_test_sql"] = (
        case.get("verification", {}).get("semantic_check", {}).get("test_sql")
    )
    return payload


def execution_input_sha256(case: dict[str, Any]) -> str:
    encoded = json.dumps(
        execution_input_payload(case),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
