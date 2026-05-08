#!/usr/bin/env python3
"""
Dump citation metadata from a ChromaDB built by build_rag.py.

Reads the `citations` collection (one entry per source PDF, populated at
index time) and writes a single JSON object to stdout mapping each PDF
filename to its citation fields. Empty strings — used by build_rag.py
to encode "no value" — are converted back to nulls. List-valued fields
(authors, keywords) are JSON-decoded back into arrays.

Designed to be invoked by the Knowledge Base Explorer (in the Next.js
UI) to enrich publication rows with extracted titles, authors, DOIs,
etc. The UI calls this script once per index build and caches the
result in the KB's kb.json — so the cost is paid at index time, not on
every page-load.

Usage:
    python dump_citations.py --db-path /path/to/molten_salts_db
    uv run --directory <repo>/mcp-server python scripts/dump_citations.py \
        --db-path <repo>/knowledge_bases/molten_salts_db

Exit codes:
    0 — success (JSON written to stdout)
    1 — fatal error (message on stderr, no output on stdout)
    2 — DB opened but no `citations` collection (empty {} on stdout)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Fields that build_rag.py writes into the citation collection metadata.
# Kept in sync with build_rag.py:CITATION_FIELDS.
CITATION_FIELDS = [
    "title", "authors", "abstract", "journal", "volume",
    "issue", "pages", "year", "doi", "keywords", "publisher",
]
LIST_FIELDS = {"authors", "keywords"}


def _decode_field(name: str, value: Any) -> Any:
    """
    Reverse the encoding done by build_rag.py._citation_to_metadata.

    - Empty strings become null (build_rag uses "" for missing values).
    - List-valued fields are JSON-decoded.
    - Everything else is passed through as-is.
    """
    if value is None or value == "":
        return None
    if name in LIST_FIELDS:
        if isinstance(value, list):
            return value
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else None
        except (TypeError, ValueError):
            return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        required=True,
        help="Filesystem path to the ChromaDB directory (the same path "
             "passed to chromadb.PersistentClient).",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"DB path does not exist: {db_path}", file=sys.stderr)
        return 1

    try:
        # Imported lazily so a missing chromadb install yields a clean
        # error rather than an import-time crash for callers that just
        # wanted to check exit codes.
        import chromadb  # noqa: WPS433
    except ImportError as exc:
        print(f"chromadb is not installed: {exc}", file=sys.stderr)
        return 1

    try:
        client = chromadb.PersistentClient(path=str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to open ChromaDB at {db_path}: {exc}", file=sys.stderr)
        return 1

    try:
        collection = client.get_collection("citations")
    except Exception:  # noqa: BLE001 — Chroma raises a non-stdlib exception
        # No citations collection yet (DB might only have text_chunks,
        # or build_rag.py was run with extract_citations=False). That's
        # not an error — just no enrichment available.
        sys.stdout.write("{}\n")
        return 2

    try:
        data = collection.get(include=["metadatas"])
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to read citations collection: {exc}", file=sys.stderr)
        return 1

    metadatas = data.get("metadatas") or []
    out: dict[str, dict[str, Any]] = {}
    for meta in metadatas:
        if not isinstance(meta, dict):
            continue
        source = meta.get("source")
        if not source or not isinstance(source, str):
            continue
        # Multiple chunks may share a source; keep the first one we see
        # (citations collection should have one entry per source anyway,
        # but we don't assume).
        if source in out:
            continue
        out[source] = {
            field: _decode_field(field, meta.get(field))
            for field in CITATION_FIELDS
        }

    json.dump(out, sys.stdout, ensure_ascii=False, indent=None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
