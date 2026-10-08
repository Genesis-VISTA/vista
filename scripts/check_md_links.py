#!/usr/bin/env python3
"""
Check the relative links in the repository's Markdown files.

Every `[text](target)` whose target is not a URL must name a file or directory
that exists, and a `#fragment` on a Markdown target must match one of its
headings (GitHub's slug rules). External URLs are not fetched: the check is
offline so it can run in hermetic CI, and what drifts is our own files.

Usage: scripts/check_md_links.py [FILE.md ...]   (default: every tracked .md)
"""

from __future__ import annotations

import re
import subprocess
import sys
from functools import cache
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent

# Point-in-time records: archived OpenSpec changes are kept as they were
# written, links to since-moved files included.
EXCLUDE_PREFIXES = ("openspec/changes/archive/",)

LINK_RE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return [
        REPO_ROOT / p for p in out.split("\0")
        if p and not p.startswith(EXCLUDE_PREFIXES)
    ]


def prose_lines(path: Path):
    """Yield (line number, text) outside fenced code, with inline code removed."""
    fence = None
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence is None:
            yield n, INLINE_CODE_RE.sub("", line)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # links keep their text
    text = re.sub(r"[`*_~]|<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


@cache
def anchors(path: Path) -> frozenset[str]:
    seen: dict[str, int] = {}
    out = set()
    for _, line in prose_lines(path):
        m = HEADING_RE.match(line)
        if not m:
            continue
        s = slug(m.group(1))
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else f"{s}-{n}")
    return frozenset(out)


def check(path: Path) -> list[str]:
    problems = []
    rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    for n, line in prose_lines(path):
        for m in LINK_RE.finditer(line):
            target = m.group(1)
            if EXTERNAL_RE.match(target) or target.startswith("${"):
                continue  # a URL, or a template's placeholder (.github/release-notes.md)
            file_part, _, fragment = target.partition("#")
            dest = (path.parent / unquote(file_part)).resolve() if file_part else path
            if not dest.exists():
                problems.append(f"{rel}:{n}: {target}: no such file")
            elif fragment and dest.suffix == ".md" and dest.is_file():
                if unquote(fragment).lower() not in anchors(dest):
                    problems.append(f"{rel}:{n}: {target}: no heading #{fragment}")
    return problems


def main(argv: list[str]) -> int:
    files = [Path(a).resolve() for a in argv] or tracked_markdown()
    problems = [p for f in files for p in check(f)]
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} broken link(s) in Markdown files", file=sys.stderr)
        return 1
    print(f"Markdown links OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
