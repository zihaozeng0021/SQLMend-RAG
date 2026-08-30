"""Small internal data contracts shared by retrieval components."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SearchResult:
    query_id: str
    chunk_id: str
    rank: int
    score: float
    run_tag: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SerializedQuery:
    query_id: str
    source_fields_used: tuple[str, ...]
    serialized_text: str
    serialized_text_sha256: str
    serializer_version: str = "sqlmend-query-v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_fields_used"] = list(self.source_fields_used)
        return value

