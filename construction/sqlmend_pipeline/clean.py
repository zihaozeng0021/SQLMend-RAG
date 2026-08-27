from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .utils import iter_jsonl, normalize_for_hash, sha256_text, word_count, write_json_atomic, write_jsonl_atomic

BOILERPLATE_EXACT = {
    "home",
    "menu",
    "about",
    "documentation",
    "docs",
    "download",
    "license",
    "support",
    "purchase",
    "search",
    "skip to main content",
    "skip navigation",
    "previous next",
    "previous page next page",
    "edit this page",
    "on this page",
    "table of contents",
    "accept all cookies",
    "cookie settings",
    "back to top",
}
BOILERPLATE_RE = re.compile(
    r"^(?:home\s*[›»/]\s*)?(?:documentation|docs)\s*[›»/]\s*$|"
    r"^(?:previous|next|up|home)(?:\s+(?:previous|next|up|home))*$",
    re.I,
)
HTML_FRAGMENT_RE = re.compile(r"</?[A-Za-z][^>]{0,200}>")
NAVIGATION_TABLE_TERMS = ("prev", "up", "home", "next")


def clean_prose(text: str) -> str:
    text = html.unescape(text).replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"\{%\s*@marketo/form\b.*?%\}", " ", text, flags=re.I | re.S)
    text = re.sub(r"\{%\s*(?:link|post_url)\s+([^%]+?)\s*%\}", r"\1", text)
    text = re.sub(r"\{%\s*hint\s+style=[\"']?(warning|danger)[\"']?\s*%\}", "Warning: ", text, flags=re.I)
    text = re.sub(r"\{%\s*hint\s+style=[\"']?info[\"']?\s*%\}", "Note: ", text, flags=re.I)
    text = re.sub(r"\{%\s*tab\s+title=[\"']([^\"']+)[\"']\s*%\}", r"\1: ", text, flags=re.I)
    text = re.sub(r"\{%\s*(?:end\w+|tabs|code|include)\b.*?%\}", " ", text, flags=re.I | re.S)
    text = re.sub(r"\{\{\s*site\.[^}]+\}\}", " ", text)
    text = re.sub(r"_?\{This page is (?:licensed|Copyright):.*?\}\s*", " ", text, flags=re.I)
    text = HTML_FRAGMENT_RE.sub(" ", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_atomic(text: str) -> str:
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def is_boilerplate(text: str) -> bool:
    normalized = normalize_for_hash(text)
    normalized = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", normalized)
    return normalized in BOILERPLATE_EXACT or bool(BOILERPLATE_RE.fullmatch(normalized))


def is_navigation_table(text: str) -> bool:
    normalized = normalize_for_hash(text)
    lines = [line for line in normalized.splitlines() if line.strip()]
    return len(lines) <= 6 and all(re.search(rf"\b{term}\b", normalized) for term in NAVIGATION_TABLE_TERMS)


def clean_document(document: dict[str, Any]) -> dict[str, Any]:
    output = dict(document)
    clean_sections: list[dict[str, Any]] = []
    global_seen: set[str] = set()
    removed = 0
    for section in document.get("sections", []):
        blocks: list[dict[str, str]] = []
        local_seen: set[str] = set()
        for block in section.get("blocks", []):
            block_type = block.get("type", "prose")
            text = clean_atomic(block.get("text", "")) if block_type in {"code", "table"} else clean_prose(block.get("text", ""))
            fingerprint = normalize_for_hash(text)
            if not text or is_boilerplate(text) or (block_type == "table" and is_navigation_table(text)):
                removed += 1
                continue
            # Repeated contents/navigation fragments within one page are removed, while
            # code/table blocks remain tied to their local explanation.
            if fingerprint in local_seen or (block_type == "prose" and word_count(text) < 15 and fingerprint in global_seen):
                removed += 1
                continue
            local_seen.add(fingerprint)
            global_seen.add(fingerprint)
            blocks.append({"type": block_type, "text": text})
        if blocks:
            clean_sections.append({"section": clean_prose(section.get("section", "")), "blocks": blocks})
    output["sections"] = clean_sections
    output["cleaned_content_hash"] = sha256_text(
        "\n".join(block["text"] for section in clean_sections for block in section["blocks"])
    )
    output["cleaning"] = {"blocks_removed": removed}
    return output


def clean_all(root: str | Path = ".") -> dict[str, Any]:
    root = Path(root).resolve()
    input_path = root / "data" / "interim" / "parsed_documents.jsonl"
    documents: list[dict[str, Any]] = []
    empty_documents: list[str] = []
    blocks_removed = 0
    for document in iter_jsonl(input_path):
        cleaned = clean_document(document)
        blocks_removed += cleaned["cleaning"]["blocks_removed"]
        if cleaned["sections"]:
            documents.append(cleaned)
        else:
            empty_documents.append(document["document_id"])
    write_jsonl_atomic(root / "data" / "interim" / "cleaned_documents.jsonl", documents)
    report = {
        "parsed_document_count": len(documents) + len(empty_documents),
        "cleaned_document_count": len(documents),
        "empty_document_count": len(empty_documents),
        "blocks_removed": blocks_removed,
        "empty_document_ids": empty_documents,
    }
    write_json_atomic(root / "reports" / "cleaning_report.json", report)
    return report
