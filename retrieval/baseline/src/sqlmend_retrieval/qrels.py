"""Machine-proposed JSONL qrel conversion and standard TREC qrel I/O."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .hashing import sha256_file


ALLOWED_RELEVANCE_LABELS = frozenset({0, 1, 2})


class QrelsError(ValueError):
    """Raised when qrels are malformed or violate snapshot invariants."""


@dataclass(frozen=True, slots=True)
class QrelEntry:
    """One explicit relevance judgment, including relevance-zero judgments."""

    query_id: str
    chunk_id: str
    relevance: int


Qrel = QrelEntry


def _token(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise QrelsError(f"{field} must be a non-empty whitespace-free token")
    if any(character.isspace() for character in value):
        raise QrelsError(f"{field} must not contain whitespace: {value!r}")
    return value


def _coerce_qrel(value: QrelEntry | Mapping[str, Any] | object) -> QrelEntry:
    if isinstance(value, QrelEntry):
        qrel = value
    elif isinstance(value, Mapping):
        try:
            qrel = QrelEntry(value["query_id"], value["chunk_id"], value["relevance"])
        except KeyError as exc:
            raise QrelsError(f"qrel is missing field {exc.args[0]!r}") from exc
    else:
        try:
            qrel = QrelEntry(
                getattr(value, "query_id"),
                getattr(value, "chunk_id"),
                getattr(value, "relevance"),
            )
        except AttributeError as exc:
            raise QrelsError(f"unsupported qrel entry: {value!r}") from exc

    query_id = _token(qrel.query_id, "query_id")
    chunk_id = _token(qrel.chunk_id, "chunk_id")
    if isinstance(qrel.relevance, bool) or not isinstance(qrel.relevance, int):
        raise QrelsError(f"relevance must be an integer label: {qrel.relevance!r}")
    if qrel.relevance not in ALLOWED_RELEVANCE_LABELS:
        raise QrelsError(
            f"relevance must be one of {sorted(ALLOWED_RELEVANCE_LABELS)!r}: "
            f"{qrel.relevance!r}"
        )
    return QrelEntry(query_id, chunk_id, qrel.relevance)


def validate_qrels(
    qrels: Iterable[QrelEntry | Mapping[str, Any] | object],
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
    require_nonempty: bool = True,
) -> list[QrelEntry]:
    """Validate explicit judgments and return normalized qrels."""

    normalized = [_coerce_qrel(qrel) for qrel in qrels]
    if require_nonempty and not normalized:
        raise QrelsError("qrels are empty")

    known = None if known_chunk_ids is None else frozenset(known_chunk_ids)
    seen: set[tuple[str, str]] = set()
    labels: set[int] = set()
    for qrel in normalized:
        pair = (qrel.query_id, qrel.chunk_id)
        if pair in seen:
            raise QrelsError(
                f"duplicate qrel for query/chunk: {qrel.query_id!r}, {qrel.chunk_id!r}"
            )
        seen.add(pair)
        labels.add(qrel.relevance)
        if known is not None and qrel.chunk_id not in known:
            raise QrelsError(f"unknown chunk_id in qrels: {qrel.chunk_id!r}")

    if require_all_labels and labels != ALLOWED_RELEVANCE_LABELS:
        missing = sorted(ALLOWED_RELEVANCE_LABELS.difference(labels))
        raise QrelsError(f"qrels must contain labels 0, 1, and 2; missing {missing!r}")
    return normalized


def load_qrels_jsonl(
    path: str | Path,
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
) -> list[QrelEntry]:
    """Load the frozen JSONL snapshot without altering or dropping judgments."""

    source_path = Path(path)
    qrels: list[QrelEntry] = []
    try:
        with source_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise QrelsError(f"blank line at JSONL line {line_number}")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise QrelsError(
                        f"invalid JSON at qrels line {line_number}: {exc.msg}"
                    ) from exc
                if not isinstance(value, Mapping):
                    raise QrelsError(f"qrels line {line_number} must be a JSON object")
                try:
                    qrels.append(_coerce_qrel(value))
                except QrelsError as exc:
                    raise QrelsError(f"invalid qrels line {line_number}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise QrelsError(f"qrels JSONL is not valid UTF-8: {source_path}") from exc

    return validate_qrels(
        qrels,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )


def parse_trec_qrels(
    text: str,
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
) -> list[QrelEntry]:
    """Parse standard four-column TREC qrels text."""

    if not isinstance(text, str):
        raise TypeError("text must be str")
    qrels: list[QrelEntry] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise QrelsError(f"blank line at qrels line {line_number}")
        fields = line.split()
        if len(fields) != 4:
            raise QrelsError(
                f"qrels line {line_number} must have 4 columns; observed {len(fields)}"
            )
        query_id, iteration, chunk_id, relevance_text = fields
        if iteration != "0":
            raise QrelsError(
                f"qrels line {line_number} column 2 must be '0'; observed {iteration!r}"
            )
        try:
            relevance = int(relevance_text, 10)
        except ValueError as exc:
            raise QrelsError(
                f"invalid relevance at qrels line {line_number}: {relevance_text!r}"
            ) from exc
        try:
            qrels.append(_coerce_qrel(QrelEntry(query_id, chunk_id, relevance)))
        except QrelsError as exc:
            raise QrelsError(f"invalid qrels line {line_number}: {exc}") from exc

    return validate_qrels(
        qrels,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )


def read_trec_qrels(
    path: str | Path,
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
) -> list[QrelEntry]:
    """Read and validate a UTF-8 TREC qrels file."""

    qrels_path = Path(path)
    try:
        text = qrels_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise QrelsError(f"TREC qrels are not valid UTF-8: {qrels_path}") from exc
    return parse_trec_qrels(
        text,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )


def format_trec_qrels(
    qrels: Iterable[QrelEntry | Mapping[str, Any] | object],
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
) -> str:
    """Serialize every qrel, including label-zero qrels, deterministically."""

    normalized = validate_qrels(
        qrels,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )
    ordered = sorted(normalized, key=lambda qrel: (qrel.query_id, qrel.chunk_id))
    return "".join(
        f"{qrel.query_id} 0 {qrel.chunk_id} {qrel.relevance}\n" for qrel in ordered
    )


def write_trec_qrels(
    path: str | Path,
    qrels: Iterable[QrelEntry | Mapping[str, Any] | object],
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
) -> Path:
    """Write canonical UTF-8/LF TREC qrels and return the output path."""

    output = format_trec_qrels(
        qrels,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(output)
    return output_path


def convert_qrels_jsonl_to_trec(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    known_chunk_ids: Iterable[str] | None = None,
    require_all_labels: bool = False,
) -> list[QrelEntry]:
    """Convert the source snapshot to TREC qrels without modifying the source."""

    qrels = load_qrels_jsonl(
        source_path,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )
    write_trec_qrels(
        destination_path,
        qrels,
        known_chunk_ids=known_chunk_ids,
        require_all_labels=require_all_labels,
    )
    return qrels


def merge_supplemental_qrels(
    base_qrels: Iterable[QrelEntry | Mapping[str, Any] | object],
    supplemental_path: str | Path,
    formal_runs: Mapping[str, Iterable[object]],
    *,
    known_chunk_ids: Iterable[str],
    top_k: int = 30,
) -> tuple[list[QrelEntry], dict[str, Any]]:
    """Merge a distinct, externally supplied judgment file without conflicts.

    Supplemental judgments are accepted only for currently unjudged pairs in
    the union of the formal top-k runs.  The request artifact is never used as
    an input and can therefore be regenerated without destroying judgments.
    """

    known = frozenset(known_chunk_ids)
    base = validate_qrels(
        base_qrels,
        known_chunk_ids=known,
        require_all_labels=True,
    )
    path = Path(supplemental_path)
    if not path.is_file():
        return base, {
            "supplemental_qrels_present": False,
            "supplemental_qrel_count": 0,
            "effective_qrel_count": len(base),
        }

    supplemental = load_qrels_jsonl(
        path,
        known_chunk_ids=known,
        require_all_labels=False,
    )
    base_pairs = {(item.query_id, item.chunk_id) for item in base}
    allowed_pairs: set[tuple[str, str]] = set()
    for entries in formal_runs.values():
        for entry in entries:
            rank = getattr(entry, "rank", None)
            query_id = getattr(entry, "query_id", None)
            chunk_id = getattr(entry, "chunk_id", None)
            if isinstance(rank, int) and rank <= top_k:
                allowed_pairs.add((query_id, chunk_id))
    conflicts = sorted(
        (item.query_id, item.chunk_id)
        for item in supplemental
        if (item.query_id, item.chunk_id) in base_pairs
    )
    outside_pool = sorted(
        (item.query_id, item.chunk_id)
        for item in supplemental
        if (item.query_id, item.chunk_id) not in allowed_pairs
    )
    if conflicts:
        raise QrelsError(f"supplemental qrels conflict with frozen base qrels: {conflicts[:10]!r}")
    if outside_pool:
        raise QrelsError(
            f"supplemental qrels are outside the current formal top-{top_k}: {outside_pool[:10]!r}"
        )
    combined = validate_qrels(
        [*base, *supplemental],
        known_chunk_ids=known,
        require_all_labels=True,
    )
    return combined, {
        "supplemental_qrels_present": True,
        "supplemental_qrels_sha256": sha256_file(path),
        "supplemental_qrel_count": len(supplemental),
        "effective_qrel_count": len(combined),
    }


load_trec_qrels = read_trec_qrels
dump_trec_qrels = write_trec_qrels
jsonl_to_trec_qrels = convert_qrels_jsonl_to_trec
