"""The retrieval gate for the `olcf-docs` corpus (design.md, Decision 8).

    uv run --project mcp_servers/vista_mcp_server \\
        scripts/olcf_docs/check_retrieval.py <rag_db> [--questions questions.toml]

Asks every question in questions.toml of the prebuilt store the way a researcher's
agent does, through the MCP server's own `rag_mcp._search` (same encoder, same
query instruction, same default of 5 vector hits), and checks that the file named
by `expected` is among the hits: a hit's file name must start with it. Exits 0 when
every question passes, 1 when any fails, 2 on bad input. A maintainer runs it after
each corpus refresh; it is not part of PR CI, because the store is not in the repo.

The store is copied to a scratch folder first, since opening a Chroma store writes
its sqlite file and a refresh's store should come out of this byte for byte.
Needs the MCP server's environment (`--project` above) and its embedding model.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path

SLUG = "olcf-docs"
TOP_K = 5
SEPARATOR = "—" * 60  # rag_mcp._format_results joins hits with this line
_HIT = re.compile(r"^\[\d+\] ([^,\s]+),")

Search = Callable[[str], str]


def load_questions(path: Path) -> list[tuple[str, str]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for i, q in enumerate(data.get("question", []), 1):
        if not (isinstance(q.get("q"), str) and isinstance(q.get("expected"), str) and q["expected"]):
            raise ValueError(f"{path}: question {i} needs string `q` and a non-empty `expected`")
        out.append((q["q"], q["expected"]))
    if not out:
        raise ValueError(f"{path}: no [[question]] entries")
    return out


def hit_files(result: str) -> list[str]:
    """File names of the hits in a `rag_search` result, best first, without repeats."""
    files: list[str] = []
    for part in result.split("\n\n" + SEPARATOR + "\n\n"):
        m = _HIT.match(part)
        if m and m.group(1) not in files:
            files.append(m.group(1))
    return files


def check(questions: list[tuple[str, str]], search: Search) -> list[tuple[str, str, list[str], bool]]:
    results = []
    for q, expected in questions:
        files = hit_files(search(q))
        results.append((q, expected, files, any(f.startswith(expected) for f in files)))
    return results


def open_search(rag_db: Path, scratch: Path) -> Search:
    """A `search(query) -> text` over a copy of `rag_db`, through rag_mcp's own code."""
    store = rag_db / "rag_db" if (rag_db / "rag_db" / "chroma.sqlite3").is_file() else rag_db
    if not (store / "chroma.sqlite3").is_file():
        raise ValueError(f"{rag_db} is not a ChromaDB store (no chroma.sqlite3)")
    shutil.copytree(store, scratch / "knowledge-bases" / SLUG)
    os.environ["VISTA_DATA_DIR"] = str(scratch)  # read when the MCP server's settings load

    from sentence_transformers import SentenceTransformer
    from vista_mcp_server import rag_mcp
    from vista_mcp_server.config import settings

    rag_mcp._encoder = SentenceTransformer(
        settings.rag_model, revision=settings.rag_model_revision, device=settings.embed_device
    )
    return lambda query: rag_mcp._search(query, SLUG, TOP_K, False, 0.5)


def main(argv: list[str] | None = None, search: Search | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rag_db", type=Path, help="the prebuilt store (the folder holding chroma.sqlite3)")
    ap.add_argument("--questions", type=Path, default=Path(__file__).with_name("questions.toml"))
    args = ap.parse_args(argv)
    try:
        questions = load_questions(args.questions)
        if search is not None:
            results = check(questions, search)
        else:
            with tempfile.TemporaryDirectory(prefix="olcf-retrieval-") as tmp:
                results = check(questions, open_search(args.rag_db, Path(tmp)))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = 0
    for q, expected, files, ok in results:
        print(f"{'ok  ' if ok else 'FAIL'} {q}")
        if not ok:
            failed += 1
            print(f"       expected a file starting with {expected!r}; top {TOP_K} were:")
            for f in files or ["(no hits)"]:
                print(f"         {f}")
    print(f"{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
