#!/usr/bin/env python3
"""
Index one or more specific PDFs into a ChromaDB.

This is the server-side incremental indexer for the Knowledge Bases UI.
When a user uploads a PDF, the Next.js side spawns this script with the
target DB path, the PDF directory, and the list of filenames to index.
Each filename is chunked + embedded into the `text_chunks` collection
and (if Azure OpenAI credentials are available) has its citation
metadata extracted into the `citations` collection — exactly the same
data the MCP server's rag_search tool reads from.

Concurrency: a fcntl lock on `<db-path>/.indexing.lock` ensures only one
indexer process writes to a given DB at a time. ChromaDB's persistent
client uses SQLite, which is single-writer; concurrent writes can fail
or block. Other invocations queue up behind the lock.

Output: a single JSON document on stdout summarizing the run, of shape:

    {
      "ok": true,
      "results": [
        {
          "filename": "...",
          "status": "indexed" | "skipped" | "failed",
          "error": null | "...",
          "chunk_count": <int>,
          "citation": null | { ...citation fields... }
        }, ...
      ]
    }

Status meanings:
    indexed  - PDF was chunked, embedded, and (if extraction was on) had
               citation metadata extracted and written to the DB.
    skipped  - PDF was already indexed (its citation row was present).
               Re-running with --force-reindex would overwrite it.
    failed   - An exception was raised during indexing. `error` carries
               the message; the DB may have partial state for this PDF.

Exit codes:
    0 - run completed (look at per-result status to know what happened)
    1 - fatal error before per-PDF processing began (bad arguments,
        couldn't import dependencies, couldn't open the DB, etc.).
        stderr carries the message; stdout will not have valid JSON.
    2 - couldn't acquire the indexer lock within the timeout.

Usage:
    python index_publications.py \
        --db-path /path/to/molten_salts_db \
        --pdfs-dir /path/to/pdfs \
        --filename paper1.pdf --filename paper2.pdf

    # or read filenames from stdin (one per line):
    echo paper1.pdf | python index_publications.py \
        --db-path /path/to/molten_salts_db \
        --pdfs-dir /path/to/pdfs \
        --filenames-stdin
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Lock acquisition timeout for the indexer lock file. If another indexer
# is already running on the same DB, we wait up to this long before
# giving up and exiting with code 2.
LOCK_TIMEOUT_SECONDS = 600  # 10 minutes


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _add_repo_root_to_path() -> Path:
    """
    The TextRAG class lives at <repo>/build_rag.py. This script lives at
    <repo>/mcp-server/scripts/. Walk up to find the repo root and put it
    on sys.path so the import works regardless of cwd.
    """
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent  # scripts -> mcp-server -> repo
    if not (repo_root / "build_rag.py").exists():
        # Fallback: try one more level up in case the layout changes.
        candidate = repo_root.parent
        if (candidate / "build_rag.py").exists():
            repo_root = candidate
    sys.path.insert(0, str(repo_root))
    return repo_root


def _acquire_lock(lock_path: Path, timeout_seconds: int) -> int | None:
    """
    Acquire an exclusive flock on `lock_path`, polling every 200ms until
    it succeeds or the timeout elapses. Returns the open file descriptor
    on success (which keeps the lock held for the lifetime of the
    process) or None on timeout.

    On Windows, fcntl is unavailable; we fall back to a simple
    file-existence check, which is racy but adequate for the
    single-developer-laptop case Windows users would have.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # noqa: WPS433 — POSIX-only
    except ImportError:
        # Windows fallback: best-effort.
        if lock_path.exists():
            return None
        lock_path.touch()
        # Open and return a fd so caller has the same lifecycle.
        return os.open(str(lock_path), os.O_RDWR)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(fd)
                raise
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        required=True,
        help="ChromaDB directory. Created if it doesn't exist.",
    )
    parser.add_argument(
        "--pdfs-dir",
        required=True,
        help="Directory the PDF filenames are resolved against.",
    )
    parser.add_argument(
        "--filename",
        action="append",
        default=[],
        help="A PDF filename (basename, not path) to index. May be given "
             "multiple times. Files are looked up under --pdfs-dir.",
    )
    parser.add_argument(
        "--filenames-stdin",
        action="store_true",
        help="Read filenames from stdin (one per line) instead of (or in "
             "addition to) --filename arguments.",
    )
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Re-process even files whose citation row is already in "
             "the DB.",
    )
    parser.add_argument(
        "--no-citations",
        action="store_true",
        help="Skip Azure OpenAI citation extraction. Text chunks are "
             "still indexed. Useful when no API key is configured.",
    )
    parser.add_argument(
        "--lock-timeout",
        type=int,
        default=LOCK_TIMEOUT_SECONDS,
        help="Seconds to wait for the per-DB indexer lock before giving up.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    pdfs_dir = Path(args.pdfs_dir).resolve()
    if not pdfs_dir.exists():
        _err(f"PDFs dir does not exist: {pdfs_dir}")
        return 1

    filenames: list[str] = list(args.filename)
    if args.filenames_stdin:
        for line in sys.stdin:
            stripped = line.strip()
            if stripped:
                filenames.append(stripped)
    if not filenames:
        _err("No filenames provided. Pass --filename or --filenames-stdin.")
        return 1

    # Defense in depth: don't let a caller sneak path separators in. The
    # Node side already sanitizes, but treating these as basenames means
    # we never accidentally read outside --pdfs-dir.
    cleaned: list[str] = []
    for name in filenames:
        base = Path(name).name
        if not base:
            continue
        cleaned.append(base)
    filenames = cleaned

    repo_root = _add_repo_root_to_path()

    try:
        from build_rag import TextRAG  # type: ignore[import-not-found]
    except ModuleNotFoundError as e:
        # Most common cause: this script is being invoked via
        # `uv run --directory mcp-server` but mcp-server's pyproject
        # hasn't been synced since the indexer's deps (pymupdf, openai)
        # were added. Give the user something concrete to do.
        _err(
            f"Missing Python dependency for indexing: {e.name}.\n"
            f"  Try: cd {repo_root}/mcp-server && uv sync\n"
            f"  (build_rag.py imports `fitz` (PyMuPDF) and `openai`; both "
            f"are declared in mcp-server/pyproject.toml.)"
        )
        return 1
    except Exception as e:  # noqa: BLE001
        _err(f"Failed to import TextRAG from {repo_root}/build_rag.py: {e}")
        return 1

    # Acquire the indexer lock before doing anything heavy. We hold it
    # for the lifetime of the process via the open fd.
    db_path.mkdir(parents=True, exist_ok=True)
    lock_path = db_path / ".indexing.lock"
    lock_fd = _acquire_lock(lock_path, timeout_seconds=args.lock_timeout)
    if lock_fd is None:
        _err(f"Could not acquire indexer lock on {lock_path} within "
             f"{args.lock_timeout}s. Another indexer is likely running.")
        return 2

    try:
        # Initialize TextRAG. This loads the sentence-transformer model
        # (~2-5s cold) and opens the Chroma collections.
        try:
            rag = TextRAG(
                pdf_folder=str(pdfs_dir),
                db_path=str(db_path),
                force_reindex=False,  # we handle dedup at the per-PDF level
                extract_citations=not args.no_citations,
            )
        except Exception as e:  # noqa: BLE001
            _err(f"Failed to initialize TextRAG: {e}")
            return 1

        # Override force_reindex flag for the per-PDF call.
        rag.force_reindex = args.force_reindex

        results: list[dict[str, Any]] = []
        for filename in filenames:
            pdf_path = pdfs_dir / filename
            if not pdf_path.is_file():
                results.append({
                    "filename": filename,
                    "status": "failed",
                    "error": f"File not found: {pdf_path}",
                    "chunk_count": 0,
                    "citation": None,
                })
                continue
            res = rag.index_single_pdf(pdf_path)
            results.append(res)

        _emit_json({"ok": True, "results": results})
        return 0
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
