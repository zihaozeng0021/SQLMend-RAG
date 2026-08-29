"""Deterministic SQL-aware lexical tokenization without stemming or stopword removal."""

from __future__ import annotations

import re

TOKENIZER_VERSION = "sqlmend-lexical-v1"

_TOKEN_RE = re.compile(
    r"""
    (?:--[^\r\n]*|/\*.*?\*/)|                         # comments, retained as terms below
    (?:'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`)| # quoted strings/identifiers
    (?:->>|->|::|<=|>=|<>|!=|:=|=>|\|\||&&)|          # multi-character operators
    (?:\$[A-Za-z_][A-Za-z0-9_$]*|\$\d+|:[A-Za-z_][A-Za-z0-9_$]*|@[A-Za-z_][A-Za-z0-9_$]*)|
    (?:\d+(?:\.\d+){1,})|                            # versions such as 3.35.0
    (?:[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)+)| # qualified names
    (?:[A-Za-z_][A-Za-z0-9_$]*)|                       # identifiers/words/error symbols
    (?:\d+(?:\.\d+)?)|                               # numbers/error codes
    (?:[+\-*/%<>=~!&|^(),.;\[\]{}])                   # meaningful punctuation/operators
    """,
    re.VERBOSE | re.DOTALL,
)


def tokenize(text: str, *, lowercase: bool = True) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    tokens = [match.group(0) for match in _TOKEN_RE.finditer(normalized)]
    if lowercase:
        tokens = [token.lower() for token in tokens]
    return tokens

