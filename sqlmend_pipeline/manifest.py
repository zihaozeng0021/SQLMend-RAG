from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import (
    ALLOWED_DIALECTS,
    AUTHORITY_CLASSES,
    REQUIRED_SOURCE_FIELDS,
    SOURCE_TYPES,
    VERSION_STATUSES,
)
from .utils import load_yaml


class ManifestError(ValueError):
    pass


def load_source_manifest(path: str | Path = "config/sources.yaml") -> dict[str, Any]:
    manifest = load_yaml(path)
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ManifestError("sources.yaml must contain a non-empty 'sources' list")
    seen_ids: set[str] = set()
    seen_collection_urls: set[tuple[str, str]] = set()
    errors: list[str] = []
    for index, source in enumerate(sources):
        label = source.get("id", f"entry #{index + 1}")
        missing = [field for field in ("id", *REQUIRED_SOURCE_FIELDS) if not source.get(field)]
        if missing:
            errors.append(f"{label}: missing {', '.join(missing)}")
        if source.get("id") in seen_ids:
            errors.append(f"{label}: duplicate source id")
        seen_ids.add(source.get("id"))
        if source.get("dialect") not in ALLOWED_DIALECTS:
            errors.append(f"{label}: invalid dialect {source.get('dialect')!r}")
        if source.get("source_type") not in SOURCE_TYPES:
            errors.append(f"{label}: invalid source_type {source.get('source_type')!r}")
        if source.get("authority_class") not in AUTHORITY_CLASSES:
            errors.append(f"{label}: invalid authority_class {source.get('authority_class')!r}")
        status = source.get("version_status", "unknown")
        if status not in VERSION_STATUSES:
            errors.append(f"{label}: invalid version_status {status!r}")
        collector = source.get("collector")
        if not isinstance(collector, dict) or collector.get("type") not in {
            "archive",
            "single",
            "url_list",
            "sitemap",
        }:
            errors.append(f"{label}: collector must have a supported type")
        elif collector.get("type") in {"archive", "single", "sitemap"}:
            url = collector.get("url")
            if not url:
                errors.append(f"{label}: collector URL is required")
            key = (source.get("dialect", ""), url or "")
            if key in seen_collection_urls:
                errors.append(f"{label}: duplicate collection URL for this dialect")
            seen_collection_urls.add(key)
    if errors:
        raise ManifestError("Invalid source manifest:\n- " + "\n- ".join(errors))
    return manifest


def source_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {source["id"]: source for source in manifest["sources"]}


def public_source_record(source: dict[str, Any]) -> dict[str, Any]:
    """Return the auditable fields that are copied into raw records/reports."""
    return {
        "source_id": source["id"],
        "source_name": source["source_name"],
        "source_type": source["source_type"],
        "vendor_or_project": source["vendor_or_project"],
        "dialect": source["dialect"],
        "base_url": source["base_url"],
        "license_or_terms_note": source["license_or_terms_note"],
        "authority_class": source["authority_class"],
        "collector": source.get("collector_name", source["collector"]["type"]),
        "version": source.get("version"),
        "version_min": source.get("version_min"),
        "version_max": source.get("version_max"),
        "version_status": source.get("version_status", "unknown"),
    }
