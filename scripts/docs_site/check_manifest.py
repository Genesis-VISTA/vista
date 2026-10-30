#!/usr/bin/env python3
"""
Check that docs/site.toml accounts for every page in docs/.

Every `docs/*.md` must appear in the manifest exactly once, either as a
published page (under a `[[section]]`) or in `exclude`, and every listed page
must exist. Offline and stdlib only, so it runs in the bare lint lane.

Usage: scripts/docs_site/check_manifest.py [--docs DIR] [--manifest FILE]
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _bare_md(name: object) -> bool:
    return isinstance(name, str) and name.endswith(".md") and "/" not in name and "\\" not in name


def check(docs_dir: Path, manifest: dict) -> list[str]:
    """Return one message per problem, each naming the file; empty when all is well."""
    problems: list[str] = []
    listed: list[str] = []

    sections = manifest.get("section", [])
    if not isinstance(sections, list):
        sections = []
        problems.append("docs/site.toml: `section` must be an array of [[section]] tables")
    for i, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            section = {}
        if not section.get("title"):
            problems.append(f"docs/site.toml: [[section]] #{i} has no title")
        for page in section.get("pages", []):
            file = page.get("file") if isinstance(page, dict) else None
            if not file:
                problems.append(f"docs/site.toml: a page in [[section]] #{i} has no file")
                continue
            if not _bare_md(file):
                problems.append(f"docs/site.toml: page file {file!r} must be a bare name.md in docs/")
                continue
            if not page.get("title"):
                problems.append(f"docs/site.toml: page {file} has no title")
            listed.append(file)

    exclude = manifest.get("exclude", [])
    if not isinstance(exclude, list):
        exclude = []
        problems.append("docs/site.toml: `exclude` must be an array of file names")
    excluded = [e for e in exclude if isinstance(e, str)]

    for file, n in sorted(Counter(listed).items()):
        if n > 1:
            problems.append(f"{file} is listed {n} times in docs/site.toml")
    for file, n in sorted(Counter(excluded).items()):
        if n > 1:
            problems.append(f"{file} is excluded {n} times in docs/site.toml")
    for file in sorted(set(listed) & set(excluded)):
        problems.append(f"{file} is both listed and excluded in docs/site.toml")

    present = {p.name for p in docs_dir.glob("*.md")}
    for file in sorted(set(listed) - present):
        problems.append(f"docs/site.toml lists {file}, which is not in docs/")
    for file in sorted(set(excluded) - present):
        problems.append(f"docs/site.toml excludes {file}, which is not in docs/")
    for file in sorted(present - set(listed) - set(excluded)):
        problems.append(f"docs/{file} is neither listed nor excluded in docs/site.toml")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--docs", type=Path, default=REPO_ROOT / "docs")
    ap.add_argument("--manifest", type=Path, default=REPO_ROOT / "docs" / "site.toml")
    args = ap.parse_args(argv)

    try:
        with args.manifest.open("rb") as f:
            manifest = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"cannot read {args.manifest}: {e}", file=sys.stderr)
        return 1

    problems = check(args.docs, manifest)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"docs manifest: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"Docs manifest OK ({len(list(args.docs.glob('*.md')))} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
