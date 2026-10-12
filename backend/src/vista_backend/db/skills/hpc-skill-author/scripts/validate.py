#!/usr/bin/env python3
"""
Check a VISTA skill that ships HPC jobs, before the researcher imports it.

    python3 validate.py /mnt/data/output/<skill>          # the whole skill
    python3 validate.py --job /mnt/data/output/<skill>/hpc_jobs/<job>
    python3 validate.py --json ...                        # machine-readable

Applies the rules VISTA applies when the skill is imported (SKILL.md, no
symlinks, size limits) and when one of its jobs is submitted (job script,
README heading, cluster_defaults.json, a section for each cluster), at least
as strictly. ERRORs are what VISTA would refuse; WARNINGs are the job contract's
advice that VISTA cannot check. Exits 1 if there is any error.

Standard library only (PyYAML is used for SKILL.md when it is installed), so
it runs in the agent's sandbox. Kept in step with VISTA by
mcp_servers/vista_mcp_server/tests/test_skill_author_validator.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CLUSTERS = ("odo", "perlmutter", "frontier", "lux")
JOB_SCRIPT = "job.{}.slurm"
SETUP_SCRIPT = "setup_{}.sh"
JOBS_DIR = "hpc_jobs"

# submit_job_mcp._NAME_PART: each part of "<skill>/<job>".
NAME_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# SkillMetadata.name.
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# skill_import: the import's limits, and the clutter it drops.
MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024
IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
IGNORED_FILES = {".DS_Store", "Thumbs.db"}

# lib/iri.py IriDefaults, IriResourceSpec, IriAttributes: field -> (types, nullable).
INT, BOOL, STR = "int", "bool", "str"
SECTION_FIELDS = {"duration": (INT, False), "resources": ("object", False), "iri": ("object", False)}
RESOURCE_FIELDS = {
    "node_count": (INT, True),
    "process_count": (INT, True),
    "processes_per_node": (INT, True),
    "cpu_cores_per_process": (INT, True),
    "exclusive_node_use": (BOOL, False),
    "gpus_per_node": (INT, True),
}
IRI_FIELDS = {
    "queue_name": (STR, False),
    "constraint": (STR, True),
    "partition": (STR, True),
    "image": (STR, True),
    "module": (STR, True),
    "pre_launch": (STR, True),
    "environment": ("env", False),
}


class Report:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def _where(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            rel = path
        return f"{rel.as_posix() if str(rel) != '.' else path.name}: "

    def error(self, path: Path | None, msg: str) -> None:
        self.errors.append(self._where(path) + msg)

    def warn(self, path: Path | None, msg: str) -> None:
        self.warnings.append(self._where(path) + msg)


def _type_ok(value, kind: str) -> bool:
    if kind == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == BOOL:
        return isinstance(value, bool)
    if kind == STR:
        return isinstance(value, str)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "env":
        return isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
    raise AssertionError(kind)


_KIND_NAMES = {INT: "an integer", BOOL: "true or false", STR: "a string",
               "object": "an object", "env": "an object of string values"}


def _check_fields(obj: dict, fields: dict, where: str, path: Path, rep: Report) -> None:
    for key, value in obj.items():
        if key not in fields:
            rep.warn(path, f"{where}.{key} is not a field VISTA knows; it is ignored")
            continue
        kind, nullable = fields[key]
        if value is None and nullable:
            continue
        if not _type_ok(value, kind):
            rep.error(path, f"{where}.{key} must be {_KIND_NAMES[kind]}{' or null' if nullable else ''}, "
                            f"not {json.dumps(value)}")


def check_cluster_defaults(job_dir: Path, rep: Report) -> dict:
    """The sections that parse, by cluster. Errors for anything VISTA's model refuses."""
    path = job_dir / "cluster_defaults.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        rep.error(path, f"not valid JSON: {e}")
        return {}
    if not isinstance(data, dict):
        rep.error(path, "must be a JSON object with one section per cluster")
        return {}
    sections = {}
    for cluster, section in data.items():
        if cluster not in CLUSTERS:
            rep.warn(path, f"section {cluster!r} is not a cluster ({', '.join(CLUSTERS)}); it is ignored")
            continue
        if section is None:
            continue
        if not isinstance(section, dict):
            rep.error(path, f"{cluster} must be an object")
            continue
        _check_fields(section, SECTION_FIELDS, cluster, path, rep)
        if isinstance(section.get("resources"), dict):
            _check_fields(section["resources"], RESOURCE_FIELDS, f"{cluster}.resources", path, rep)
        if isinstance(section.get("iri"), dict):
            _check_fields(section["iri"], IRI_FIELDS, f"{cluster}.iri", path, rep)
        sections[cluster] = section
    return sections


_ARGS_USE = re.compile(r'\$(?:@|\*|[1-9])|\$\{(?:@|\*|[1-9])\}')
_ALLOW_LIST = re.compile(r"\bcase\b[\s\S]*=\*\)")
_CONDA_BUILD = re.compile(r"\b(?:conda|mamba|micromamba)\s+(?:env\s+)?create\b")
_RUN_DIR_WRITE = re.compile(r'(?:>>?|\bmkdir\b[^\n]*|\btouch\b[^\n]*)\s*"?\$\{?RUN_DIR_')


def _code(text: str) -> str:
    """The script without comment lines (a shebang is a comment too)."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def check_script(path: Path, rep: Report) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        rep.error(path, "is not UTF-8 text")
        return
    if not text.startswith("#!"):
        rep.warn(path, "has no #! line (e.g. #!/bin/bash -l)")
    code = _code(text)
    is_job = path.name.startswith("job.")
    if is_job and "VISTA_OUT" not in code:
        rep.warn(path, "never uses $VISTA_OUT; VISTA fetches outputs only from there")
    if is_job and _ARGS_USE.search(code) and not _ALLOW_LIST.search(code):
        rep.warn(path, "reads script_args without a KEY=VALUE allow-list "
                       "(a `case` that accepts KEY=* and refuses anything else)")
    if _CONDA_BUILD.search(code) and "CONDA_PKGS_DIRS" not in code:
        rep.warn(path, "builds a conda env without moving its package cache off Lustre "
                       "(export CONDA_PKGS_DIRS and TMPDIR under /tmp first; see templates/env/"
                       "build_conda_env.sh)")
    if _RUN_DIR_WRITE.search(code):
        rep.warn(path, "writes into $RUN_DIR_<Cluster>; it is read-only on Odo and Frontier, "
                       "write under $VISTA_OUT or $VISTA_JOB_DIR")


def check_job(job_dir: Path, rep: Report, *, skill: str | None = None) -> None:
    """The rules submit_hpc_job applies to one job folder (`_load_job` and dispatch)."""
    name = job_dir.name
    if not NAME_PART.fullmatch(name):
        rep.error(job_dir, "job folder names may use only letters, digits, '.', '_' and '-', "
                           "and must start with a letter or digit")
    scripts = [c for c in CLUSTERS if (job_dir / JOB_SCRIPT.format(c)).is_file()]
    if not scripts:
        rep.error(job_dir, "no job script; add at least one of "
                           + ", ".join(JOB_SCRIPT.format(c) for c in CLUSTERS))
    readme = job_dir / "README.md"
    if not readme.is_file():
        rep.error(job_dir, "no README.md")
    else:
        first = readme.read_text(encoding="utf-8").strip().split("\n", 1)[0].strip()
        if first != f"# {name}":
            rep.error(readme, f'must start with the line "# {name}" (the job folder\'s name'
                              + (f', not "{skill}/{name}"' if skill else "") + f"), found {first!r}")
    if scripts and not (job_dir / "cluster_defaults.json").is_file():
        rep.error(job_dir, "no cluster_defaults.json; VISTA refuses to submit to a cluster without "
                           'a section for it (e.g. {"' + scripts[0] + '": {}})')
    sections = check_cluster_defaults(job_dir, rep)
    defaults = job_dir / "cluster_defaults.json"
    for cluster in scripts:
        if defaults.is_file() and cluster not in sections:
            rep.error(defaults, f"no {cluster!r} section for {JOB_SCRIPT.format(cluster)}; "
                                f"VISTA refuses to submit to {cluster} without one")
    for cluster in sections:
        if cluster not in scripts:
            rep.warn(defaults, f"has a {cluster!r} section but there is no {JOB_SCRIPT.format(cluster)}")
    lux = sections.get("lux")
    if lux is not None:
        iri, res = lux.get("iri") or {}, lux.get("resources") or {}
        if not (isinstance(iri, dict) and iri.get("partition")):
            rep.error(defaults, "lux.iri.partition is required; Lux refuses a job with no partition")
        if not (isinstance(res, dict) and res.get("gpus_per_node")):
            rep.error(defaults, "lux.resources.gpus_per_node is required; Lux refuses a job "
                                "that selects no GPUs")
    pm = sections.get("perlmutter")
    if pm is not None and not (isinstance(pm.get("iri"), dict) and pm["iri"].get("constraint")):
        rep.warn(defaults, 'perlmutter.iri.constraint is unset; Perlmutter needs "gpu" or "cpu"')
    for legacy in ("job.slurm", "s3m_defaults.json"):
        if (job_dir / legacy).exists():
            rep.warn(job_dir / legacy, "is a legacy name VISTA no longer reads")
    for cluster in CLUSTERS:
        for script in (job_dir / JOB_SCRIPT.format(cluster), job_dir / SETUP_SCRIPT.format(cluster)):
            if script.is_file():
                check_script(script, rep)


def _parse_frontmatter(text: str, path: Path, rep: Report) -> dict | None:
    """SKILL.md frontmatter as VISTA's parse_skill reads it."""
    if not text.startswith("---"):
        rep.error(path, "must start with YAML frontmatter (---)")
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        rep.error(path, "frontmatter is not closed with ---")
        return None
    try:
        import yaml  # noqa: PLC0415 - optional
    except ImportError:
        rep.warn(path, "PyYAML is not installed, so only `name` and `description` were checked")
        return _minimal_frontmatter(parts[1])
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        rep.error(path, f"invalid YAML in the frontmatter: {e}")
        return None
    if not isinstance(data, dict):
        rep.error(path, "frontmatter must be a YAML mapping")
        return None
    meta = data.get("metadata")
    if meta is not None and not (
        isinstance(meta, dict)
        and all(isinstance(v, str) or (isinstance(v, list) and all(isinstance(i, str) for i in v))
                for v in meta.values())
    ):
        rep.error(path, 'metadata values must be strings or lists of strings (quote numbers: version: "0.1")')
    for key in ("license", "compatibility", "allowed_tools", "author"):
        if data.get(key) is not None and not isinstance(data[key], str):
            rep.error(path, f"{key} must be a string")
    return data


def _minimal_frontmatter(block: str) -> dict:
    """Top-level `name:` and `description:` (inline, or a folded/literal block)."""
    data: dict = {}
    lines = block.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(name|description):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in ("", ">", ">-", "|", "|-"):
            body = []
            for nxt in lines[i + 1:]:
                if nxt and not nxt[0].isspace():
                    break
                body.append(nxt.strip())
            value = " ".join(b for b in body if b)
        data[key] = value.strip("'\"")
    return data


def _walk(root: Path, rep: Report) -> None:
    """The import's file rules: no symlinks, at most MAX_FILES files and MAX_BYTES."""
    count = total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for d in dirnames:
            if (here / d).is_symlink():
                rep.error(here / d, "is a symlink; the import refuses symlinks, copy the files instead")
        for f in filenames:
            p = here / f
            if p.is_symlink():
                rep.error(p, "is a symlink; the import refuses symlinks, copy the file instead")
                continue
            if f in IGNORED_FILES:
                continue
            count += 1
            total += p.stat().st_size
    if count > MAX_FILES:
        rep.error(None, f"{count} files; a skill may have at most {MAX_FILES}")
    if total > MAX_BYTES:
        rep.error(None, f"{total / 1024 / 1024:.1f} MB; a skill may be at most "
                        f"{MAX_BYTES // 1024 // 1024} MB. Ship sources and configs; "
                        "download models and build images on the cluster")


def check_skill(skill_dir: Path, rep: Report) -> list[str]:
    """Every rule for a skill folder; returns the `<skill>/<job>` names it ships."""
    _walk(skill_dir, rep)
    skill_md = next((skill_dir / n for n in ("SKILL.md", "skill.md") if (skill_dir / n).is_file()), None)
    name = skill_dir.name
    text = ""
    if skill_md is None:
        rep.error(None, "no SKILL.md at the top of the skill folder")
    else:
        text = skill_md.read_text(encoding="utf-8")
        data = _parse_frontmatter(text, skill_md, rep)
        if data is not None:
            declared = data.get("name")
            if not isinstance(declared, str) or not SKILL_NAME.match(declared):
                rep.error(skill_md, f"name must be kebab-case (e.g. lammps-on-frontier), found {declared!r}")
            elif declared != name:
                rep.error(skill_md, f"name is {declared!r} but the folder is {name!r}; they must match, "
                                    "since jobs are submitted as <name>/<job>")
            desc = data.get("description")
            if not isinstance(desc, str) or not desc.strip():
                rep.error(skill_md, "description is required")
    jobs_root = skill_dir / JOBS_DIR
    jobs: list[str] = []
    if not jobs_root.is_dir():
        rep.warn(None, f"no {JOBS_DIR}/ folder, so the skill ships no HPC jobs")
        return jobs
    for entry in sorted(jobs_root.iterdir()):
        if entry.name.startswith(".") or entry.name in IGNORED_DIRS or entry.is_symlink():
            continue
        if not entry.is_dir():
            rep.warn(entry, f"is a file; only folders under {JOBS_DIR}/ are jobs")
            continue
        check_job(entry, rep, skill=name)
        full = f"{name}/{entry.name}"
        jobs.append(full)
        if skill_md is not None and full not in text:
            rep.error(skill_md, f"does not name the job {full!r}; submit_hpc_job lists only VISTA's "
                                "built-in jobs, so the SKILL.md must say how to call it")
    if not jobs:
        rep.warn(jobs_root, "holds no job folders")
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("path", type=Path, help="the skill folder, or a job folder with --job")
    parser.add_argument("--job", action="store_true", help="check a single job folder")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)

    path: Path = args.path
    rep = Report(path)
    jobs: list[str] = []
    if path.is_symlink() or not path.is_dir():
        rep.error(None, f"{path} is not a folder")
    elif args.job:
        _walk(path, rep)
        check_job(path, rep)
        jobs = [path.name]
    else:
        jobs = check_skill(path, rep)

    ok = not rep.errors
    if args.json:
        print(json.dumps({"ok": ok, "path": str(path), "jobs": jobs,
                          "errors": rep.errors, "warnings": rep.warnings}, indent=2))
    else:
        for e in rep.errors:
            print(f"ERROR   {e}")
        for w in rep.warnings:
            print(f"WARNING {w}")
        if ok:
            print(f"OK: {path.name}" + (f" (jobs: {', '.join(jobs)})" if jobs else ""))
        else:
            print(f"FAILED: {len(rep.errors)} error(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
