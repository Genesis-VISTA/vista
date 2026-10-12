#!/usr/bin/env python3
"""
Unpack code the user uploaded, for an HPC skill to wrap, into a folder the agent
can read.

    python3 get_source.py /mnt/data/uploads/code.zip --dest DIR     # .zip .tar .tar.gz .tgz
    python3 get_source.py /mnt/data/uploads/code --dest DIR          # a folder

Archives are unpacked refusing absolute paths, `..` and links. Writes
DIR/SOURCE.json (where the code came from) and prints an inventory of the code
(see inventory.py).

This reads local files only. For a repository on github.com or gitlab.com use
the `fetch_repository` tool instead: the backend downloads it, pinned to a
commit, with credentials and network trust the sandbox does not have.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

sys.dont_write_bytecode = True  # no __pycache__ beside the script in the skill
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402

MAX_BYTES = 500 * 1024 * 1024
""" Refuse sources larger than this unpacked; a skill should ship or clone code, not data. """


class SourceError(Exception):
    pass


def _check_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise SourceError(f"archive member {name!r} would land outside the destination")
    return path


def _extract_tar(fileobj: BinaryIO, dest: Path) -> None:
    total = 0
    with tarfile.open(fileobj=fileobj, mode="r:*") as tar:
        for member in tar.getmembers():
            path = _check_member(member.name)
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


def _skip_in_folder(folder: str, names: list[str]) -> set[str]:
    """Clutter, and links: a link is never followed out of the folder it is in."""
    return {n for n in names
            if n in (".git", "__pycache__", "node_modules", ".venv") or (Path(folder) / n).is_symlink()}


def fetch(source: str, dest: Path) -> dict:
    """Put `source` into `dest` (created; must be empty) and return its SOURCE.json record."""
    if source.startswith(("http://", "https://")):
        raise SourceError("this script reads uploaded files only; fetch a github.com or gitlab.com "
                          "repository with the fetch_repository tool, and for anything else ask "
                          "the user to upload an archive")
    path = Path(source)
    if not (path.is_dir() or (path.is_file() and (zipfile.is_zipfile(path) or tarfile.is_tarfile(path)))):
        raise SourceError(f"{source} is not a folder or a .zip/.tar archive")
    if dest.exists() and any(dest.iterdir()):
        raise SourceError(f"{dest} is not empty; pass a new --dest")
    dest.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, dest, dirs_exist_ok=True, ignore=_skip_in_folder)
        record = {"kind": "folder", "path": str(path)}
    else:
        with tempfile.TemporaryDirectory() as tmp:
            if zipfile.is_zipfile(path):
                _extract_zip(path, Path(tmp))
            else:
                with open(path, "rb") as f:
                    _extract_tar(f, Path(tmp))
            shutil.copytree(_single_top(Path(tmp)), dest, dirs_exist_ok=True)
        record = {"kind": "archive", "path": str(path)}
    (dest / "SOURCE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("source", help="an uploaded .zip/.tar archive, or a folder")
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
