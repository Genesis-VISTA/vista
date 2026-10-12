#!/usr/bin/env python3
"""
Fetch the code an HPC skill will wrap, into a folder the agent can read.

    python3 get_source.py https://github.com/<owner>/<repo>[/tree/<ref>[/<subpath>]] --dest DIR
    python3 get_source.py https://gitlab.com/<group>[/<subgroup>...]/<repo>[/-/tree/<ref>[/<subpath>]] --dest DIR
    python3 get_source.py /mnt/data/uploads/code.zip --dest DIR     # .zip .tar .tar.gz .tgz
    python3 get_source.py /mnt/data/uploads/code --dest DIR          # a folder

A GitHub or GitLab URL is resolved to the exact commit its branch or tag
points at now, so the skill can pin it. Archives are unpacked refusing absolute paths, `..`
and links. Writes DIR/SOURCE.json (where the code came from, and the commit)
and prints an inventory of the code (see inventory.py).

Standard library only. Public repositories on github.com and gitlab.com only:
the sandbox has no credentials for either, so for a private repository (or one
on another host) ask the user to upload an archive of it instead.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True  # no __pycache__ beside the script in the skill
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402

GITHUB = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/\s]+)(?:/(?P<subpath>.+?))?)?/?$"
)
# GitLab projects can sit in nested groups, so the project path is everything
# before `/-/` (or the whole path). A branch name containing `/` cannot be told
# apart from a subpath in a tree URL; pass such a ref as a commit or tag.
GITLAB = re.compile(
    r"^https?://gitlab\.com/(?P<project>[^\s?#]+?)(?:\.git)?"
    r"(?:/-/tree/(?P<ref>[^/\s?#]+)(?:/(?P<subpath>[^\s?#]+?))?)?/?(?:[?#]\S*)?$"
)
_PATH_PART = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")
API_PREFIXES = ("https://api.github.com/", "https://gitlab.com/api/v4/")
""" The only URLs this script fetches. """
MAX_BYTES = 500 * 1024 * 1024
""" Refuse sources larger than this unpacked; a skill should ship or clone code, not data. """


class SourceError(Exception):
    pass


def _subpath(raw: str | None, url: str) -> str:
    subpath = (raw or "").strip("/")
    if any(part in ("", ".", "..") for part in subpath.split("/") if subpath):
        raise SourceError(f"invalid path in {url!r}")
    return subpath


def parse_remote(url: str) -> dict | None:
    """
    `{"host", "project", "ref", "subpath"}` for a github.com or gitlab.com
    repository URL, `project` being `owner/repo` (GitHub) or the full
    `group/.../repo` path (GitLab); None for anything else.
    """
    url = url.strip()
    m = GITHUB.match(url)
    if m:
        return {"host": "github", "project": f"{m.group('owner')}/{m.group('repo')}",
                "ref": m.group("ref"), "subpath": _subpath(m.group("subpath"), url)}
    m = GITLAB.match(url)
    if m:
        project = m.group("project")
        parts = project.split("/")
        if len(parts) < 2 or "-" in parts or not all(_PATH_PART.fullmatch(p) and p != ".." for p in parts):
            raise SourceError(f"{url!r} is not a GitLab project URL (https://gitlab.com/<group>/<repo>)")
        return {"host": "gitlab", "project": project,
                "ref": m.group("ref"), "subpath": _subpath(m.group("subpath"), url)}
    return None


def _get(url: str, accept: str = "application/json") -> bytes:
    if not url.startswith(API_PREFIXES):
        raise SourceError(f"refusing to fetch {url}")
    host = "GitLab" if url.startswith("https://gitlab.com/") else "GitHub"
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "vista-hpc-skill-author"})
    try:
        # The guard above restricts this to the GitHub and GitLab APIs over https.
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise SourceError(
                f"{host} returned {e.code} for {url}. The repository may be private or the ref "
                "may not exist; for a private repository ask the user to upload an archive."
            ) from e
        raise SourceError(f"{host} request failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise SourceError(f"could not reach {host}: {e.reason}") from e


def _check_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise SourceError(f"archive member {name!r} would land outside the destination")
    return path


def _extract_tar(data: io.BufferedIOBase | bytes, dest: Path, *, strip_top: bool) -> None:
    fileobj = io.BytesIO(data) if isinstance(data, bytes) else data
    total = 0
    with tarfile.open(fileobj=fileobj, mode="r:*") as tar:
        for member in tar.getmembers():
            path = _check_member(member.name)
            if strip_top:
                path = PurePosixPath(*path.parts[1:])
            if not path.parts or member.isdir():
                continue
            if not member.isfile():
                continue  # links, devices, fifos: never unpacked
            total += member.size
            if total > MAX_BYTES:
                raise SourceError(f"source is larger than {MAX_BYTES // 1024 // 1024} MB unpacked")
            target = dest.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if member.mode & 0o111:
                target.chmod(0o755)


def _extract_zip(path: Path, dest: Path) -> None:
    total = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            rel = _check_member(info.filename)
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # a symlink
            total += info.file_size
            if total > MAX_BYTES:
                raise SourceError(f"source is larger than {MAX_BYTES // 1024 // 1024} MB unpacked")
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _single_top(dest: Path) -> Path:
    """An archive of one folder (code/...) is that folder."""
    entries = [p for p in dest.iterdir() if p.name not in ("__MACOSX", ".DS_Store")]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest


def _resolve(remote: dict) -> tuple[str, str, bytes]:
    """`(ref, commit, tarball)` for a parsed remote: the branch or tag pinned to its commit now."""
    project, ref = remote["project"], remote["ref"]
    if remote["host"] == "github":
        api = f"https://api.github.com/repos/{project}"
        ref = ref or json.loads(_get(api)).get("default_branch") or "main"
        commit = json.loads(_get(f"{api}/commits/{urllib.parse.quote(ref, safe='')}"))["sha"]
        return ref, commit, _get(f"{api}/tarball/{commit}", accept="application/vnd.github+json")
    api = f"https://gitlab.com/api/v4/projects/{urllib.parse.quote(project, safe='')}"
    ref = ref or json.loads(_get(api)).get("default_branch") or "main"
    commit = json.loads(_get(f"{api}/repository/commits/{urllib.parse.quote(ref, safe='')}"))["id"]
    return ref, commit, _get(f"{api}/repository/archive.tar.gz?sha={commit}", accept="application/gzip")


def fetch_remote(remote: dict, dest: Path) -> dict:
    ref, commit, tarball = _resolve(remote)
    with tempfile.TemporaryDirectory() as tmp:
        # Both hosts wrap the tree in one top-level folder named after the commit.
        _extract_tar(tarball, Path(tmp), strip_top=True)
        src = Path(tmp) / remote["subpath"] if remote["subpath"] else Path(tmp)
        if not src.is_dir():
            raise SourceError(f"{remote['subpath']!r} is not a folder in {remote['project']}@{ref}")
        shutil.copytree(src, dest, dirs_exist_ok=True)
    web = f"https://{remote['host']}.com/{remote['project']}"
    return {
        "kind": remote["host"],
        "url": web,
        "clone_url": f"{web}.git",
        "ref": ref,
        "commit": commit,
        "subpath": remote["subpath"],
    }


def _skip_in_folder(folder: str, names: list[str]) -> set[str]:
    """Clutter, and links: a link is never followed out of the folder it is in."""
    return {n for n in names
            if n in (".git", "__pycache__", "node_modules", ".venv") or (Path(folder) / n).is_symlink()}


def fetch(source: str, dest: Path) -> dict:
    """Put `source` into `dest` (created; must be empty) and return its SOURCE.json record."""
    if dest.exists() and any(dest.iterdir()):
        raise SourceError(f"{dest} is not empty; pass a new --dest")
    dest.mkdir(parents=True, exist_ok=True)
    remote = parse_remote(source)
    if remote is not None:
        record = fetch_remote(remote, dest)
    elif source.startswith(("http://", "https://")):
        raise SourceError("only github.com and gitlab.com URLs can be fetched; for anything else "
                          "ask the user to upload an archive")
    else:
        path = Path(source)
        if path.is_dir():
            shutil.copytree(path, dest, dirs_exist_ok=True, ignore=_skip_in_folder)
            record = {"kind": "folder", "path": str(path)}
        elif path.is_file() and zipfile.is_zipfile(path):
            with tempfile.TemporaryDirectory() as tmp:
                _extract_zip(path, Path(tmp))
                shutil.copytree(_single_top(Path(tmp)), dest, dirs_exist_ok=True)
            record = {"kind": "archive", "path": str(path)}
        elif path.is_file() and tarfile.is_tarfile(path):
            with tempfile.TemporaryDirectory() as tmp, open(path, "rb") as f:
                _extract_tar(f, Path(tmp), strip_top=False)
                shutil.copytree(_single_top(Path(tmp)), dest, dirs_exist_ok=True)
            record = {"kind": "archive", "path": str(path)}
        else:
            raise SourceError(f"{source} is not a GitHub URL, a folder, or a .zip/.tar archive")
    (dest / "SOURCE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("source", help="a github.com or gitlab.com URL, a .zip/.tar archive, or a folder")
    parser.add_argument("--dest", type=Path, required=True,
                        help="where to put the code, e.g. /mnt/data/work/<name> (must be new or empty)")
    args = parser.parse_args(argv)
    try:
        record = fetch(args.source, args.dest)
    except SourceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("SOURCE " + json.dumps(record))
    print()
    print(inventory.render(inventory.inventory(args.dest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
