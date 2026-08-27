from __future__ import annotations

from sqlmend_pipeline.chunking import fixed_chunks, select_balanced, structure_chunks
from sqlmend_pipeline.constants import REQUIRED_CORPUS_FIELDS
from sqlmend_pipeline.statistics import calculate_statistics


def document(dialect: str = "postgresql") -> dict:
    return {
        "document_id": f"doc_{dialect}_1",
        "source_id": f"source_{dialect}",
        "dialect": dialect,
        "vendor_or_project": dialect,
        "version": "1.0",
        "version_min": "1.0",
        "version_max": "1.0",
        "version_status": "exact",
        "source_type": "official_docs",
        "source_name": f"{dialect} docs",
        "source_url": f"https://example.test/{dialect}/manual",
        "title": "SELECT statement",
        "retrieved_at": "2026-08-27T00:00:00Z",
        "authority_class": "official_project_documentation",
        "sections": [
            {
                "section": "SELECT > Examples",
                "blocks": [
                    {"type": "prose", "text": "This example retrieves rows and explains the predicate. " * 8},
                    {"type": "code", "text": "```sql\nSELECT id FROM t WHERE id > 10;\n```"},
                    {"type": "prose", "text": "The result includes matching identifiers only. " * 8},
                ],
            }
        ],
    }


STRUCTURE_CONFIG = {
    "target_words": 55,
    "minimum_words": 20,
    "maximum_words": 90,
    "overlap_words": 8,
    "keep_code_blocks_intact": True,
    "keep_tables_intact": True,
}


def test_structure_chunks_have_stable_schema_and_keep_code_with_explanation() -> None:
    first = structure_chunks(document(), STRUCTURE_CONFIG)
    second = structure_chunks(document(), STRUCTURE_CONFIG)
    assert [row["chunk_id"] for row in first] == [row["chunk_id"] for row in second]
    assert set(REQUIRED_CORPUS_FIELDS).issubset(first[0])
    code_chunks = [row for row in first if "SELECT id FROM t" in row["text"]]
    assert len(code_chunks) == 1
    assert code_chunks[0]["text"].count("```") == 2
    assert "retrieves rows" in code_chunks[0]["text"]


def test_large_table_is_split_only_between_rows_and_repeats_table_marker() -> None:
    doc = document()
    doc["sections"] = [
        {
            "section": "Codes",
            "blocks": [
                {
                    "type": "table",
                    "text": "Table:\n" + "\n".join(f"- Code: E{i} | Meaning: " + "word " * 20 for i in range(20)),
                }
            ],
        }
    ]
    chunks = structure_chunks(doc, STRUCTURE_CONFIG)
    assert len(chunks) > 1
    assert all("Table:" in chunk["text"] for chunk in chunks)
    assert sum(chunk["text"].count("- Code:") for chunk in chunks) == 20


def test_fixed_size_baseline_uses_overlap_and_stable_ids() -> None:
    config = {"size_words": 40, "minimum_words": 10, "overlap_words": 5}
    chunks = fixed_chunks(document(), config)
    assert len(chunks) >= 2
    assert all(chunk["chunking_strategy"] == "fixed" for chunk in chunks)
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)


def test_hierarchical_balancing_prevents_release_notes_from_dominating() -> None:
    chunks = []
    for dialect in ("postgresql", "mysql", "sqlite", "mariadb", "duckdb"):
        for index in range(30):
            topic = "release_notes" if index < 24 else "syntax"
            chunks.append(
                {
                    "chunk_id": f"{dialect}_{index}",
                    "dialect": dialect,
                    "source_id": f"s_{dialect}",
                    "source_type": "release_notes" if topic == "release_notes" else "official_docs",
                    "version": str(index) if topic == "release_notes" else "current",
                    "version_status": "exact",
                    "topic": topic,
                }
            )
    selected, _ = select_balanced(
        chunks,
        {"enabled": True, "target_chunks_per_dialect": 10, "minimum_total_chunks": 50},
    )
    for dialect in ("postgresql", "mysql", "sqlite", "mariadb", "duckdb"):
        dialect_rows = [row for row in selected if row["dialect"] == dialect]
        assert sum(row["topic"] == "syntax" for row in dialect_rows) >= 4


def test_statistics_calculation_reports_core_metrics() -> None:
    chunks = structure_chunks(document(), STRUCTURE_CONFIG)
    stats = calculate_statistics(chunks, raw_documents=[{"dialect": "postgresql"}], cleaned_document_count=1)
    assert stats["raw_document_count"] == 1
    assert stats["cleaned_document_count"] == 1
    assert stats["final_chunk_count"] == len(chunks)
    assert stats["total_word_count"] > 0
    assert stats["chunks_per_dialect"]["postgresql"] == len(chunks)
    assert stats["version_known_percentage"] == 100.0

