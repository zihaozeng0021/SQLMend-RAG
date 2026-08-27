from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from .constants import ALLOWED_DIALECTS
from .utils import iter_jsonl, read_json, relative_posix, sha256_text, write_json_atomic, write_jsonl_atomic

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "title"}
CODE_TAGS = {"pre", "programlisting", "screen", "synopsis", "literallayout"}
PARAGRAPH_TAGS = {
    "p",
    "para",
    "simpara",
    "li",
    "listitem",
    "dt",
    "dd",
    "term",
    "blockquote",
    "remark",
}
TABLE_TAGS = {"table", "informaltable"}
REMOVE_TAGS = {"script", "style", "nav", "footer", "form", "noscript", "svg", "iframe", "button"}
UNWANTED_CLASS_RE = re.compile(
    r"(?:^|[-_ ])(?:nav(?:igation)?|cookie|breadcrumbs?|toolbar|footer|sidebar|skip-link|page-controls?)(?:$|[-_ ])",
    re.I,
)
MYSQL_HELP_RE = re.compile(
    r"INSERT\s+INTO\s+help_topic\s*\([^)]*\)\s*VALUES\s*"
    r"\((\d+),(\d+),'((?:\\.|[^'])*)','((?:\\.|[^'])*)','((?:\\.|[^'])*)','((?:\\.|[^'])*)'\);",
    re.I,
)
MYSQL_CATEGORY_RE = re.compile(
    r"INSERT\s+INTO\s+help_category\s*\([^)]*\)\s*VALUES\s*"
    r"\((\d+),'((?:\\.|[^'])*)',(?:\d+|NULL),'(?:\\.|[^'])*'\);",
    re.I,
)
MYSQL_ERROR_START_RE = re.compile(r"^((?:ER|WARN|OBSOLETE|EE)_[A-Z0-9_]+)(.*)$")


@dataclass
class SectionBuilder:
    path: list[str]
    blocks: list[dict[str, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return " > ".join(part for part in self.path if part)


def _clean_inline(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"[ \t\f\v]+", " ", text).strip()


def _code_text(tag: Tag) -> str:
    text = tag.get_text("\n", strip=False).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    joined = "\n".join(lines)
    sql_hint = bool(re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|FROM|WHERE)\b", joined, re.I))
    language = "sql" if sql_hint else "text"
    return f"```{language}\n{joined}\n```"


def _table_rows(tag: Tag) -> tuple[list[str], list[list[str]]]:
    rows: list[list[str]] = []
    header: list[str] = []
    html_rows = tag.find_all("tr")
    if html_rows:
        for row_index, row in enumerate(html_rows):
            cells = row.find_all(["th", "td"], recursive=False) or row.find_all(["th", "td"])
            values = [_clean_inline(cell.get_text(" ", strip=True)) for cell in cells]
            if not values:
                continue
            if row_index == 0 and row.find("th"):
                header = values
            else:
                rows.append(values)
        return header, rows
    xml_rows = tag.find_all("row")
    for row_index, row in enumerate(xml_rows):
        values = [_clean_inline(cell.get_text(" ", strip=True)) for cell in row.find_all("entry", recursive=False)]
        if not values:
            continue
        if row_index == 0 and (row.find_parent("thead") or tag.find("thead")):
            header = values
        else:
            rows.append(values)
    return header, rows


def table_to_text(tag: Tag) -> str:
    header, rows = _table_rows(tag)
    if not rows and not header:
        return ""
    if not header:
        width = max(len(row) for row in rows)
        header = [f"Column {index + 1}" for index in range(width)]
    output = ["Table:"]
    for row in rows or [header]:
        pairs = []
        for index, value in enumerate(row):
            label = header[index] if index < len(header) else f"Column {index + 1}"
            pairs.append(f"{label}: {value}")
        output.append("- " + " | ".join(pairs))
    return "\n".join(output)


def _markup_heading_level(tag: Tag) -> int:
    if tag.name and re.fullmatch(r"h[1-6]", tag.name.lower()):
        return int(tag.name[1])
    parent = tag.parent
    depth = 1
    while isinstance(parent, Tag):
        name = (parent.name or "").lower()
        match = re.fullmatch(r"(?:sect|refsect)([1-6])", name)
        if match:
            return int(match.group(1))
        if name in {"section", "chapter", "appendix", "refentry", "article"}:
            depth += 1
        parent = parent.parent
    return min(depth, 6)


def _inside(tag: Tag, names: set[str]) -> bool:
    return any(isinstance(parent, Tag) and (parent.name or "").lower() in names for parent in tag.parents)


def parse_markup(text: str, title: str, xml: bool = False) -> list[dict[str, Any]]:
    parser = "xml" if xml else "lxml"
    soup = BeautifulSoup(text, parser)
    for tag in soup.find_all(REMOVE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        # Descendants of a previously decomposed navigation container remain in
        # BeautifulSoup's result snapshot with attrs=None.
        if tag.attrs is None or tag.parent is None:
            continue
        marker = " ".join(
            [str(tag.get("id", "")), " ".join(tag.get("class", [])) if tag.get("class") else ""]
        )
        if marker and UNWANTED_CLASS_RE.search(marker):
            tag.decompose()

    sections: list[SectionBuilder] = []
    current_path = [title]
    current = SectionBuilder(current_path.copy())
    sections.append(current)
    candidates = HEADING_TAGS | CODE_TAGS | PARAGRAPH_TAGS | TABLE_TAGS
    for tag in soup.find_all(lambda candidate: (candidate.name or "").lower() in candidates):
        name = (tag.name or "").lower()
        if _inside(tag, TABLE_TAGS | CODE_TAGS):
            continue
        # SQLite's generated C API pages wrap <pre> in <blockquote>.  Treating
        # both as blocks duplicates the signature once as prose (which can then
        # be split mid-token) and once as intact code.
        if name == "blockquote" and tag.find(lambda child: (child.name or "").lower() in CODE_TAGS):
            continue
        if name in {"li", "listitem", "dd"} and tag.find(
            lambda child: child is not tag and (child.name or "").lower() in PARAGRAPH_TAGS
        ):
            continue
        if name in HEADING_TAGS:
            heading = _clean_inline(tag.get_text(" ", strip=True))
            if not heading or heading == current.name.split(" > ")[-1]:
                continue
            level = _markup_heading_level(tag)
            base = current_path[: max(1, level - 1)]
            current_path = base + [heading]
            current = SectionBuilder(current_path.copy())
            sections.append(current)
            continue
        if name in CODE_TAGS:
            block_text = _code_text(tag)
            block_type = "code"
        elif name in TABLE_TAGS:
            block_text = table_to_text(tag)
            block_type = "table"
        else:
            block_text = _clean_inline(tag.get_text(" ", strip=True))
            if name in {"li", "listitem", "dt", "dd", "term"} and block_text:
                block_text = "- " + block_text
            block_type = "prose"
        if not block_text:
            continue
        if current.blocks and current.blocks[-1]["text"] == block_text:
            continue
        current.blocks.append({"type": block_type, "text": block_text})
    return [
        {"section": section.name or title, "blocks": section.blocks}
        for section in sections
        if section.blocks
    ]


def _markdown_table(lines: list[str]) -> str:
    cells = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    header = cells[0]
    data = cells[2:] if len(cells) > 1 and all(re.fullmatch(r":?-{2,}:?", value) for value in cells[1]) else cells[1:]
    output = ["Table:"]
    for row in data:
        output.append(
            "- "
            + " | ".join(
                f"{header[index] if index < len(header) and header[index] else f'Column {index + 1}'}: {value}"
                for index, value in enumerate(row)
            )
        )
    return "\n".join(output)


def parse_markdown(text: str, title: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if lines and lines[0].strip() == "---":
        try:
            closing = next(index for index in range(1, min(len(lines), 200)) if lines[index].strip() == "---")
            lines = lines[closing + 1 :]
        except StopIteration:
            pass
    paths = [title]
    sections: list[SectionBuilder] = [SectionBuilder(paths.copy())]
    current = sections[0]
    index = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        value = _clean_inline(" ".join(line.strip() for line in paragraph))
        if value:
            current.blocks.append({"type": "prose", "text": value})
        paragraph = []

    while index < len(lines):
        line = lines[index]
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        fence = re.match(r"^\s*(```+|~~~+)\s*([\w+-]*)\s*$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            paths = paths[: max(1, level)] + [heading_text]
            current = SectionBuilder(paths.copy())
            sections.append(current)
            index += 1
            continue
        if fence:
            flush_paragraph()
            marker, language = fence.groups()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not re.match(rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$", lines[index]):
                code_lines.append(lines[index].rstrip())
                index += 1
            code = "\n".join(code_lines).strip("\n")
            if code:
                if not language:
                    language = "sql" if re.search(r"\b(?:SELECT|CREATE|INSERT|UPDATE|DELETE)\b", code, re.I) else "text"
                current.blocks.append({"type": "code", "text": f"```{language}\n{code}\n```"})
            index += 1
            continue
        if (
            "|" in line
            and index + 1 < len(lines)
            and re.match(r"^\s*\|?\s*:?-{2,}", lines[index + 1])
        ):
            flush_paragraph()
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            current.blocks.append({"type": "table", "text": _markdown_table(table_lines)})
            continue
        if not line.strip():
            flush_paragraph()
        elif re.match(r"^\s*(?:[-*+] |\d+[.)] )", line):
            flush_paragraph()
            current.blocks.append({"type": "prose", "text": _clean_inline(line)})
        else:
            paragraph.append(line)
        index += 1
    flush_paragraph()
    return [{"section": section.name, "blocks": section.blocks} for section in sections if section.blocks]


def markdown_frontmatter_title(text: str) -> str | None:
    """Read an explicit Markdown YAML title without treating `layout` as one."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:200]:
        if line.strip() == "---":
            break
        match = re.match(r"^\s*title\s*:\s*(.+?)\s*$", line, re.I)
        if match:
            return match.group(1).strip().strip("\"'")[:500] or None
    return None


def parse_text(text: str, title: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    sections: list[SectionBuilder] = [SectionBuilder([title])]
    current = sections[0]
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        raw = "\n".join(buffer).strip()
        if raw:
            sqlish = len(buffer) > 1 and bool(
                re.search(r"\b(?:SELECT|CREATE|ALTER|INSERT|UPDATE|DELETE|ERROR|SQLSTATE)\b", raw, re.I)
            )
            if sqlish:
                current.blocks.append({"type": "code", "text": f"```text\n{raw}\n```"})
            else:
                current.blocks.append({"type": "prose", "text": _clean_inline(raw)})
        buffer = []

    index = 0
    while index < len(lines):
        line = lines[index]
        if index + 1 < len(lines) and line.strip() and re.fullmatch(r"\s*[=-]{3,}\s*", lines[index + 1]):
            flush()
            current = SectionBuilder([title, line.strip()])
            sections.append(current)
            index += 2
            continue
        if not line.strip():
            flush()
        else:
            buffer.append(line)
        index += 1
    flush()
    return [{"section": section.name, "blocks": section.blocks} for section in sections if section.blocks]


def _mysql_unescape(value: str) -> str:
    replacements = {"n": "\n", "r": "\r", "t": "\t", "'": "'", '"': '"', "\\": "\\"}
    return re.sub(r"\\(.)", lambda match: replacements.get(match.group(1), match.group(1)), value)


def parse_mysql_help_tables(text: str, title: str) -> list[dict[str, Any]]:
    categories = {
        int(match.group(1)): _mysql_unescape(match.group(2))
        for match in MYSQL_CATEGORY_RE.finditer(text)
    }
    sections: list[dict[str, Any]] = []
    for match in MYSQL_HELP_RE.finditer(text):
        _, category_id, name, description, example, url = match.groups()
        name = _mysql_unescape(name)
        description = _mysql_unescape(description).strip()
        example = _mysql_unescape(example).strip()
        url = _mysql_unescape(url).strip()
        blocks: list[dict[str, str]] = []
        if description:
            blocks.append({"type": "prose", "text": description})
        if example:
            blocks.append({"type": "code", "text": f"```sql\n{example}\n```"})
        if not blocks:
            continue
        category = categories.get(int(category_id), "Server-side help")
        section: dict[str, Any] = {
            "section": f"{title} > {category} > {name}",
            "blocks": blocks,
        }
        if url.startswith(("https://", "http://")):
            section["source_url"] = url
        sections.append(section)
    return sections


def parse_mysql_error_messages(text: str, title: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_symbol: str | None = None
    current_sqlstate: str | None = None
    current_number: int | None = None
    next_number: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_symbol, current_sqlstate, current_number, current_lines
        if not current_symbol:
            return
        english: list[str] = []
        capture = False
        for line in current_lines:
            match = re.match(r'^\s*eng\s+"(.*)"\s*$', line)
            if match:
                english.append(match.group(1))
                capture = True
            elif capture and line.startswith((" ", "\t")) and not re.match(r"^\s*[a-z]{3}\s+\"", line):
                english.append(line.strip().strip('"'))
            elif capture:
                break
        message = _mysql_unescape(" ".join(english)).strip() or _clean_inline(" ".join(current_lines))
        details = [f"Error symbol: {current_symbol}"]
        if current_number is not None:
            details.append(f"Error number: {current_number}")
        if current_sqlstate:
            details.append(f"SQLSTATE: {current_sqlstate}")
        if message:
            details.append(f"Message: {message}")
        sections.append(
            {
                "section": f"{title} > {current_symbol}",
                "blocks": [{"type": "prose", "text": "\n".join(details)}],
            }
        )
        current_symbol, current_sqlstate, current_number, current_lines = None, None, None, []

    for line in text.splitlines():
        offset = re.match(r"^\s*#?\s*(?:start-error-number|skip-to-error-number)\s+(\d+)\s*$", line)
        if offset:
            flush()
            next_number = int(offset.group(1))
            continue
        start = MYSQL_ERROR_START_RE.match(line.strip())
        if start:
            flush()
            current_symbol, rest = start.groups()
            state_match = re.search(r"\b[0-9A-Z]{5}\b", rest.upper())
            current_sqlstate = state_match.group(0) if state_match else None
            current_number = next_number
            if next_number is not None:
                next_number += 1
        elif current_symbol:
            current_lines.append(line)
    flush()
    return sections


def parse_raw_record(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("dialect") not in ALLOWED_DIALECTS:
        raise ValueError(f"Unknown dialect in raw record: {raw.get('dialect')!r}")
    content_format = raw.get("content_format", "text")
    title = raw.get("original_title") or "Untitled documentation"
    logical_path = str(raw.get("logical_source_path") or "").replace("\\", "/")
    content = raw["content"]
    if raw.get("dialect") == "postgresql" and content_format == "xml" and raw.get("version"):
        version = str(raw["version"])
        content = content.replace("&majorversion;", version.split(".", 1)[0]).replace("&version;", version)
    if logical_path.endswith("scripts/fill_help_tables.sql") or logical_path.endswith("fill_help_tables.sql"):
        title = f"{raw.get('source_name') or 'MySQL'} server-side help"
        sections = parse_mysql_help_tables(content, title)
    elif PurePathName(logical_path) in {"messages_to_clients.txt", "messages_to_error_log.txt", "errmsg-utf8.txt"}:
        title = f"{raw.get('source_name') or 'MySQL'} error messages"
        sections = parse_mysql_error_messages(content, title)
    elif content_format == "html":
        sections = parse_markup(content, title, xml=False)
    elif content_format == "xml":
        sections = parse_markup(content, title, xml=True)
        if not sections:  # SGML with unresolved entities is often more tolerant in HTML mode.
            sections = parse_markup(content, title, xml=False)
    elif content_format == "markdown":
        title = markdown_frontmatter_title(content) or title
        sections = parse_markdown(content, title)
    else:
        sections = parse_text(content, title)
    return {
        key: raw.get(key)
        for key in (
            "document_id",
            "source_id",
            "dialect",
            "vendor_or_project",
            "version",
            "version_min",
            "version_max",
            "version_status",
            "source_type",
            "source_name",
            "source_url",
            "authority_class",
            "license_or_terms_note",
            "retrieved_at",
            "content_hash",
            "local_raw_file_path",
            "logical_source_path",
        )
    } | {
        "title": title,
        "sections": sections,
        "parsed_content_hash": sha256_text(
            "\n".join(block["text"] for section in sections for block in section["blocks"])
        ),
    }


def PurePathName(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def parse_all(root: str | Path = ".") -> dict[str, Any]:
    root = Path(root).resolve()
    collection_index = root / "data" / "raw" / "collection_index.jsonl"
    if collection_index.exists():
        paths = [root / row["raw_path"] for row in iter_jsonl(collection_index)]
    else:
        paths = []
        for dialect in ALLOWED_DIALECTS:
            paths.extend(sorted((root / "data" / "raw" / dialect).glob("*.json")))
    documents: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for path in paths:
        try:
            documents.append(parse_raw_record(read_json(path)))
        except Exception as exc:
            failures.append(
                {
                    "raw_path": relative_posix(path, root),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    output = root / "data" / "interim" / "parsed_documents.jsonl"
    write_jsonl_atomic(output, documents)
    report = {
        "raw_document_count": len(paths),
        "parsed_document_count": len(documents),
        "parse_failure_count": len(failures),
        "failures": failures,
    }
    write_json_atomic(root / "reports" / "parse_report.json", report)
    # Always replace the sidecar so a successful rerun cannot leave stale
    # failures from an earlier parser revision.
    write_jsonl_atomic(root / "reports" / "parse_failures.jsonl", failures)
    return report
