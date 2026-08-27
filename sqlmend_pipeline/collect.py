from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import tarfile
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, UnicodeDammit
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .manifest import load_source_manifest, public_source_record
from .utils import (
    canonicalize_url,
    read_json,
    relative_posix,
    safe_slug,
    sha256_bytes,
    sha256_text,
    utc_now,
    write_json_atomic,
    write_jsonl_atomic,
)

LOGGER = logging.getLogger("sqlmend.collect")
SUPPORTED_EXTENSIONS = {
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".xml": "xml",
    ".sgml": "xml",
    ".sgm": "xml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".rst": "text",
    ".txt": "text",
    ".in": "html",
}


class CollectionError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.last_request = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self.last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self.last_request = time.monotonic()


def build_session(user_agent: str, retries: int) -> requests.Session:
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _decode_bytes(data: bytes) -> tuple[str, str, int]:
    try:
        return data.decode("utf-8-sig"), "utf-8", 0
    except UnicodeDecodeError:
        detected = UnicodeDammit(data)
        if detected.unicode_markup is not None:
            text = detected.unicode_markup
            return text, detected.original_encoding or "detected", text.count("\ufffd")
        text = data.decode("utf-8", errors="replace")
        return text, "utf-8-replacement", text.count("\ufffd")


def detect_format(path: str, content_type: str | None = None) -> str:
    suffix = PurePosixPath(path.lower()).suffix
    if suffix in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[suffix]
    content_type = (content_type or "").lower()
    if "html" in content_type:
        return "html"
    if "xml" in content_type:
        return "xml"
    if "markdown" in content_type:
        return "markdown"
    return "text"


def extract_original_title(text: str, content_format: str, fallback: str) -> str:
    if content_format in {"html", "xml"}:
        soup = BeautifulSoup(text[:1_000_000], "lxml" if content_format == "html" else "xml")
        candidate = soup.find(["title", "h1", "refname"])
        if candidate and candidate.get_text(" ", strip=True):
            return candidate.get_text(" ", strip=True)[:500]
    elif content_format == "markdown":
        for line in text.splitlines()[:200]:
            match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if match:
                return match.group(1).strip()[:500]
    for line in text.splitlines()[:50]:
        clean = line.strip().strip("=#*-")
        if 3 <= len(clean) <= 500:
            return clean
    return fallback[:500]


def _included(path: str, collector: dict[str, Any]) -> bool:
    normalized = path.replace("\\", "/")
    includes = collector.get("include") or ["**/*"]
    excludes = collector.get("exclude") or []
    matched = any(fnmatch.fnmatch(normalized, pattern) for pattern in includes)
    if not matched:
        return False
    return not any(fnmatch.fnmatch(normalized, pattern) for pattern in excludes)


def _strip_components(path: str, count: int) -> str:
    parts = [part for part in PurePosixPath(path).parts if part not in {"", "."}]
    if count >= len(parts):
        return parts[-1] if parts else "document"
    return PurePosixPath(*parts[count:]).as_posix()


def _apply_path_rules(metadata: dict[str, Any], logical_path: str, source: dict[str, Any]) -> None:
    for rule in source.get("path_rules", []):
        glob = rule.get("glob")
        regex = rule.get("regex")
        if (glob and fnmatch.fnmatch(logical_path, glob)) or (regex and re.search(regex, logical_path)):
            for key, value in rule.get("set", {}).items():
                metadata[key] = value
            extraction = rule.get("extract_version_regex")
            if extraction:
                match = re.search(extraction, logical_path)
                if match:
                    version = match.groupdict().get("version") or match.group(1)
                    metadata["version"] = version
                    metadata["version_status"] = rule.get("version_status", "exact")


def _logical_source_url(source: dict[str, Any], logical_path: str) -> str:
    collector = source["collector"]
    template = collector.get("source_url_template")
    pure = PurePosixPath(logical_path)
    if template:
        return canonicalize_url(
            template.format(path=logical_path, name=pure.name, stem=pure.stem)
        )
    return canonicalize_url(urljoin(source["base_url"].rstrip("/") + "/", logical_path))


def _raw_output_path(root: Path, source: dict[str, Any], source_url: str, logical_path: str) -> Path:
    suffix = safe_slug(PurePosixPath(logical_path).stem, 45)
    name = f"{safe_slug(source['id'], 30)}_{sha256_text(source_url)[:16]}_{suffix}.json"
    return root / "data" / "raw" / source["dialect"] / name


def _save_raw_document(
    root: Path,
    source: dict[str, Any],
    logical_path: str,
    source_url: str,
    payload: bytes,
    retrieved_at: str,
    content_type: str | None = None,
) -> tuple[str, bool]:
    output = _raw_output_path(root, source, source_url, logical_path)
    payload_hash = sha256_bytes(payload)
    if output.exists():
        existing = read_json(output, {})
        if (
            existing.get("source_url") == source_url
            and existing.get("content_hash") == payload_hash
            and existing.get("content") is not None
        ):
            return relative_posix(output, root), True
    text, encoding, replacements = _decode_bytes(payload)
    content_format = detect_format(logical_path, content_type)
    metadata = public_source_record(source)
    _apply_path_rules(metadata, logical_path, source)
    license_match = re.search(
        r"(?im)^\s*(?:license|content-license)\s*:\s*[\"']?([^\r\n\"']+)|"
        r"This page is licensed:\s*([^}\r\n]+)",
        text,
    )
    if license_match:
        page_license = next(group for group in license_match.groups() if group)
        metadata["license_or_terms_note"] = (
            f"Per-page license marker: {page_license.strip()}. "
            f"Manifest note: {metadata['license_or_terms_note']}"
        )
    document_id = f"doc_{source['dialect']}_{sha256_text(source['id'] + '|' + source_url)[:24]}"
    record: dict[str, Any] = {
        "document_id": document_id,
        **metadata,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "original_title": extract_original_title(text, content_format, PurePosixPath(logical_path).stem),
        "section_or_header": None,
        "product_or_project_name": source["vendor_or_project"],
        "logical_source_path": logical_path,
        "local_raw_file_path": relative_posix(output, root),
        "content_hash": payload_hash,
        "content_format": content_format,
        "content_encoding": encoding,
        "decode_replacement_count": replacements,
        "content": text,
    }
    write_json_atomic(output, record)
    return relative_posix(output, root), False


def _download_to_cache(
    session: requests.Session,
    limiter: RateLimiter,
    url: str,
    cache_path: Path,
    timeout: tuple[float, float],
    expected_sha256: str | None,
    expected_sha3_256: str | None = None,
) -> tuple[Path, bool, str, str | None]:
    if cache_path.exists():
        cached_bytes = cache_path.read_bytes()
        digest = sha256_bytes(cached_bytes)
        digest_sha3 = hashlib.sha3_256(cached_bytes).hexdigest()
        if (not expected_sha256 or digest == expected_sha256) and (
            not expected_sha3_256 or digest_sha3 == expected_sha3_256
        ):
            return cache_path, True, digest, None
        LOGGER.warning("Cached archive hash mismatch; downloading again: %s", cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    limiter.wait()
    response = session.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    fd, temp_name = tempfile.mkstemp(prefix=f".{cache_path.name}.", suffix=".part", dir=cache_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        digest = sha256_bytes(Path(temp_name).read_bytes())
        digest_sha3 = hashlib.sha3_256(Path(temp_name).read_bytes()).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise CollectionError(
                f"SHA-256 mismatch for {url}: expected {expected_sha256}, observed {digest}"
            )
        if expected_sha3_256 and digest_sha3 != expected_sha3_256:
            raise CollectionError(
                f"SHA3-256 mismatch for {url}: expected {expected_sha3_256}, observed {digest_sha3}"
            )
        os.replace(temp_name, cache_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return cache_path, False, digest, response.headers.get("ETag")


def _iter_archive(path: Path, archive_format: str) -> Iterable[tuple[str, bytes]]:
    if archive_format == "zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                yield info.filename, archive.read(info)
        return
    if archive_format in {"tar", "tar.gz", "tgz", "tar.bz2", "tar.xz"}:
        with tarfile.open(path, mode="r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    yield member.name, handle.read()
        return
    raise CollectionError(f"Unsupported archive format: {archive_format}")


def _collect_archive(
    root: Path,
    source: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    timeout: tuple[float, float],
    seen_urls: set[str],
) -> dict[str, Any]:
    collector = source["collector"]
    url = canonicalize_url(collector["url"])
    archive_format = collector.get("archive_format") or ("zip" if url.endswith(".zip") else "tar.gz")
    cache_suffix = {"zip": ".zip", "tar": ".tar", "tar.gz": ".tar.gz", "tgz": ".tgz", "tar.bz2": ".tar.bz2", "tar.xz": ".tar.xz"}.get(
        archive_format, ".archive"
    )
    cache_path = root / "data" / "raw" / source["dialect"] / ".archives" / f"{safe_slug(source['id'])}{cache_suffix}"
    cache_path, cache_hit, archive_hash, etag = _download_to_cache(
        session,
        limiter,
        url,
        cache_path,
        timeout,
        collector.get("expected_sha256"),
        collector.get("expected_sha3_256"),
    )
    retrieved_at = utc_now()
    result: dict[str, Any] = {
        "source_id": source["id"],
        "collection_url": url,
        "archive_sha256": archive_hash,
        "archive_etag": etag,
        "archive_cache_hit": cache_hit,
        "members_seen": 0,
        "documents_written": 0,
        "documents_resumed": 0,
        "duplicate_urls": 0,
        "oversize_members": 0,
        "content_excluded_members": 0,
        "raw_paths": [],
    }
    strip_components = int(collector.get("strip_components", 0))
    max_bytes = int(collector.get("max_member_bytes", 20_000_000))
    max_documents = collector.get("max_documents")
    for member_path, payload in _iter_archive(cache_path, archive_format):
        logical_path = _strip_components(member_path, strip_components)
        if not _included(logical_path, collector):
            continue
        if len(payload) > max_bytes:
            result["oversize_members"] += 1
            continue
        content_exclude_regex = collector.get("content_exclude_regex")
        if content_exclude_regex:
            preview, _, _ = _decode_bytes(payload)
            if re.search(content_exclude_regex, preview):
                result["content_excluded_members"] += 1
                continue
        result["members_seen"] += 1
        source_url = _logical_source_url(source, logical_path)
        if source_url in seen_urls:
            result["duplicate_urls"] += 1
            continue
        seen_urls.add(source_url)
        raw_path, resumed = _save_raw_document(
            root, source, logical_path, source_url, payload, retrieved_at
        )
        result["raw_paths"].append(raw_path)
        result["documents_resumed" if resumed else "documents_written"] += 1
        if max_documents and result["members_seen"] >= int(max_documents):
            break
    return result


def _fetch_bytes(
    session: requests.Session,
    limiter: RateLimiter,
    url: str,
    timeout: tuple[float, float],
) -> tuple[bytes, str | None]:
    limiter.wait()
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def _collect_urls(
    root: Path,
    source: dict[str, Any],
    urls: list[Any],
    session: requests.Session,
    limiter: RateLimiter,
    timeout: tuple[float, float],
    seen_urls: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result: dict[str, Any] = {
        "source_id": source["id"],
        "members_seen": 0,
        "documents_written": 0,
        "documents_resumed": 0,
        "duplicate_urls": 0,
        "raw_paths": [],
    }
    failures: list[dict[str, Any]] = []
    for url_entry in urls:
        if isinstance(url_entry, dict):
            raw_url = url_entry["url"]
            expected_sha256 = url_entry.get("expected_sha256")
        else:
            raw_url = url_entry
            expected_sha256 = source["collector"].get("expected_sha256") if len(urls) == 1 else None
        url = canonicalize_url(raw_url)
        if url in seen_urls:
            result["duplicate_urls"] += 1
            continue
        seen_urls.add(url)
        result["members_seen"] += 1
        try:
            payload, content_type = _fetch_bytes(session, limiter, url, timeout)
            observed_hash = sha256_bytes(payload)
            if expected_sha256 and observed_hash != expected_sha256:
                raise CollectionError(
                    f"SHA-256 mismatch for {url}: expected {expected_sha256}, observed {observed_hash}"
                )
            logical_path = PurePosixPath(url.split("?", 1)[0]).name or f"page-{sha256_text(url)[:8]}.html"
            raw_path, resumed = _save_raw_document(
                root, source, logical_path, url, payload, utc_now(), content_type
            )
            result["raw_paths"].append(raw_path)
            result["documents_resumed" if resumed else "documents_written"] += 1
        except Exception as exc:  # failure is made explicit in the report
            failures.append(
                {
                    "source_id": source["id"],
                    "url": url,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "inaccessible": isinstance(exc, requests.HTTPError)
                    and exc.response is not None
                    and exc.response.status_code in {401, 403, 404, 410, 451},
                    "failed_at": utc_now(),
                }
            )
    return result, failures


def _sitemap_urls(
    source: dict[str, Any],
    session: requests.Session,
    limiter: RateLimiter,
    timeout: tuple[float, float],
) -> list[str]:
    collector = source["collector"]
    payload, _ = _fetch_bytes(session, limiter, collector["url"], timeout)
    soup = BeautifulSoup(payload, "xml")
    urls = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
    patterns = collector.get("url_include") or ["*"]
    excludes = collector.get("url_exclude") or []
    selected = [
        url
        for url in urls
        if any(fnmatch.fnmatch(url, pattern) for pattern in patterns)
        and not any(fnmatch.fnmatch(url, pattern) for pattern in excludes)
    ]
    max_documents = collector.get("max_documents")
    return selected[: int(max_documents)] if max_documents else selected


def collect_all(
    root: str | Path = ".",
    manifest_path: str | Path = "config/sources.yaml",
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = load_source_manifest(root / manifest_path)
    settings = manifest.get("collection", {})
    retries = int(settings.get("retries", 3))
    timeout = (
        float(settings.get("connect_timeout_seconds", 15)),
        float(settings.get("read_timeout_seconds", 120)),
    )
    limiter = RateLimiter(float(settings.get("rate_limit_seconds", 1.0)))
    session = build_session(
        settings.get("user_agent", "SQLMendRAG-university-research/0.1 (+local reproducible collector)"),
        retries,
    )
    seen_urls: set[str] = set()
    failures: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    started_at = utc_now()
    for source in manifest["sources"]:
        if not source.get("enabled", True) or (source_ids and source["id"] not in source_ids):
            continue
        LOGGER.info("Collecting %s", source["id"])
        collector_type = source["collector"]["type"]
        try:
            if collector_type == "archive":
                source_results.append(
                    _collect_archive(root, source, session, limiter, timeout, seen_urls)
                )
            else:
                if collector_type == "single":
                    urls = [source["collector"]["url"]]
                elif collector_type == "url_list":
                    urls = list(source["collector"].get("urls", []))
                else:
                    urls = _sitemap_urls(source, session, limiter, timeout)
                result, url_failures = _collect_urls(
                    root, source, urls, session, limiter, timeout, seen_urls
                )
                source_results.append(result)
                failures.extend(url_failures)
        except Exception as exc:
            status = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response else None
            failures.append(
                {
                    "source_id": source["id"],
                    "url": source["collector"].get("url"),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "http_status": status,
                    "inaccessible": status in {401, 403, 404, 410, 451},
                    "failed_at": utc_now(),
                }
            )
            LOGGER.exception("Collection failed for %s", source["id"])
    report = {
        "started_at": started_at,
        "finished_at": utc_now(),
        "source_count": len(source_results),
        "raw_document_count": sum(
            row.get("documents_written", 0) + row.get("documents_resumed", 0)
            for row in source_results
        ),
        "documents_written": sum(row.get("documents_written", 0) for row in source_results),
        "documents_resumed": sum(row.get("documents_resumed", 0) for row in source_results),
        "duplicate_url_count": sum(row.get("duplicate_urls", 0) for row in source_results),
        "failed_source_count": len({row["source_id"] for row in failures}),
        "failed_url_count": len(failures),
        "inaccessible_source_count": len({row["source_id"] for row in failures if row.get("inaccessible")}),
        "sources": source_results,
        "failures": failures,
    }
    write_json_atomic(root / "reports" / "collection_report.json", report)
    write_jsonl_atomic(root / "reports" / "download_failures.jsonl", failures)
    index_rows: list[dict[str, Any]] = []
    for result in source_results:
        for raw_path in result.get("raw_paths", []):
            raw = read_json(root / raw_path, {})
            index_rows.append(
                {
                    "source_id": raw.get("source_id"),
                    "source_url": raw.get("source_url"),
                    "raw_path": raw_path,
                    "content_hash": raw.get("content_hash"),
                    "retrieved_at": raw.get("retrieved_at"),
                }
            )
    write_jsonl_atomic(root / "data" / "raw" / "collection_index.jsonl", index_rows)
    return report
