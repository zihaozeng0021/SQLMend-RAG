from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .chunking import chunk_all
from .clean import clean_all
from .collect import collect_all
from .dedup import deduplicate_documents
from .metadata import enrich_all
from .parsers import parse_all
from .statistics import generate_statistics
from .validation import validate_corpus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLMendRAG knowledge-base construction pipeline")
    parser.add_argument("--root", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect", help="Collect raw source documents")
    collect_parser.add_argument("--source", action="append", dest="sources", help="Collect only a source ID (repeatable)")
    subparsers.add_parser("parse", help="Parse raw HTML/Markdown/XML/text")
    subparsers.add_parser("clean", help="Clean parsed documents")
    subparsers.add_parser("enrich", help="Normalize and enrich metadata")
    subparsers.add_parser("deduplicate", help="Deduplicate cleaned documents")
    subparsers.add_parser("chunk", help="Create structure-aware and fixed baseline chunks")
    subparsers.add_parser("statistics", help="Generate corpus and coverage statistics")
    subparsers.add_parser("validate", help="Run hard corpus validation")
    build = subparsers.add_parser("build", help="Run the complete pipeline")
    build.add_argument("--skip-collect", action="store_true", help="Reuse existing raw documents")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = Path(args.root).resolve()
    command = args.command
    if command == "collect":
        collect_all(root, source_ids=set(args.sources) if args.sources else None)
    elif command == "parse":
        parse_all(root)
    elif command == "clean":
        clean_all(root)
    elif command == "enrich":
        enrich_all(root)
    elif command == "deduplicate":
        deduplicate_documents(root)
    elif command == "chunk":
        chunk_all(root)
    elif command == "statistics":
        generate_statistics(root)
    elif command == "validate":
        return 0 if validate_corpus(root)["status"] == "PASS" else 1
    elif command == "build":
        if not args.skip_collect:
            collect_all(root)
        parse_all(root)
        clean_all(root)
        enrich_all(root)
        deduplicate_documents(root)
        chunk_all(root)
        generate_statistics(root)
        return 0 if validate_corpus(root)["status"] == "PASS" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

