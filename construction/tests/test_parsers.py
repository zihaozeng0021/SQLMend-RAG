from __future__ import annotations

from bs4 import BeautifulSoup

from sqlmend_pipeline.clean import clean_document, is_boilerplate, is_navigation_table
from sqlmend_pipeline.parsers import (
    parse_all,
    parse_markdown,
    parse_markup,
    parse_mysql_error_messages,
    parse_mysql_help_tables,
    parse_text,
    markdown_frontmatter_title,
    table_to_text,
)


def flatten(sections: list[dict]) -> str:
    return "\n".join(block["text"] for section in sections for block in section["blocks"])


def test_parse_all_clears_stale_failure_sidecar(tmp_path) -> None:
    raw_dir = tmp_path / "data" / "raw"
    reports_dir = tmp_path / "reports"
    raw_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    (raw_dir / "collection_index.jsonl").write_text("", encoding="utf-8")
    failure_path = reports_dir / "parse_failures.jsonl"
    failure_path.write_text('{"error":"stale"}\n', encoding="utf-8")

    report = parse_all(tmp_path)

    assert report["parse_failure_count"] == 0
    assert failure_path.read_text(encoding="utf-8") == ""


def test_html_parser_removes_navigation_and_preserves_code_and_table() -> None:
    html = """
    <html><head><title>SELECT reference</title></head><body>
      <nav>Previous Next Cookies</nav><h1>SELECT</h1>
      <p>Use SELECT to retrieve rows.</p><pre>SELECT id FROM users;</pre>
      <table><tr><th>Name</th><th>Meaning</th></tr><tr><td>ALL</td><td>Keep duplicates</td></tr></table>
    </body></html>
    """
    text = flatten(parse_markup(html, "SELECT reference"))
    assert "Previous Next Cookies" not in text
    assert "```sql\nSELECT id FROM users;\n```" in text
    assert "Name: ALL | Meaning: Keep duplicates" in text


def test_html_parser_does_not_duplicate_pre_wrapped_by_blockquote() -> None:
    html = "<h1>API</h1><blockquote><pre>sqlite3_open(\"db\", &amp;db);</pre></blockquote>"

    text = flatten(parse_markup(html, "API"))

    assert text.count("sqlite3_open") == 1
    assert "```text" in text


def test_cleaner_removes_sqlite_bulleted_navigation_section() -> None:
    document = {
        "sections": [
            {
                "section": "API constant",
                "blocks": [{"type": "prose", "text": label} for label in ("- Home", "- Documentation", "- Search")],
            },
            {
                "section": "API constant > Description",
                "blocks": [{"type": "prose", "text": "SQLITE_STATIC keeps the supplied pointer unchanged."}],
            },
        ]
    }

    cleaned = clean_document(document)

    assert is_boilerplate("- Home")
    assert len(cleaned["sections"]) == 1
    assert "SQLITE_STATIC" in flatten(cleaned["sections"])


def test_cleaner_recognizes_generated_prev_up_home_next_table() -> None:
    navigation = (
        "Table:\n- Column 1: Prev | Column 2: Up | Column 3: Next\n"
        "- Column 1: CREATE TABLE | Column 2: Home | Column 3: ALTER TABLE"
    )

    assert is_navigation_table(navigation)


def test_xml_sgml_parser_preserves_section_and_programlisting() -> None:
    xml = """<article><title>Commands</title><sect1><title>CREATE TABLE</title>
    <para>Creates a table.</para><programlisting>CREATE TABLE t (id INT);</programlisting>
    </sect1></article>"""
    sections = parse_markup(xml, "Commands", xml=True)
    assert any("CREATE TABLE" in section["section"] for section in sections)
    assert "CREATE TABLE t (id INT);" in flatten(sections)


def test_markdown_parser_keeps_fenced_sql_and_converts_table() -> None:
    markdown = """---
title: INSERT
---
# INSERT
Adds rows.

```sql
INSERT INTO t VALUES (1);
```

| Parameter | Meaning |
|---|---|
| IGNORE | Ignore selected errors |
"""
    text = flatten(parse_markdown(markdown, "INSERT"))
    assert "```sql\nINSERT INTO t VALUES (1);\n```" in text
    assert "Parameter: IGNORE | Meaning: Ignore selected errors" in text
    assert "title: INSERT" not in text


def test_markdown_table_accepts_two_hyphen_alignment_rows() -> None:
    markdown = "# Functions\n\n| Function | Description |\n|:--|:--|\n| f(x) | Returns x |\n"

    text = flatten(parse_markdown(markdown, "Functions"))

    assert "Table:\n- Function: f(x) | Description: Returns x" in text


def test_markdown_frontmatter_title_ignores_layout_key() -> None:
    markdown = "---\nlayout: docu\ntitle: Quack Setup\n---\n\nSetup guide.\n"

    assert markdown_frontmatter_title(markdown) == "Quack Setup"


def test_plain_text_parser_detects_underlined_heading_and_sql() -> None:
    value = """Transactions
============

Use a transaction for atomic changes.

BEGIN;
UPDATE t SET a = 1;
COMMIT;
"""
    sections = parse_text(value, "Manual")
    assert any("Transactions" in section["section"] for section in sections)
    assert "UPDATE t SET a = 1" in flatten(sections)


def test_table_conversion_repeats_header_relationship() -> None:
    soup = BeautifulSoup(
        "<table><tr><th>Code</th><th>Message</th></tr><tr><td>23505</td><td>unique violation</td></tr></table>",
        "html.parser",
    )
    assert table_to_text(soup.table) == "Table:\n- Code: 23505 | Message: unique violation"


def test_mysql_help_sql_parser_extracts_topic_description_example_and_url() -> None:
    sql = """
    INSERT INTO help_category (help_category_id,name,parent_category_id,url) VALUES (1,'Functions',0,'');
    INSERT INTO help_topic (help_topic_id,help_category_id,name,description,example,url) VALUES (7,1,'ABS','Syntax:\\nABS(x)\\n\\nReturns an absolute value.','SELECT ABS(-2);','https://dev.mysql.com/doc/refman/8.4/en/mathematical-functions.html');
    """
    sections = parse_mysql_help_tables(sql, "MySQL 8.4")
    assert len(sections) == 1
    assert sections[0]["section"].endswith("Functions > ABS")
    assert sections[0]["source_url"].startswith("https://dev.mysql.com/")
    assert sections[0]["blocks"][1]["text"] == "```sql\nSELECT ABS(-2);\n```"


def test_mysql_error_parser_retains_number_sqlstate_symbol_and_placeholder() -> None:
    source = """start-error-number 1000
ER_FIRST HY000
        eng "Failure for %s"
ER_SECOND 23000
        eng "Duplicate %-.64s"
skip-to-error-number 2000
ER_THIRD
        eng "Third failure"
"""
    sections = parse_mysql_error_messages(source, "Errors")
    first = sections[0]["blocks"][0]["text"]
    third = sections[2]["blocks"][0]["text"]
    assert "ER_FIRST" in first and "1000" in first and "HY000" in first and "%s" in first
    assert "Error number: 2000" in third
