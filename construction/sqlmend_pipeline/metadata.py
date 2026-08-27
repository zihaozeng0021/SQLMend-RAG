from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .constants import ALLOWED_DIALECTS, VERSION_STATUSES
from .utils import iter_jsonl, write_json_atomic, write_jsonl_atomic

DIALECT_ALIASES = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "pgsql": "postgresql",
    "mysql": "mysql",
    "mysql community": "mysql",
    "mysql community edition": "mysql",
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "mariadb": "mariadb",
    "maria db": "mariadb",
    "duckdb": "duckdb",
}

VERSION_TOKEN = r"v?(\d{1,2}(?:[._]\d{1,3}){0,2})(?![A-Za-z0-9]|[._]\d)"
RELEASE_VERSION_AFTER_RE = re.compile(
    r"\b(?:release(?:[\s_-]+notes?)?|version|changelog|change[\s_-]*log|"
    r"what'?s[\s_-]+new(?:\s+in)?|releaselog)\b"
    r"(?:\s*[:#=/\\_-]\s*|\s+)"
    r"(?:(?:[A-Za-z][A-Za-z0-9-]*)\s*[-_/\\]\s*){0,4}" + VERSION_TOKEN,
    re.I,
)
RELEASE_VERSION_BEFORE_RE = re.compile(
    VERSION_TOKEN
    + r"(?:\s+[A-Za-z][A-Za-z0-9-]*){0,3}\s+"
    + r"(?:release(?:[\s_-]+notes?)?|version|changelog|change[\s_-]*log)\b",
    re.I,
)
PROJECT_PATCH_VERSION_RE = re.compile(
    r"\b(?:PostgreSQL|MySQL|SQLite|MariaDB|DuckDB)\s+"
    r"v?(\d{1,2}(?:[._]\d{1,3}){2})(?![A-Za-z0-9]|[._]\d)",
    re.I,
)
SQL_RE = re.compile(
    r"```\s*(?:sql|postgresql|mysql|sqlite|mariadb|duckdb)\b\s*\n\s*\S|"
    r"^\s*(?:mysql>|sqlite>|postgres(?:ql)?[=#>]|"
    r"(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|WITH|PRAGMA|EXPLAIN|VACUUM)\b)|"
    r"^\s*(?:Syntax|Synopsis)\s*:\s*$",
    re.I | re.M,
)
ERROR_PATTERNS = {
    "postgresql": re.compile(r"\bSQLSTATE\s*[=:]?\s*[0-9A-Z]{5}\b|\b(?:ERROR|FATAL|PANIC):", re.I),
    "mysql": re.compile(r"\b(?:ERROR\s+\d{3,5}|(?:OBSOLETE_)?ER_[A-Z0-9_]+|SQLSTATE\s*\[?[0-9A-Z]{5}\]?)\b", re.I),
    "sqlite": re.compile(r"\bSQLITE_[A-Z0-9_]+\b|\b(?:error|extended error) code\b", re.I),
    "mariadb": re.compile(r"\b(?:ERROR\s+\d{3,5}|(?:OBSOLETE_)?ER_[A-Z0-9_]+|SQLSTATE\s*\[?[0-9A-Z]{5}\]?)\b", re.I),
    "duckdb": re.compile(r"\b(?:Binder|Catalog|Conversion|Invalid Input|Parser|Transaction) Error\b", re.I),
}
GENERIC_ERROR_RE = re.compile(r"\b(?:error code|error message|SQLSTATE)\b", re.I)
VERSION_COMPAT_RE = re.compile(
    r"\b(?:version|release|deprecated|deprecation|compatib(?:le|ility)|incompatib(?:le|ility)|"
    r"migration|upgrade|legacy|prior to|before version|since version|new in|removed in|not supported)\b",
    re.I,
)

TOPIC_RULES = (
    ("errors", re.compile(r"\b(?:error|sqlstate|diagnostic|exception|warning)\b", re.I)),
    ("migration", re.compile(r"\b(?:migration|migrating|upgrade|compatib|deprecated|porting)\b", re.I)),
    ("functions", re.compile(r"\b(?:function|aggregate|window function|scalar function|operator)\b", re.I)),
    ("data_types", re.compile(r"\b(?:data type|datatype|type conversion|cast|collation)\b", re.I)),
    ("syntax", re.compile(r"\b(?:syntax|statement|command|query|expression|grammar)\b", re.I)),
    ("configuration", re.compile(r"\b(?:configuration|setting|parameter|pragma|server option)\b", re.I)),
    ("transactions", re.compile(r"\b(?:transaction|locking|concurrency|isolation|commit|rollback)\b", re.I)),
    ("release_notes", re.compile(r"\b(?:release|changelog|what'?s new)\b", re.I)),
)


def normalize_dialect(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().casefold())
    if normalized not in DIALECT_ALIASES:
        raise ValueError(f"Unsupported dialect: {value!r}")
    return DIALECT_ALIASES[normalized]


def parse_version_label(value: str | None) -> dict[str, str | None]:
    if value is None or not str(value).strip():
        return {"version": None, "version_min": None, "version_max": None, "version_status": "unknown"}
    text = str(value).strip()
    if text.casefold() in {"current", "latest", "stable"}:
        return {"version": "current", "version_min": None, "version_max": None, "version_status": "current"}
    range_match = re.fullmatch(r"v?(\d+(?:\.\d+){0,2})\s*(?:-|–|—|to)\s*v?(\d+(?:\.\d+){0,2}|x)", text, re.I)
    if range_match:
        return {
            "version": text,
            "version_min": range_match.group(1),
            "version_max": range_match.group(2),
            "version_status": "range",
        }
    family_match = re.fullmatch(r"v?(\d+\.\d+)\.x", text, re.I)
    if family_match:
        family = family_match.group(1)
        return {
            "version": f"{family}.x",
            "version_min": f"{family}.0",
            "version_max": f"{family}.x",
            "version_status": "range",
        }
    exact_match = re.fullmatch(r"v?(\d+(?:\.\d+){1,2})", text, re.I)
    if exact_match:
        return {
            "version": exact_match.group(1),
            "version_min": exact_match.group(1),
            "version_max": exact_match.group(1),
            "version_status": "exact",
        }
    major_match = re.fullmatch(r"v?(\d{1,2})", text, re.I)
    if major_match:
        major = major_match.group(1)
        return {
            "version": major,
            "version_min": f"{major}.0",
            "version_max": f"{major}.x",
            "version_status": "range",
        }
    return {"version": text, "version_min": None, "version_max": None, "version_status": "unknown"}


def version_scope(record: dict[str, Any]) -> str:
    status = record.get("version_status") or "unknown"
    if status == "range":
        return f"{record.get('version_min') or '?'}..{record.get('version_max') or '?'}"
    return str(record.get("version") or status)


def infer_explicit_release_version(text: str) -> dict[str, str | None] | None:
    normalized = re.sub(r"(?<=\d)_(?=\d)", ".", text)
    # Require the number to be adjacent to an explicit release/version label.
    # A generic "release" elsewhere in a heading is not enough: DocBook titles
    # such as "E.25. Release 14 > E.25.2" otherwise turn the structural chapter
    # number 25.2 into a fabricated PostgreSQL version.
    match = (
        RELEASE_VERSION_AFTER_RE.search(normalized)
        or RELEASE_VERSION_BEFORE_RE.search(normalized)
        or PROJECT_PATCH_VERSION_RE.search(normalized)
    )
    if not match:
        return None
    return parse_version_label(match.group(1))


def contains_sql(text: str) -> bool:
    return bool(SQL_RE.search(text))


def contains_error_code(text: str, dialect: str) -> bool:
    pattern = ERROR_PATTERNS.get(dialect)
    return bool((pattern and pattern.search(text)) or GENERIC_ERROR_RE.search(text))


def contains_version_or_compatibility(text: str) -> bool:
    return bool(VERSION_COMPAT_RE.search(text))


def classify_topic(section: str, text: str, source_type: str) -> str:
    if source_type == "release_notes":
        return "release_notes"
    sample = f"{section}\n{text[:1000]}"
    for topic, pattern in TOPIC_RULES:
        if pattern.search(sample):
            return topic
    return "general"


def enrich_document(document: dict[str, Any]) -> dict[str, Any]:
    output = dict(document)
    output["dialect"] = normalize_dialect(str(document.get("dialect", "")))
    status = document.get("version_status") or "unknown"
    if status not in VERSION_STATUSES:
        status = "unknown"
    output["version_status"] = status
    if status == "unknown":
        context = " ".join(
            str(document.get(key) or "")
            for key in ("title", "logical_source_path", "source_name", "source_url")
        )
        inferred = infer_explicit_release_version(context)
        if inferred and document.get("source_type") == "release_notes":
            output.update(inferred)
            output["version_inference"] = "explicit release/version label in source path or title"
        else:
            output["version"] = document.get("version")
            output["version_min"] = document.get("version_min")
            output["version_max"] = document.get("version_max")
            output["version_inference"] = None
    for field in ("version", "version_min", "version_max"):
        output.setdefault(field, None)
    return output


def enrich_all(root: str | Path = ".") -> dict[str, Any]:
    root = Path(root).resolve()
    input_path = root / "data" / "interim" / "cleaned_documents.jsonl"
    documents = [enrich_document(document) for document in iter_jsonl(input_path)]
    write_jsonl_atomic(root / "data" / "interim" / "enriched_documents.jsonl", documents)
    known = sum(document["version_status"] != "unknown" for document in documents)
    report = {
        "document_count": len(documents),
        "version_known_count": known,
        "version_known_percentage": round(100 * known / len(documents), 4) if documents else 0.0,
        "dialects": sorted({document["dialect"] for document in documents}),
    }
    write_json_atomic(root / "reports" / "metadata_report.json", report)
    return report
