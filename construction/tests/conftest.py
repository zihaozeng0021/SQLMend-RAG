from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from sqlmend_pipeline.utils import sha256_text


@pytest.fixture
def document_factory() -> Callable[..., dict[str, Any]]:
    """Return a complete, production-shaped parsed document factory."""

    base: dict[str, Any] = {
        "document_id": "doc-postgresql-16-select",
        "source_id": "postgresql_docs",
        "dialect": "postgresql",
        "vendor_or_project": "PostgreSQL Global Development Group",
        "version": "16.4",
        "version_min": "16.4",
        "version_max": "16.4",
        "version_status": "exact",
        "source_type": "official_docs",
        "source_name": "PostgreSQL 16 documentation",
        "source_url": "https://www.postgresql.org/docs/16/sql-select.html",
        "authority_class": "official_project_documentation",
        "license_or_terms_note": "PostgreSQL documentation license.",
        "retrieved_at": "2026-08-27T00:00:00Z",
        "content_hash": sha256_text("raw source"),
        "title": "SELECT",
        "sections": [
            {
                "section": "SELECT > Synopsis",
                "blocks": [
                    {
                        "type": "prose",
                        "text": (
                            "SELECT reads rows from one or more tables. The WHERE clause "
                            "filters rows before the result is returned to the client."
                        ),
                    },
                    {"type": "code", "text": "```sql\nSELECT id FROM accounts WHERE active;\n```"},
                ],
            }
        ],
    }

    def make(**overrides: Any) -> dict[str, Any]:
        result = copy.deepcopy(base)
        result.update(overrides)
        return result

    return make


@pytest.fixture
def chunk_factory() -> Callable[..., dict[str, Any]]:
    """Return a complete final-corpus chunk factory."""

    def make(index: int = 0, **overrides: Any) -> dict[str, Any]:
        dialect = overrides.pop("dialect", "postgresql")
        text = overrides.pop(
            "text",
            (
                f"Title: SELECT {index}\n\nThis documentation passage number {index} explains how a SQL "
                "query selects rows, applies predicates, orders results, and returns values to the client "
                "without dropping any syntax or compatibility context."
            ),
        )
        source_id = overrides.pop("source_id", f"{dialect}_docs")
        chunk = {
            "chunk_id": f"smr_{dialect}_{index:024d}",
            "document_id": f"doc-{dialect}-{index}",
            "dialect": dialect,
            "vendor_or_project": f"{dialect} project",
            "version": "1.2",
            "version_min": "1.2",
            "version_max": "1.2",
            "version_status": "exact",
            "source_type": "official_docs",
            "source_name": f"{dialect} documentation",
            "source_url": f"https://example.test/{dialect}/{index}",
            "title": f"SELECT {index}",
            "section": "Query syntax",
            "text": text,
            "contains_sql": True,
            "contains_error_code": False,
            "retrieved_at": "2026-08-27T00:00:00Z",
            "content_hash": sha256_text(text),
            "source_id": source_id,
            "authority_class": "official_project_documentation",
            "topic": "syntax",
            "contains_version_or_compatibility": False,
            "chunking_strategy": "structure",
        }
        chunk.update(overrides)
        return chunk

    return make
