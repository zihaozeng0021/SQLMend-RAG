from __future__ import annotations

from sqlmend_pipeline.chunking import fixed_chunks, make_chunk, structure_chunks
from sqlmend_pipeline.constants import REQUIRED_CORPUS_FIELDS
from sqlmend_pipeline.utils import word_count


STRUCTURE_CONFIG = {
    "target_words": 45,
    "minimum_words": 10,
    "maximum_words": 60,
    "overlap_words": 5,
    "keep_code_blocks_intact": True,
    "keep_tables_intact": True,
}


def test_structure_chunks_keep_code_with_explanation_and_stable_ids(document_factory) -> None:
    document = document_factory()

    first = structure_chunks(document, STRUCTURE_CONFIG)
    second = structure_chunks(document, STRUCTURE_CONFIG)

    assert first
    assert [chunk["chunk_id"] for chunk in first] == [chunk["chunk_id"] for chunk in second]
    sql_chunk = next(chunk for chunk in first if "SELECT id FROM accounts" in chunk["text"])
    assert "SELECT reads rows" in sql_chunk["text"]
    assert sql_chunk["text"].count("```") == 2
    assert sql_chunk["contains_sql"] is True
    assert sql_chunk["text"].startswith("Title: SELECT\nSection: SELECT > Synopsis")


def test_structure_chunks_keep_table_headers_and_rows_together(document_factory) -> None:
    document = document_factory(
        title="Error codes",
        sections=[
            {
                "section": "Error codes > Constraint errors",
                "blocks": [
                    {"type": "prose", "text": "The following table maps each code to its meaning."},
                    {
                        "type": "table",
                        "text": (
                            "Table:\n- Code: SQLITE_CONSTRAINT | Meaning: constraint failed\n"
                            "- Code: SQLITE_BUSY | Meaning: database is locked"
                        ),
                    },
                ],
            }
        ],
    )

    chunks = structure_chunks(document, STRUCTURE_CONFIG)
    table_chunk = next(chunk for chunk in chunks if "SQLITE_CONSTRAINT" in chunk["text"])

    assert "Code: SQLITE_CONSTRAINT | Meaning: constraint failed" in table_chunk["text"]
    assert "Code: SQLITE_BUSY | Meaning: database is locked" in table_chunk["text"]
    assert "maps each code" in table_chunk["text"]


def test_protected_code_may_exceed_maximum_instead_of_being_split(document_factory) -> None:
    long_sql = "SELECT " + ", ".join(f"column_{index}" for index in range(100)) + " FROM large_table;"
    document = document_factory(
        sections=[
            {
                "section": "Long syntax",
                "blocks": [{"type": "code", "text": f"```sql\n{long_sql}\n```"}],
            }
        ]
    )

    chunks = structure_chunks(document, STRUCTURE_CONFIG)

    assert len(chunks) == 1
    assert chunks[0]["text"].count("```") == 2
    assert word_count(chunks[0]["text"]) > STRUCTURE_CONFIG["maximum_words"]


def test_fixed_size_baseline_uses_overlap_and_marks_strategy(document_factory) -> None:
    words = [f"token{index}" for index in range(55)]
    document = document_factory(
        title="Baseline",
        sections=[{"section": "All", "blocks": [{"type": "prose", "text": " ".join(words)}]}],
    )

    chunks = fixed_chunks(document, {"size_words": 20, "minimum_words": 5, "overlap_words": 5})

    assert len(chunks) >= 3
    assert all(chunk["chunking_strategy"] == "fixed" for chunk in chunks)
    bodies = [chunk["text"].split("\n\n", 1)[1].split() for chunk in chunks]
    assert bodies[0][-5:] == bodies[1][:5]


def test_release_note_chunk_gets_exact_version_from_section(document_factory) -> None:
    document = document_factory(
        source_type="release_notes",
        version=None,
        version_min=None,
        version_max=None,
        version_status="unknown",
    )

    chunk = make_chunk(document, "Release 1.4.2", "Title: Release\n\nFixed parser behavior.", "structure", 0, 0)

    assert chunk["version"] == "1.4.2"
    assert chunk["version_min"] == "1.4.2"
    assert chunk["version_max"] == "1.4.2"
    assert chunk["version_status"] == "exact"


def test_make_chunk_emits_required_schema_and_deterministic_hash(document_factory) -> None:
    document = document_factory()
    text = (
        "Title: Errors\n\nSQLSTATE 23505 indicates a duplicate key error.\n\n"
        "INSERT INTO accounts(id) VALUES (1);"
    )

    first = make_chunk(document, "Errors", text, "structure", 1, 2)
    second = make_chunk(document, "Errors", text, "structure", 1, 2)

    assert set(REQUIRED_CORPUS_FIELDS) <= first.keys()
    assert first["chunk_id"] == second["chunk_id"]
    assert first["content_hash"] == second["content_hash"]
    assert first["contains_sql"] is True
    assert first["contains_error_code"] is True


def test_short_adjacent_signature_and_parameters_are_joined(document_factory) -> None:
    document = document_factory(
        title="Function API",
        sections=[
            {
                "section": "Function API > Signature",
                "blocks": [{"type": "code", "text": "```sql\nINTEGER add_one(INTEGER value)\n```"}],
            },
            {
                "section": "Function API > Parameters",
                "blocks": [{"type": "prose", "text": "value: input integer."}],
            },
        ],
    )
    config = {**STRUCTURE_CONFIG, "minimum_words": 30}

    chunks = structure_chunks(document, config)

    assert len(chunks) == 1
    assert "INTEGER add_one" in chunks[0]["text"]
    assert "value: input integer" in chunks[0]["text"]
    assert chunks[0]["text"].count("```") == 2


def test_minimum_size_uses_body_not_long_synthetic_heading(document_factory) -> None:
    document = document_factory(
        title="A very long generated documentation title with many repeated contextual words",
        sections=[
            {
                "section": "A very long generated documentation title > Overview > Navigation > Index",
                "blocks": [{"type": "prose", "text": "See the guide."}],
            }
        ],
    )

    assert structure_chunks(document, STRUCTURE_CONFIG) == []


def test_short_siblings_are_paired_without_cascading_section_headers(document_factory) -> None:
    sections = [
        {
            "section": f"API > function_{index} > Description",
            "blocks": [{"type": "prose", "text": f"Returns result number {index} for the supplied value."}],
        }
        for index in range(6)
    ]
    document = document_factory(title="API", sections=sections)
    config = {**STRUCTURE_CONFIG, "minimum_words": 20}

    chunks = structure_chunks(document, config)

    assert len(chunks) == 3
    assert all(chunk["section"].count("adjacent subsections") <= 1 for chunk in chunks)
    assert all(chunk["text"].count("Related subsection:") <= 1 for chunk in chunks)
