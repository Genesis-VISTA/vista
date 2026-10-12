#!/usr/bin/env python3
"""
Fetch the code an HPC skill will wrap, into a folder the agent can read.

    python3 get_source.py https://github.com/<owner>/<repo>[/tree/<ref>[/<subpath>]] --dest DIR
    python3 get_source.py /mnt/data/uploads/code.zip --dest DIR     # .zip .tar .tar.gz .tgz
    python3 get_source.py /mnt/data/uploads/code --dest DIR          # a folder

A GitHub URL is resolved to the exact commit its branch or tag points at now,
so the skill can pin it. Archives are unpacked refusing absolute paths, `..`
and links. Writes DIR/SOURCE.json (where the code came from, and the commit)
and prints an inventory of the code (see inventory.py).

Standard library only. Public GitHub repositories only: the sandbox has no
GitHub credentials, so for a private repository ask the user to upload an
archive of it instead.
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
MAX_BYTES = 500 * 1024 * 1024
""" Refuse sources larger than this unpacked; a skill should ship or clone code, not data. """


class SourceError(Exception):
    pass


def parse_github(url: str) -> dict | None:
    m = GITHUB.match(url.strip())
    if not m:
        return None
    subpath = (m.group("subpath") or "").strip("/")
    if any(part in ("", ".", "..") for part in subpath.split("/") if subpath):
        raise SourceError(f"invalid path in {url!r}")
    return {
        "owner": m.group("owner"),
        "repo": m.group("repo"),
        "ref": m.group("ref"),
        "subpath": subpath,
    }


def _get(url: str, accept: str = "application/json") -> bytes:
    if not url.startswith("https://api.github.com/"):
        raise SourceError(f"refusing to fetch {url}")
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "vista-hpc-skill-author"})
    try:
        # The guard above restricts this to the GitHub API over https.
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise SourceError(
                f"GitHub returned {e.code} for {url}. The repository may be private or the ref "
                "may not exist; for a private repository ask the user to upload an archive."
            ) from e
        raise SourceError(f"GitHub request failed ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise SourceError(f"could not reach GitHub: {e.reason}") from e


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


def fetch_github(gh: dict, dest: Path) -> dict:
    owner, repo = gh["owner"], gh["repo"]
    api = f"https://api.github.com/repos/{owner}/{repo}"
    ref = gh["ref"] or json.loads(_get(api)).get("default_branch") or "main"
    commit = json.loads(_get(f"{api}/commits/{ref}"))["sha"]
    with tempfile.TemporaryDirectory() as tmp:
        _extract_tar(_get(f"{api}/tarball/{commit}", accept="application/vnd.github+json"),
                     Path(tmp), strip_top=True)
        src = Path(tmp) / gh["subpath"] if gh["subpath"] else Path(tmp)
        if not src.is_dir():
            raise SourceError(f"{gh['subpath']!r} is not a folder in {owner}/{repo}@{ref}")
        shutil.copytree(src, dest, dirs_exist_ok=True)
    return {
        "kind": "github",
        "url": f"https://github.com/{owner}/{repo}",
        "clone_url": f"https://github.com/{owner}/{repo}.git",
        "ref": ref,
        "commit": commit,
        "subpath": gh["subpath"],
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
    gh = parse_github(source)
    if gh is not None:
        record = fetch_github(gh, dest)
    elif source.startswith(("http://", "https://")):
        raise SourceError("only github.com URLs can be fetched; for anything else ask the user "
                          "to upload an archive")
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
    parser.add_argument("source", help="a github.com URL, a .zip/.tar archive, or a folder")
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
