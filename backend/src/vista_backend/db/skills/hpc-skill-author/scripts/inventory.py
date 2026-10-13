#!/usr/bin/env python3
"""
Summarize a codebase an HPC skill will wrap: what it is, how it builds, how it
runs, and what it already does on a cluster.

    python3 inventory.py DIR [--json]

Lists the READMEs, build and environment files, existing batch scripts (with
their #SBATCH lines), entry points (scripts with a shebang or a __main__
block) and their command-line flags, the environment variables the code
reads, and the size of the tree. It reads; it runs nothing. Use it to decide
what the job runs and which flags become script_args.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "build", "dist"}
BUILD_FILES = {
    "CMakeLists.txt", "Makefile", "makefile", "GNUmakefile", "configure", "meson.build",
    "setup.py", "setup.cfg", "pyproject.toml", "requirements.txt", "environment.yml",
    "environment.yaml", "Pipfile", "Cargo.toml", "spack.yaml", "Dockerfile", "Containerfile",
}
BUILD_SUFFIXES = (".def", ".sif.def", ".spec")
BATCH_SUFFIXES = (".slurm", ".sbatch", ".pbs", ".lsf")
SCRIPT_SUFFIXES = (".py", ".sh", ".bash")
MAX_READ = 512 * 1024

_SBATCH = re.compile(r"^#(?:SBATCH|PBS|BSUB)\s+(.*)$", re.M)
_FLAG = re.compile(r"""add_argument\(\s*['"](--?[A-Za-z0-9][\w-]*)['"]""")
_CLICK = re.compile(r"""@click\.option\(\s*['"](--?[A-Za-z0-9][\w-]*)['"]""")
_ENV = re.compile(r"""os\.environ(?:\.get)?[\[(]\s*['"]([A-Z][A-Z0-9_]+)['"]|getenv\(\s*['"]([A-Z][A-Z0-9_]+)['"]""")
_SH_ENV = re.compile(r"\$\{?([A-Z][A-Z0-9_]{2,})(?::?[-=?+][^}]*)?\}?")
_SH_BUILTIN = {"HOME", "PATH", "PWD", "USER", "SHELL", "HOSTNAME", "TMPDIR", "LANG", "IFS", "OLDPWD"}


def _read(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_READ)
    except OSError:
        return ""
    if b"\0" in data[:1024]:
        return ""
    return data.decode("utf-8", errors="replace")


def inventory(root: Path) -> dict:
    root = Path(root)
    out: dict = {"root": str(root), "files": 0, "bytes": 0, "readmes": [], "build": [],
                 "batch_scripts": [], "entry_points": [], "env_vars": [], "skill_md": None}
    env: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        here = Path(dirpath)
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            out["files"] += 1
            out["bytes"] += path.stat().st_size
            if name in ("SKILL.md", "skill.md") and here == root:
                out["skill_md"] = rel
            if name.lower().startswith("readme"):
                out["readmes"].append(rel)
            if name in BUILD_FILES or name.endswith(BUILD_SUFFIXES) or name.startswith("requirements"):
                out["build"].append(rel)
            if not (name.endswith(BATCH_SUFFIXES + SCRIPT_SUFFIXES) or os.access(path, os.X_OK)):
                continue
            text = _read(path)
            if not text:
                continue
            directives = _SBATCH.findall(text)
            if directives or name.endswith(BATCH_SUFFIXES):
                out["batch_scripts"].append({"path": rel, "directives": directives[:20]})
            if text.startswith("#!") or "__main__" in text:
                flags = sorted(set(_FLAG.findall(text)) | set(_CLICK.findall(text)))
                out["entry_points"].append({"path": rel, "flags": flags})
            for a, b in _ENV.findall(text):
                env.add(a or b)
            if name.endswith((".sh", ".bash") + BATCH_SUFFIXES):
                env.update(v for v in _SH_ENV.findall(text) if v not in _SH_BUILTIN
                           and not v.startswith(("SLURM_", "SBATCH_", "PBS_")))
    out["env_vars"] = sorted(env)
    return out


def render(inv: dict) -> str:
    lines = [f"INVENTORY {inv['root']}: {inv['files']} files, {inv['bytes'] / 1024 / 1024:.1f} MB"]
    if inv["skill_md"]:
        lines.append(f"  already a skill: {inv['skill_md']} (check for hpc_jobs/ before wrapping it)")
    for key, title in (("readmes", "readme"), ("build", "build/env")):
        for item in inv[key]:
            lines.append(f"  {title}: {item}")
    for item in inv["batch_scripts"]:
        lines.append(f"  batch script: {item['path']}")
        lines += [f"      #SBATCH {d}" for d in item["directives"]]
    for item in inv["entry_points"]:
        flags = f" flags: {' '.join(item['flags'])}" if item["flags"] else ""
        lines.append(f"  entry point: {item['path']}{flags}")
    if inv["env_vars"]:
        lines.append(f"  env vars read: {' '.join(inv['env_vars'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.path.is_dir():
        print(f"ERROR: {args.path} is not a folder", file=sys.stderr)
        return 1
    inv = inventory(args.path)
    print(json.dumps(inv, indent=2) if args.json else render(inv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
