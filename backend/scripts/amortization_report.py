#!/usr/bin/env python3
"""Amortization analyzer (evaluation plan M9, experiments E10/E12a).

Pure repository analysis — no runtime hooks. For each application defined in
amortization.yaml, discovers the commits that introduced it (git log over the
app's path set), then attributes added/changed LOC per subsystem so the
paper's amortization claim can be stated mechanically:

  per app: {platform LOC co-changed, skill LOC, new MCP tools,
            new job templates, files touched, subsystems touched}

Usage (from backend/):
  uv run python scripts/amortization_report.py            # E10 per-app report
  uv run python scripts/amortization_report.py --detail   # + per-commit rows (E12a)
  uv run python scripts/amortization_report.py --snapshot # platform LOC at HEAD
  uv run python scripts/amortization_report.py --latex    # emit LaTeX table rows
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

OTHER = "other"
PLATFORM_BUCKETS = ("runtime", "security", "scheduler", "mcp_servers", "ui")
RENAME_BRACES = re.compile(r"\{([^{}]*) => ([^{}]*)\}")
MCP_TOOL_DECORATOR = re.compile(r"^\+\s*@\w+\.tool\b")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def matches(path: str, globs: list[str]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(g) for g in globs)


def classify(path: str, subsystems: dict[str, list[str]]) -> str:
    for bucket, globs in subsystems.items():
        if matches(path, globs):
            return bucket
    return OTHER


def to_pathspec(glob: str) -> str:
    """Convert a config glob to a git pathspec for commit discovery."""
    if glob.endswith("/**"):
        return glob[:-3]  # directory prefix pathspec
    if any(c in glob for c in "*?["):
        return f":(glob){glob}"
    return glob


def resolve_rename(path: str) -> str:
    """Return the post-rename path from a numstat rename entry."""
    if "{" in path:
        return RENAME_BRACES.sub(lambda m: m.group(2), path).replace("//", "/")
    if " => " in path:
        return path.split(" => ")[1]
    return path


@dataclass
class Commit:
    sha: str
    date: str
    subject: str
    # path -> (added, deleted); '-' (binary) recorded as (0, 0)
    files: dict[str, tuple[int, int]] = field(default_factory=dict)


def discover_commits(repo: Path, branch: str, paths: list[str]) -> list[str]:
    pathspecs = [to_pathspec(p) for p in paths]
    out = git(repo, "log", "--no-merges", "--reverse", "--format=%H", branch, "--", *pathspecs)
    return out.split()


def commit_stats(repo: Path, sha: str) -> Commit:
    out = git(repo, "show", "-M", "--numstat", "--format=%H%x09%ad%x09%s", "--date=short", sha)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    full_sha, date, subject = lines[0].split("\t", 2)
    commit = Commit(sha=full_sha[:7], date=date, subject=subject)
    for ln in lines[1:]:
        added, deleted, path = ln.split("\t", 2)
        path = resolve_rename(path)
        a = int(added) if added != "-" else 0
        d = int(deleted) if deleted != "-" else 0
        commit.files[path] = (a, d)
    return commit


def added_artifacts(repo: Path, sha: str) -> tuple[list[str], list[str]]:
    """(new SKILL.md files, new hpc_jobs/<template> dirs) introduced by sha."""
    out = git(repo, "show", "-M", "--diff-filter=A", "--name-only", "--format=", sha)
    added = [ln for ln in out.splitlines() if ln.strip()]
    skills = [p for p in added if p.endswith("/SKILL.md")]
    templates = sorted({p.split("/")[1] for p in added if p.startswith("hpc_jobs/") and p.count("/") >= 2})
    return skills, templates


def new_mcp_tools(repo: Path, sha: str) -> int:
    patch = git(repo, "show", "-M", "--unified=0", "--format=", sha)
    added = sum(1 for ln in patch.splitlines() if MCP_TOOL_DECORATOR.match(ln))
    removed = sum(
        1 for ln in patch.splitlines() if MCP_TOOL_DECORATOR.match("+" + ln[1:]) and ln.startswith("-")
    )
    return added - removed


def head_loc(repo: Path, paths: list[str], exclude: list[str]) -> tuple[int, int]:
    """(files, LOC) currently in HEAD under the given globs."""
    files = [
        f for f in git(repo, "ls-files", "--", *[to_pathspec(p) for p in paths]).splitlines()
        if f and not matches(f, exclude)
    ]
    loc = 0
    for f in files:
        try:
            loc += len((repo / f).read_text(errors="strict").splitlines())
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary or sparse checkout
    return len(files), loc


@dataclass
class AppReport:
    name: str
    commits: list[Commit]
    incidental: list[tuple[Commit, float]] = field(default_factory=list)
    app_loc_added: int = 0
    co_change: dict[str, tuple[int, int]] = field(default_factory=dict)  # bucket -> (LOC added, files)
    files_touched: int = 0
    new_skills: list[str] = field(default_factory=list)
    new_templates: list[str] = field(default_factory=list)
    new_tools: int = 0
    head_skill_files: int = 0
    head_skill_loc: int = 0


def split_incidental(
    commits: list[Commit], spec: dict, cfg: dict
) -> tuple[list[Commit], list[tuple[Commit, float]]]:
    """Separate app-introducing commits from platform work that touched app
    files in passing, by the share of added LOC inside the app's paths."""
    exclude = cfg.get("exclude", [])
    min_share = spec.get("min_app_share", cfg.get("min_app_share", 0.25))
    include = {s[:7] for s in spec.get("include_commits", [])}
    drop = {s[:7] for s in spec.get("exclude_commits", [])}
    kept: list[Commit] = []
    incidental: list[tuple[Commit, float]] = []
    for c in commits:
        counted = {p: a for p, (a, _) in c.files.items() if not matches(p, exclude)}
        app_added = sum(a for p, a in counted.items() if matches(p, spec["paths"]))
        total_added = sum(counted.values())
        share = app_added / total_added if total_added else 1.0
        if c.sha in drop or (share < min_share and c.sha not in include):
            incidental.append((c, share))
        else:
            kept.append(c)
    return kept, incidental


def analyze_app(repo: Path, name: str, spec: dict, cfg: dict) -> AppReport:
    subsystems = cfg["subsystems"]
    exclude = cfg.get("exclude", [])
    app_paths = spec["paths"]
    shas = discover_commits(repo, cfg.get("branch", "main"), app_paths)
    commits = [commit_stats(repo, s) for s in shas]
    commits, incidental = split_incidental(commits, spec, cfg)

    rep = AppReport(name=name, commits=commits, incidental=incidental)
    co_loc: dict[str, int] = defaultdict(int)
    co_files: dict[str, set[str]] = defaultdict(set)
    touched: set[str] = set()
    for c in commits:
        for path, (added, _deleted) in c.files.items():
            if matches(path, exclude):
                continue
            touched.add(path)
            if matches(path, app_paths):
                rep.app_loc_added += added
            else:
                bucket = classify(path, subsystems)
                co_loc[bucket] += added
                co_files[bucket].add(path)
        skills, templates = added_artifacts(repo, c.sha)
        rep.new_skills += [s for s in skills if matches(s, app_paths) and s not in rep.new_skills]
        rep.new_templates += [
            t for t in templates
            if matches(f"hpc_jobs/{t}/x", app_paths) and t not in rep.new_templates
        ]
        rep.new_tools += new_mcp_tools(repo, c.sha)
    rep.files_touched = len(touched)
    rep.co_change = {b: (co_loc[b], len(co_files[b])) for b in sorted(co_loc)}
    rep.head_skill_files, rep.head_skill_loc = head_loc(repo, app_paths, exclude)
    return rep


def snapshot(repo: Path, cfg: dict) -> dict[str, tuple[int, int]]:
    """Current LOC per subsystem at HEAD — fills the platform-size paragraph."""
    exclude = cfg.get("exclude", [])
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in git(repo, "ls-files").splitlines():
        if not f or matches(f, exclude):
            continue
        bucket = classify(f, cfg["subsystems"])
        try:
            loc = len((repo / f).read_text(errors="strict").splitlines())
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        totals[bucket][0] += 1
        totals[bucket][1] += loc
    return {b: tuple(v) for b, v in totals.items()}


def print_report(rep: AppReport, detail: bool, latex: bool) -> None:
    platform_loc = sum(loc for b, (loc, _) in rep.co_change.items() if b in PLATFORM_BUCKETS)
    dates = f"{rep.commits[0].date} .. {rep.commits[-1].date}" if rep.commits else "-"
    print(f"\n## {rep.name}")
    print(f"- commits: {len(rep.commits)} ({dates})")
    print(f"- files touched: {rep.files_touched}")
    print(f"- app LOC added (skills/templates/app code): {rep.app_loc_added}")
    print(f"- app footprint at HEAD: {rep.head_skill_files} files, {rep.head_skill_loc} LOC")
    print(f"- new skills: {len(rep.new_skills)} ({', '.join(s.split('/')[-2] for s in rep.new_skills) or '-'})")
    print(f"- new job templates: {len(rep.new_templates)} ({', '.join(rep.new_templates) or '-'})")
    print(f"- new MCP tools (net @*.tool): {rep.new_tools}")
    print(f"- platform LOC co-changed ({'+'.join(PLATFORM_BUCKETS)}): {platform_loc}")
    print("\n| co-changed subsystem | LOC added | files |")
    print("|---|---|---|")
    for bucket, (loc, files) in rep.co_change.items():
        print(f"| {bucket} | {loc} | {files} |")
    if rep.incidental:
        print(
            f"\n{len(rep.incidental)} incidental commits excluded "
            f"(app share of added LOC below threshold):"
        )
        for c, share in rep.incidental:
            print(f"- {c.sha} {c.date} ({share:.0%}) {c.subject[:60]}")
    if latex:
        print(
            f"\n% E10 row: app & skills & templates & MCP tools & platform LOC\n"
            f"{rep.name} & {len(rep.new_skills)} ({rep.head_skill_loc} LOC) & "
            f"{len(rep.new_templates)} & {rep.new_tools} & {platform_loc} \\\\"
        )
    if detail:
        print("\n| commit | date | subject | files | LOC by bucket |")
        print("|---|---|---|---|---|")
        for c in rep.commits:
            buckets: dict[str, int] = defaultdict(int)
            for path, (added, _) in c.files.items():
                buckets[classify(path, CONFIG["subsystems"])] += added
            cell = ", ".join(f"{b}:{n}" for b, n in sorted(buckets.items()) if n)
            print(f"| {c.sha} | {c.date} | {c.subject[:48]} | {len(c.files)} | {cell} |")


CONFIG: dict = {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).parent / "amortization.yaml"))
    parser.add_argument("--snapshot", action="store_true", help="platform LOC per subsystem at HEAD")
    parser.add_argument("--detail", action="store_true", help="per-commit breakdown (E12a)")
    parser.add_argument("--latex", action="store_true", help="emit LaTeX rows for the paper")
    args = parser.parse_args()

    global CONFIG
    CONFIG = yaml.safe_load(Path(args.config).read_text())
    repo = Path(git(Path(args.config).parent, "rev-parse", "--show-toplevel").strip())
    head = git(repo, "rev-parse", "--short", "HEAD").strip()
    print(f"# VISTA amortization report (M9) — {CONFIG.get('branch', 'main')} @ {head}")

    if args.snapshot:
        print("\n## Platform snapshot at HEAD (files, LOC)")
        print("\n| subsystem | files | LOC |")
        print("|---|---|---|")
        snap = snapshot(repo, CONFIG)
        for bucket in [*CONFIG["subsystems"], OTHER]:
            files, loc = snap.get(bucket, (0, 0))
            print(f"| {bucket} | {files} | {loc} |")
        print(f"| **total** | {sum(f for f, _ in snap.values())} | {sum(l for _, l in snap.values())} |")
        return

    for name, spec in CONFIG["apps"].items():
        print_report(analyze_app(repo, name, spec, CONFIG), args.detail, args.latex)


if __name__ == "__main__":
    main()
