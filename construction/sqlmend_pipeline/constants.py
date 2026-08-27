from __future__ import annotations

ALLOWED_DIALECTS = ("postgresql", "mysql", "sqlite", "mariadb", "duckdb")
VERSION_STATUSES = ("exact", "range", "current", "legacy", "unknown")
SOURCE_TYPES = (
    "official_docs",
    "release_notes",
    "migration_guide",
    "error_reference",
    "project_docs",
    "other",
)
AUTHORITY_CLASSES = (
    "official_project_documentation",
    "official_release_documentation",
    "project_maintained_technical_documentation",
    "community_documentation",
)

REQUIRED_SOURCE_FIELDS = (
    "source_name",
    "source_type",
    "vendor_or_project",
    "dialect",
    "base_url",
    "retrieved_at",
    "license_or_terms_note",
    "collector",
    "authority_class",
)

REQUIRED_CORPUS_FIELDS = (
    "chunk_id",
    "document_id",
    "dialect",
    "vendor_or_project",
    "version",
    "version_min",
    "version_max",
    "version_status",
    "source_type",
    "source_name",
    "source_url",
    "title",
    "section",
    "text",
    "contains_sql",
    "contains_error_code",
    "retrieved_at",
    "content_hash",
)

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}
