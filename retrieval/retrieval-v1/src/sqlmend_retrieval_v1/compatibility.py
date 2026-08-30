"""Conservative dialect and version compatibility classification.

The online functions in this module consume only the safe query projection and
corpus-owned metadata/passage text.  A document snapshot version is not treated
as a feature-support claim: an out-of-range but generic passage remains useful.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .models import CandidatePassage, OnlineQuery


DIALECTS = frozenset({"postgresql", "mysql", "sqlite", "mariadb", "duckdb"})
RELATED_DIALECT_PAIRS = frozenset({frozenset({"mysql", "mariadb"})})
DIALECT_CATEGORIES = ("compatible", "related", "unknown", "incompatible")
VERSION_CATEGORIES = ("compatible", "general", "unknown", "not_applicable", "incompatible")

_VERSION_TOKEN = r"(?<![\w.])v?(\d{1,3}(?:\.\d{1,3}){0,2})(?!\w|%|\.\d)"
_DIALECT_LABEL = r"(?:(?:PostgreSQL|MySQL|SQLite|MariaDB|DuckDB)\s+)?"
_MINIMUM_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b(?:added|introduced|implemented|available|supported)\s+(?:only\s+)?(?:in|since|from|starting\s+(?:in|with)|as\s+of)\s+(?:version\s+)?{_DIALECT_LABEL}{_VERSION_TOKEN}",
        rf"\b(?:PostgreSQL|MySQL|SQLite|MariaDB|DuckDB)\s+starting\s+with\s+(?:version\s+)?{_VERSION_TOKEN}",
        rf"\bsince\s+version\s+{_VERSION_TOKEN}",
        rf"\brequires?\s+(?:version\s+)?{_VERSION_TOKEN}\s+(?:or\s+(?:later|newer|higher)|and\s+later|\+)",
        rf"\b{_VERSION_TOKEN}\s+(?:and|or)\s+(?:later|newer|higher)\b",
    )
)
_MAXIMUM_EXCLUSIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b(?:before|prior\s+to|older\s+than)\s+(?:version\s+)?{_VERSION_TOKEN}",
        rf"\b(?:removed|dropped|no\s+longer\s+(?:available|supported))\s+(?:in|since|from|as\s+of)\s+(?:version\s+)?{_VERSION_TOKEN}",
    )
)
_NEW_FEATURE_CUE = re.compile(
    r"\b(?:new\s+features?|what'?s\s+new|now\s+supports?|was\s+added|"
    r"has\s+been\s+added|introduc(?:e|ed|ing)|newly\s+available)\b",
    re.IGNORECASE,
)
_SIGNATURE_TOKEN_RE = re.compile(r"->>|->|::|[A-Za-z_][A-Za-z_0-9$]*")
_FUNCTION_RE = re.compile(r"\b([A-Za-z_][A-Za-z_0-9$]*)\s*\(")
_GENERIC_FUNCTIONS = frozenset(
    {"cast", "coalesce", "count", "max", "min", "sum", "avg", "values"}
)
_SQL_KEYWORDS = frozenset(
    {
        "all", "and", "as", "asc", "by", "case", "create", "delete", "desc",
        "distinct", "else", "end", "from", "group", "having", "in", "insert",
        "into", "join", "limit", "not", "null", "on", "or", "order", "over",
        "select", "set", "table", "then", "union", "update", "using", "values",
        "when", "where", "with",
    }
)


@dataclass(frozen=True, order=True, slots=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0


@dataclass(frozen=True, slots=True)
class TargetVersion:
    lower_inclusive: Version | None
    upper_exclusive: Version | None
    exact: bool


@dataclass(frozen=True, slots=True)
class VersionDecision:
    category: str
    reason: str
    explicit_bounds: tuple[str, ...] = ()


def _canonical_dialect(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if normalized in DIALECTS else None


def dialect_compatibility(query_dialect: str | None, document_dialect: str | None) -> str:
    query = _canonical_dialect(query_dialect)
    document = _canonical_dialect(document_dialect)
    if query is None or document is None:
        return "unknown"
    if query == document:
        return "compatible"
    if frozenset({query, document}) in RELATED_DIALECT_PAIRS:
        return "related"
    return "incompatible"


def is_wrong_dialect(query_dialect: str | None, document_dialect: str | None) -> bool:
    """Count every explicit canonical dialect mismatch, including MySQL/MariaDB."""

    query = _canonical_dialect(query_dialect)
    document = _canonical_dialect(document_dialect)
    return query is not None and document is not None and query != document


def parse_version(value: str | None) -> Version | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"v?(\d{1,3})(?:\.(\d{1,3}))?(?:\.(\d{1,3}))?", value.strip(), re.IGNORECASE)
    if not match:
        return None
    return Version(*(int(component or 0) for component in match.groups()))


def parse_target_version(value: str | None) -> TargetVersion:
    exact = parse_version(value)
    if exact is not None:
        return TargetVersion(exact, Version(exact.major, exact.minor, exact.patch + 1), True)
    if isinstance(value, str):
        match = re.fullmatch(r"pre[-\s]*v?(\d{1,3})(?:\.(\d{1,3}))?(?:\.(\d{1,3}))?", value.strip(), re.IGNORECASE)
        if match:
            upper = Version(*(int(component or 0) for component in match.groups()))
            return TargetVersion(None, upper, False)
    return TargetVersion(None, None, False)


def _range_upper(value: str | None) -> Version | None:
    if not isinstance(value, str):
        return None
    text = value.strip().casefold().lstrip("v")
    if text.endswith(".x"):
        base = text[:-2].split(".")
        if len(base) == 1 and base[0].isdigit():
            return Version(int(base[0]) + 1, 0, 0)
        if len(base) == 2 and all(part.isdigit() for part in base):
            return Version(int(base[0]), int(base[1]) + 1, 0)
        return None
    parsed = parse_version(text)
    if parsed is None:
        return None
    components = text.split(".")
    if len(components) == 1:
        return Version(parsed.major + 1, 0, 0)
    if len(components) == 2:
        return Version(parsed.major, parsed.minor + 1, 0)
    return Version(parsed.major, parsed.minor, parsed.patch + 1)


def _target_before(target: TargetVersion, boundary: Version) -> bool:
    return target.upper_exclusive is not None and target.upper_exclusive <= boundary


def _target_at_or_after(target: TargetVersion, boundary: Version) -> bool:
    return target.lower_inclusive is not None and target.lower_inclusive >= boundary


def _target_intersects(
    target: TargetVersion,
    lower: Version | None,
    upper: Version | None,
) -> bool:
    if target.lower_inclusive is None and target.upper_exclusive is None:
        return False
    if upper is not None and target.lower_inclusive is not None and target.lower_inclusive >= upper:
        return False
    if lower is not None and target.upper_exclusive is not None and target.upper_exclusive <= lower:
        return False
    return True


def _explicit_constraints(text: str) -> tuple[list[Version], list[Version]]:
    minimums: list[Version] = []
    maximums: list[Version] = []
    for pattern in _MINIMUM_PATTERNS:
        for match in pattern.finditer(text):
            parsed = parse_version(match.group(1))
            if parsed is not None:
                minimums.append(parsed)
    for pattern in _MAXIMUM_EXCLUSIVE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = parse_version(match.group(1))
            if parsed is not None:
                maximums.append(parsed)
    return minimums, maximums


def _diagnostic_signatures(query: OnlineQuery) -> tuple[set[str], set[str], tuple[str, ...]]:
    sql = query.sql or ""
    functions = {
        match.group(1).casefold()
        for match in _FUNCTION_RE.finditer(sql)
        if match.group(1).casefold() not in _GENERIC_FUNCTIONS
    }
    tokens = [token.casefold() for token in _SIGNATURE_TOKEN_RE.findall(sql)]
    distinctive = {
        token
        for token in tokens
        if token not in _SQL_KEYWORDS and (len(token) >= 4 or token in {"->", "->>", "::"})
    }
    leading = tuple(tokens[:2]) if len(tokens) >= 2 else tuple(tokens)
    return functions, distinctive, leading


def _directly_diagnostic(query: OnlineQuery, passage_text: str) -> bool:
    folded = passage_text.casefold()
    functions, distinctive, leading = _diagnostic_signatures(query)
    if any(re.search(rf"(?<![a-z0-9_$]){re.escape(name)}\s*\(", folded) for name in functions):
        return True
    if any(operator in (query.sql or "") and operator in passage_text for operator in ("->>", "->", "::")):
        return True
    passage_tokens = set(token.casefold() for token in _SIGNATURE_TOKEN_RE.findall(passage_text))
    if len(distinctive.intersection(passage_tokens)) >= 2:
        return True
    if len(leading) == 2 and all(token in passage_tokens for token in leading):
        return True
    for exact in (query.error_code, query.sqlstate, query.error_symbol):
        if exact and exact.casefold() in folded:
            return True
    return False


def version_compatibility(query: OnlineQuery, candidate: CandidatePassage) -> VersionDecision:
    query_dialect = _canonical_dialect(query.dialect)
    document_dialect = _canonical_dialect(candidate.dialect)
    if query_dialect is not None and document_dialect is not None and query_dialect != document_dialect:
        return VersionDecision("not_applicable", "version namespaces differ across dialects")

    target = parse_target_version(query.version)
    if target.lower_inclusive is None and target.upper_exclusive is None:
        return VersionDecision("unknown", "query version is absent, current, or not numeric")

    text = "\n".join(value for value in (candidate.title, candidate.section, candidate.text) if value)
    minimums, maximums = _explicit_constraints(text)
    satisfied_minimums = [boundary for boundary in minimums if _target_at_or_after(target, boundary)]
    satisfied_maximums = [boundary for boundary in maximums if _target_before(target, boundary)]
    if satisfied_minimums or satisfied_maximums:
        bounds = tuple(str(boundary) for boundary in (*satisfied_minimums, *satisfied_maximums))
        return VersionDecision("compatible", "target satisfies an explicit passage version boundary", bounds)

    excluded_minimums = [boundary for boundary in minimums if _target_before(target, boundary)]
    excluded_maximums = [boundary for boundary in maximums if _target_at_or_after(target, boundary)]
    if excluded_minimums or excluded_maximums:
        bounds = tuple(str(boundary) for boundary in (*excluded_minimums, *excluded_maximums))
        if _directly_diagnostic(query, text):
            return VersionDecision(
                "compatible",
                "excluding version boundary directly diagnoses a named query feature",
                bounds,
            )
        return VersionDecision("incompatible", "explicit passage version boundary excludes the target", bounds)

    document_lower = parse_version(candidate.version_min)
    document_upper = _range_upper(candidate.version_max)
    if (
        candidate.source_type == "release_notes"
        and _NEW_FEATURE_CUE.search(text)
        and document_lower is not None
    ):
        if _target_before(target, document_lower):
            if _directly_diagnostic(query, text):
                return VersionDecision(
                    "compatible",
                    "future new-feature evidence directly diagnoses a named query feature",
                    (str(document_lower),),
                )
            return VersionDecision("incompatible", "explicit new-feature release scope is newer than the target", (str(document_lower),))
        if _target_at_or_after(target, document_lower):
            return VersionDecision("compatible", "target is at or after the explicit new-feature release scope", (str(document_lower),))

    if document_lower is not None and document_upper is not None and _target_intersects(target, document_lower, document_upper):
        return VersionDecision("compatible", "target intersects the corpus-owned document version range")
    if candidate.version_status == "unknown":
        return VersionDecision("unknown", "corpus version metadata is explicitly unknown")
    if candidate.version_status == "current" and document_lower is None and document_upper is None:
        return VersionDecision("unknown", "current documentation has no explicit numeric bounds")
    return VersionDecision("general", "no explicit passage-level version conflict")


def version_categories(
    query: OnlineQuery, candidates: Iterable[CandidatePassage]
) -> list[VersionDecision]:
    return [version_compatibility(query, candidate) for candidate in candidates]
