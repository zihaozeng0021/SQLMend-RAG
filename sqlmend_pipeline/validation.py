from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .constants import ALLOWED_DIALECTS, REQUIRED_CORPUS_FIELDS, VERSION_STATUSES
from .dedup import find_residual_near_duplicates
from .manifest import load_source_manifest
from .metadata import version_scope
from .utils import iter_jsonl, load_yaml, normalize_for_hash, read_json, word_count, write_json_atomic

SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("openai_token", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("generic_bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{32,}\b", re.I)),
)
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".txt", ".ps1", ".sh", ".csv"}
SQL_FENCE_OPEN_RE = re.compile(r"```\s*(?:sql|postgresql|mysql|sqlite|mariadb|duckdb)\b", re.I)
SQL_FENCE_COMPLETE_RE = re.compile(
    r"```\s*(?:sql|postgresql|mysql|sqlite|mariadb|duckdb)\b\s*\n\s*\S[\s\S]*?```", re.I
)
SQL_STATEMENT_LINE_RE = re.compile(
    r"(?im)^\s*(?:mysql>|sqlite>|postgres(?:ql)?[=#>]|"
    r"(?:SELECT|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|WITH|PRAGMA|EXPLAIN|VACUUM)\b)\s*\S+"
)
SYNTAX_BLOCK_RE = re.compile(r"(?im)^\s*(?:Syntax|Synopsis)\s*:\s*\n\s*\S+")
ERROR_EVIDENCE_RE = re.compile(
    r"\b(?:SQLSTATE\s*[=:]?\s*[0-9A-Z]{5}|(?:OBSOLETE_)?ER_[A-Z0-9_]+|SQLITE_[A-Z0-9_]+|"
    r"ERROR\s+\d{3,5}|(?:Binder|Catalog|Conversion|Invalid Input|Parser|Transaction) Error)\b|"
    r"\berror message\b|^\s*(?:Error symbol|Error number|Message)\s*:",
    re.I | re.M,
)
ATOMIC_SECTION_RE = re.compile(r"\b(?:syntax|synopsis|signature|parameters?|returns?|return value|description)\b", re.I)
MOJIBAKE_RE = re.compile(r"(?:Ã[\u00a0-\u00bfƒ]|Â[\u0080-\u00bf]|â€™|â€œ|â€\x9d|鈥.|聽|搂|锟斤拷)")


def _check(
    name: str,
    passed: bool,
    observed: Any,
    required: Any,
    explanation: str,
    remediation: str,
    critical: bool = True,
) -> dict[str, Any]:
    return {
        "check": name,
        "status": "PASS" if passed else "FAIL",
        "critical": critical,
        "observed": observed,
        "required": required,
        "explanation": explanation if not passed else "Requirement satisfied.",
        "recommended_remediation": None if passed else remediation,
    }


def _tracked_candidate_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        candidates = [root / line for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.CalledProcessError):
        candidates = [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]
    return [
        path
        for path in candidates
        if path.suffix.lower() in TEXT_SUFFIXES
        and "data" not in path.relative_to(root).parts
        and path.stat().st_size <= 5_000_000
    ]


def scan_secrets(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _tracked_candidate_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append({"type": name, "path": path.relative_to(root).as_posix(), "line": line})
    return findings


def _is_usable_atomic_fragment(chunk: dict[str, Any]) -> bool:
    text = str(chunk.get("text") or "")
    section = str(chunk.get("section") or "")
    fence_balanced = text.count("```") % 2 == 0
    if not fence_balanced:
        return False
    if chunk.get("contains_error_code") and ERROR_EVIDENCE_RE.search(text):
        return True
    if SQL_FENCE_COMPLETE_RE.search(text) or SYNTAX_BLOCK_RE.search(text) or SQL_STATEMENT_LINE_RE.search(text):
        return True
    if "```" in text or re.search(r"(?m)^\s*Table:\s*$\n\s*-\s*\S+", text):
        return True
    return bool(ATOMIC_SECTION_RE.search(section) and word_count(text) >= 8)


def _preserved_sql_or_error_material(chunk: dict[str, Any]) -> tuple[bool, bool]:
    """Return (applicable, preserved) using explicit structural evidence.

    A prose sentence containing a token such as FROM is not code.  Applicability
    therefore uses code fences, statement/prompt lines, Syntax blocks, or an
    identifiable error record.  This avoids treating ordinary SQL discussion as
    a failed code-preservation case.
    """
    text = str(chunk.get("text") or "")
    has_sql = bool(SQL_FENCE_OPEN_RE.search(text) or SQL_STATEMENT_LINE_RE.search(text) or SYNTAX_BLOCK_RE.search(text))
    has_error = bool(chunk.get("contains_error_code") or chunk.get("source_type") == "error_reference")
    if not has_sql and not has_error:
        return False, True
    fence_balanced = text.count("```") % 2 == 0
    sql_preserved = not has_sql or bool(
        (not SQL_FENCE_OPEN_RE.search(text) or SQL_FENCE_COMPLETE_RE.search(text))
        and (SQL_FENCE_COMPLETE_RE.search(text) or SQL_STATEMENT_LINE_RE.search(text) or SYNTAX_BLOCK_RE.search(text))
    )
    if chunk.get("source_type") == "error_reference" and chunk.get("dialect") in {"mysql", "mariadb"}:
        error_preserved = bool(re.search(r"(?im)^\s*Message\s*:\s*\S+", text) and ERROR_EVIDENCE_RE.search(text))
    else:
        error_preserved = not has_error or bool(ERROR_EVIDENCE_RE.search(text))
    return True, fence_balanced and sql_preserved and error_preserved


def _coherence_proxy(chunks: list[dict[str, Any]], sample_ids: set[str]) -> dict[str, Any]:
    sample = [chunk for chunk in chunks if chunk.get("chunk_id") in sample_ids]
    coherent = 0
    applicable_code = 0
    preserved_code = 0
    for chunk in sample:
        text = chunk["text"]
        words = word_count(text)
        alphanumeric = sum(character.isalnum() for character in text)
        ratio = alphanumeric / max(len(text), 1)
        fence_balanced = text.count("```") % 2 == 0
        adequate_content = words >= 20 or _is_usable_atomic_fragment(chunk)
        good = adequate_content and ratio >= 0.20 and "\ufffd" not in text and fence_balanced and text.startswith("Title:")
        coherent += int(good)
        applicable, preserved = _preserved_sql_or_error_material(chunk)
        if applicable:
            applicable_code += 1
            preserved_code += int(preserved)
    return {
        "sample_size": len(sample),
        "coherent_count": coherent,
        "coherent_percentage": 100 * coherent / len(sample) if sample else 0.0,
        "code_or_error_applicable_count": applicable_code,
        "code_or_error_preserved_count": preserved_code,
        "code_or_error_preserved_percentage": 100 * preserved_code / applicable_code if applicable_code else 100.0,
        "method": "deterministic inspectable sample; coherence and SQL/error preservation use explicit structural evidence, with the sample retained for manual review",
    }


def validate_corpus(root: str | Path = ".", config_path: str | Path = "config/chunking.yaml") -> dict[str, Any]:
    root = Path(root).resolve()
    corpus_path = root / "data" / "processed" / "corpus.jsonl"
    config = load_yaml(root / config_path)
    limits = config.get("validation", {})
    checks: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    chunks: list[dict[str, Any]] = []
    if corpus_path.exists():
        with corpus_path.open("r", encoding="utf-8", errors="strict") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    parse_errors.append(f"line {line_number}: {exc}")
    else:
        parse_errors.append("corpus.jsonl does not exist")
    checks.append(_check("jsonl_parseable", not parse_errors, parse_errors or len(chunks), "0 parse errors", "JSONL could not be parsed.", "Re-run chunking and inspect the listed line numbers."))
    if not chunks:
        report = {"status": "FAIL", "critical_failures": 1, "checks": checks}
        write_json_atomic(root / "reports" / "validation_report.json", report)
        return report

    ids = [chunk.get("chunk_id") for chunk in chunks]
    duplicate_ids = [key for key, count in Counter(ids).items() if key and count > 1]
    checks.append(_check("unique_chunk_ids", not duplicate_ids and None not in ids, duplicate_ids[:20], "all IDs unique and non-null", "Duplicate or null chunk IDs found.", "Regenerate chunk IDs from stable document/section/text identities."))
    missing_document_ids = [chunk.get("chunk_id") for chunk in chunks if not chunk.get("document_id")]
    checks.append(_check("document_ids_present", not missing_document_ids, len(missing_document_ids), 0, "Some chunks lack document IDs.", "Repair parser metadata propagation and rebuild."))
    missing_fields = [
        {"chunk_id": chunk.get("chunk_id"), "fields": [field for field in REQUIRED_CORPUS_FIELDS if field not in chunk]}
        for chunk in chunks
        if any(field not in chunk for field in REQUIRED_CORPUS_FIELDS)
    ]
    checks.append(_check("required_fields_exist", not missing_fields, missing_fields[:20], "all required fields on every chunk", "Required schema fields are absent.", "Fix chunk schema construction and rebuild."))
    empty_text = [chunk.get("chunk_id") for chunk in chunks if not str(chunk.get("text") or "").strip()]
    checks.append(_check("non_empty_text", not empty_text, len(empty_text), 0, "Empty chunk text found.", "Drop empty blocks before chunking."))
    bad_urls = [
        chunk.get("chunk_id")
        for chunk in chunks
        if urlsplit(str(chunk.get("source_url") or "")).scheme not in {"http", "https"}
        or not urlsplit(str(chunk.get("source_url") or "")).netloc
    ]
    checks.append(_check("source_urls_present", not bad_urls, len(bad_urls), 0, "Missing or invalid source URLs found.", "Correct source URL templates in sources.yaml."))
    bad_dialects = Counter(chunk.get("dialect") for chunk in chunks if chunk.get("dialect") not in ALLOWED_DIALECTS)
    checks.append(_check("allowed_dialect_vocabulary", not bad_dialects, dict(bad_dialects), list(ALLOWED_DIALECTS), "Unknown dialect values found.", "Use normalize_dialect and rebuild."))

    exact_keys: Counter[tuple[str, str, str]] = Counter()
    for chunk in chunks:
        exact_keys[(chunk.get("dialect"), version_scope(chunk), normalize_for_hash(str(chunk.get("text") or "")))] += 1
    exact_duplicates = sum(count - 1 for count in exact_keys.values() if count > 1)
    checks.append(_check("no_exact_duplicates", exact_duplicates == 0, exact_duplicates, 0, "Exact duplicates remain inside a dialect/version scope.", "Run chunk-level exact deduplication after chunk generation."))

    broken = [
        chunk.get("chunk_id")
        for chunk in chunks
        if "\ufffd" in str(chunk.get("text") or "")
        or MOJIBAKE_RE.search(str(chunk.get("text") or ""))
    ]
    checks.append(_check("encoding_not_obviously_broken", not broken, {"count": len(broken), "chunk_ids": broken[:20]}, {"count": 0}, "Replacement characters or common mojibake found.", "Fix source decoding and recollect affected documents."))
    minimum_words = int(limits.get("minimum_chunk_words", 20))
    short_chunks = [chunk for chunk in chunks if word_count(chunk.get("text", "")) < minimum_words]
    garbage = [
        chunk.get("chunk_id")
        for chunk in short_chunks
        if not _is_usable_atomic_fragment(chunk)
    ]
    checks.append(
        _check(
            "no_extremely_short_garbage",
            not garbage,
            {"garbage_count": len(garbage), "garbage_chunk_ids": garbage[:20], "short_but_atomic_count": len(short_chunks) - len(garbage)},
            {"garbage_count": 0},
            f"Unstructured chunks below {minimum_words} lexical words found.",
            "Merge or remove the listed trailing/navigation fragments while retaining complete signatures and error records.",
        )
    )
    invalid_status = Counter(chunk.get("version_status") for chunk in chunks if chunk.get("version_status") not in VERSION_STATUSES)
    checks.append(_check("valid_version_status", not invalid_status, dict(invalid_status), list(VERSION_STATUSES), "Invalid version status values found.", "Normalize version metadata during enrichment."))

    minimum_chunks = int(limits.get("minimum_chunks", 10000))
    checks.append(_check("minimum_corpus_chunks", len(chunks) >= minimum_chunks, len(chunks), f">={minimum_chunks}", "Corpus is below the hard chunk threshold.", "Collect more official material or reduce coherent chunk target size."))
    total_words = sum(word_count(str(chunk.get("text") or "")) for chunk in chunks)
    minimum_words_total = int(limits.get("minimum_total_words", 100000))
    checks.append(_check("minimum_total_words", total_words >= minimum_words_total, total_words, f">={minimum_words_total}", "Corpus has too few words.", "Collect and process more useful documentation."))
    dialect_counts = Counter(chunk.get("dialect") for chunk in chunks)
    minimum_per_dialect = int(limits.get("minimum_chunks_per_dialect", 1000))
    low_dialects = {dialect: dialect_counts[dialect] for dialect in ALLOWED_DIALECTS if dialect_counts[dialect] < minimum_per_dialect}
    checks.append(_check("minimum_chunks_each_dialect", not low_dialects, dict(dialect_counts), f">={minimum_per_dialect} for each", "One or more dialects are underrepresented.", "Add authoritative sources for the listed dialects or tune structure-aware chunk sizes."))
    max_share = float(limits.get("maximum_single_dialect_share", 0.35))
    shares = {dialect: dialect_counts[dialect] / len(chunks) for dialect in ALLOWED_DIALECTS}
    excessive = {dialect: share for dialect, share in shares.items() if share > max_share}
    checks.append(_check("maximum_dialect_share", not excessive, shares, f"<={max_share:.0%} each", "A dialect dominates the corpus.", "Enable deterministic balancing or document a technical exception."))
    known_versions = sum(chunk.get("version_status") != "unknown" for chunk in chunks)
    version_ratio = known_versions / len(chunks)
    required_version_ratio = float(limits.get("minimum_version_known_ratio", 0.90))
    checks.append(_check("version_metadata_coverage", version_ratio >= required_version_ratio, version_ratio, f">={required_version_ratio:.0%}", "Too many chunks have unknown version applicability.", "Use versioned manuals or retain explicit release-family metadata."))
    dialect_ratio = sum(chunk.get("dialect") in ALLOWED_DIALECTS for chunk in chunks) / len(chunks)
    checks.append(_check("dialect_metadata_coverage", dialect_ratio == 1.0, dialect_ratio, "100%", "Some chunks do not have a recognized dialect.", "Normalize dialects and rebuild."))

    manifest = load_source_manifest(root / "config" / "sources.yaml")
    manifest_ids = {source["id"] for source in manifest["sources"]}
    missing_sources = Counter(chunk.get("source_id") for chunk in chunks if chunk.get("source_id") not in manifest_ids)
    checks.append(_check("source_manifest_consistency", not missing_sources, dict(missing_sources), "every source_id in config/sources.yaml", "Corpus contains a source absent from the manifest.", "Add the source with correct authority/licensing metadata or remove its chunks."))

    secret_findings = scan_secrets(root)
    checks.append(_check("no_secret_credentials", not secret_findings, secret_findings[:20], 0, "Credential-like material found in tracked project files.", "Remove and rotate any real credential; use environment variables."))

    analyzable_chunks = [
        {
            **chunk,
            "chunk_id": chunk.get("chunk_id") or f"invalid_{index}",
            "dialect": chunk.get("dialect") or "unknown",
            "text": str(chunk.get("text") or ""),
            "version_status": chunk.get("version_status") or "unknown",
        }
        for index, chunk in enumerate(chunks)
    ]
    residual = find_residual_near_duplicates(analyzable_chunks, lambda row: row["text"], float(limits.get("near_duplicate_threshold", 0.94)))
    max_residual = float(limits.get("maximum_residual_near_duplicate_rate", 0.03))
    checks.append(_check("residual_near_duplicate_rate", residual["estimated_record_rate"] < max_residual, residual, f"<{max_residual:.0%}", "Estimated residual near duplicates exceed the limit.", "Lower the near-duplicate threshold after reviewing version/error safeguards."))

    sample_path = root / "reports" / "inspection_sample.jsonl"
    sample_ids = {row["chunk_id"] for row in iter_jsonl(sample_path)} if sample_path.exists() else set()
    proxy = _coherence_proxy(chunks, sample_ids)
    checks.append(_check("inspectable_sample_size", proxy["sample_size"] >= 100, proxy["sample_size"], ">=100", "The deterministic inspection sample is missing or too small.", "Run the statistics stage to regenerate a stratified sample."))
    checks.append(_check("sample_coherence_proxy", proxy["coherent_percentage"] >= 95.0, proxy["coherent_percentage"], ">=95%", "The sample failed structural coherence heuristics.", "Inspect reports/inspection_sample.jsonl and fix parser/chunk boundaries.", critical=True))
    checks.append(_check("sample_code_error_preservation_proxy", proxy["code_or_error_preserved_percentage"] >= 90.0, proxy["code_or_error_preserved_percentage"], ">=90%", "SQL/error material appears broken in the sample.", "Review atomic code/table preservation and source parsing.", critical=True))

    topic_counts = Counter((chunk.get("dialect"), chunk.get("topic")) for chunk in chunks)
    missing_topic_coverage = {
        dialect: [topic for topic in ("syntax", "functions", "errors", "migration", "release_notes") if topic_counts[(dialect, topic)] == 0]
        for dialect in ALLOWED_DIALECTS
    }
    missing_topic_coverage = {key: value for key, value in missing_topic_coverage.items() if value}
    checks.append(_check("topic_coverage", not missing_topic_coverage, missing_topic_coverage or "all topic families represented", "syntax/functions/errors/migration/release notes per dialect where sources permit", "Some classified topic families are absent.", "Add official error, migration, release, or reference documentation.", critical=False))

    critical_failures = sum(check["status"] == "FAIL" and check["critical"] for check in checks)
    report = {
        "status": "PASS" if critical_failures == 0 else "FAIL",
        "critical_failures": critical_failures,
        "warning_failures": sum(check["status"] == "FAIL" and not check["critical"] for check in checks),
        "corpus_path": corpus_path.relative_to(root).as_posix(),
        "checks": checks,
        "sample_proxy": proxy,
    }
    write_json_atomic(root / "reports" / "validation_report.json", report)
    return report
