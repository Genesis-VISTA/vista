"""
Import a skill from a folder uploaded by the browser, or from a folder on the
backend's own disk (the agent's output). A repository import is
`skill_repo.py`.

A local import receives the files of a folder the user picked in the browser,
each with its path relative to that folder (see `import_skill_from_files`).

Either way we copy the entire skill directory (SKILL.md plus any sibling
scripts / references / assets) to the caller-provided destination directory,
and `is_public` is forced to `false` so imports land private (the user can
publish them via the /skills tab).
"""

import os
import shutil
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from pydantic import ValidationError

from .skills import (
    Skill,
    SkillError,
    find_skill_md,
    read_skill,
)


class SkillImportError(Exception):
    """Raised for any user-facing problem during import (parse / fetch / layout)."""


def _install_skill(source_dir: Path, dest_dir: Path, where: str) -> Skill:
    """
    Validate the skill in `source_dir` and copy it to `dest_dir`.

    `where` names the source in error messages, e.g. "<repo root> in owner/repo".
    """
    if find_skill_md(source_dir) is None:
        raise SkillImportError(f"No SKILL.md found at {where}.")
    try:
        skill = read_skill(source_dir)
    except (SkillError, ValidationError) as e:
        raise SkillImportError(f"Imported SKILL.md is invalid: {e}") from e

    shutil.copytree(source_dir, dest_dir)
    return skill


MAX_UPLOAD_FILES = 2000
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Clutter a folder picker drags along that never belongs in a skill. Anything
# under these directories, or with these names, is dropped rather than rejected.
_IGNORED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv"})
_IGNORED_FILES = frozenset({".DS_Store", "Thumbs.db"})


def _clean_relpath(raw: str) -> PurePosixPath:
    """
    Check one uploaded file's relative path; reject anything that could land
    outside the skill directory.
    """
    if not raw or "\\" in raw or ":" in raw or "\0" in raw:
        raise SkillImportError(f"Invalid file path in upload: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in raw.split("/")):
        raise SkillImportError(f"Invalid file path in upload: {raw!r}")
    return path


def _is_ignored(path: PurePosixPath) -> bool:
    return path.name in _IGNORED_FILES or any(
        part in _IGNORED_DIRS for part in path.parts[:-1]
    )


def import_skill_from_files(
    files: Iterable[tuple[str, BinaryIO]], dest_dir: Path | str
) -> Skill:
    """
    Import a skill from uploaded files into `dest_dir` (which must not already exist).

    `files` pairs each file's path, relative to the folder the user picked,
    with its contents. A browser folder picker prefixes every path with the
    folder's own name ("my-skill/SKILL.md"); when all paths share one leading
    directory it is stripped, so SKILL.md is expected at the root of the
    picked folder.

    Returns the imported `Skill`. Raises `SkillImportError` for any
    user-facing problem.
    """
    entries = [(_clean_relpath(raw), stream) for raw, stream in files]
    entries = [(path, stream) for path, stream in entries if not _is_ignored(path)]
    if not entries:
        raise SkillImportError("No files were uploaded.")
    if len(entries) > MAX_UPLOAD_FILES:
        raise SkillImportError(
            f"Too many files ({len(entries)}); a skill may have at most {MAX_UPLOAD_FILES}."
        )

    tops = {path.parts[0] for path, _ in entries}
    if len(tops) == 1 and all(len(path.parts) > 1 for path, _ in entries):
        entries = [(PurePosixPath(*path.parts[1:]), stream) for path, stream in entries]

    seen: set[PurePosixPath] = set()
    for path, _ in entries:
        if path in seen:
            raise SkillImportError(f"Duplicate file in upload: {str(path)!r}")
        seen.add(path)

    dest_dir = Path(dest_dir).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        total = 0
        for path, stream in entries:
            target = root.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise SkillImportError(
                            f"Upload is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                        )
                    out.write(chunk)
        return _install_skill(root, dest_dir, "the root of the uploaded folder")


class _LazyFile:
    """A file opened on first read and closed at its end, so a folder with more
    files than `MAX_UPLOAD_FILES` is refused before any descriptor is opened.

    Opened without following a symlink and without blocking, and refused unless
    it is a regular file: the sandbox can swap a file for a symlink or a FIFO
    after the folder was walked, and a FIFO would block the read forever."""

    _FLAGS = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )

    def __init__(self, path: Path, rel: str):
        self._path = path
        self._rel = rel
        self._stream: BinaryIO | None = None

    def read(self, size: int = -1) -> bytes:
        if self._stream is None:
            try:
                fd = os.open(self._path, self._FLAGS)
            except OSError as e:
                raise SkillImportError(f"{self._rel} could not be read: {e.strerror}")
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise SkillImportError(f"{self._rel} is not a regular file.")
            self._stream = os.fdopen(fd, "rb")
        chunk = self._stream.read(size)
        if not chunk:
            self._stream.close()
        return chunk


def read_plain_file(path: Path, rel: str) -> bytes:
    """All of one file from a folder the sandbox can write, read as `_LazyFile`
    does: refused unless it is a regular file, never through a symlink."""
    f = _LazyFile(path, rel)
    data = b""
    while chunk := f.read(1 << 20):
        data += chunk
    return data


def plain_files(source_dir: Path | str) -> list[tuple[str, Path]]:
    """
    Every file in a folder on the backend's disk that the agent's sandbox can
    write, as `(path relative to it, file)`, refusing what would be unsafe to
    read from the host: the folder itself or anything in it being a symlink
    (the sandbox can point one anywhere on this machine), or anything but a
    file or a folder (a FIFO would block the read). Folders and files an
    import drops (`_IGNORED_DIRS`, `_IGNORED_FILES`) are skipped unread: a
    .venv the agent built holds symlinks the validator rightly ignores.

    Raises `SkillImportError`, naming the entry.
    """
    source_dir = Path(source_dir)
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise SkillImportError(f"{source_dir.name} is not a folder.")
    files: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(source_dir, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        filenames = [f for f in filenames if f not in _IGNORED_FILES]
        for name in [*dirnames, *filenames]:
            rel = (here / name).relative_to(source_dir).as_posix()
            mode = (here / name).lstat().st_mode
            if stat.S_ISLNK(mode):
                raise SkillImportError(
                    f"{rel} is a symlink; a skill imported from the output folder "
                    "may not contain symlinks."
                )
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SkillImportError(f"{rel} is not a regular file or folder.")
        for name in filenames:
            files.append(
                ((here / name).relative_to(source_dir).as_posix(), here / name)
            )
            if len(files) > MAX_UPLOAD_FILES:
                raise SkillImportError(
                    f"{source_dir.name} has more than {MAX_UPLOAD_FILES} files."
                )
    return files


def import_skill_from_dir(source_dir: Path | str, dest_dir: Path | str) -> Skill:
    """
    Import the skill in a folder on the backend's own disk, such as one the
    agent wrote to its output folder, into `dest_dir` (which must not exist).

    The folder goes through `import_skill_from_files`, so it meets exactly the
    rules and limits of a folder upload, and must pass `plain_files`. Each
    file is opened by `_LazyFile`, which checks again, since the sandbox can
    change the folder after it was walked.
    """
    source_dir = Path(source_dir)
    files: list[tuple[str, BinaryIO]] = [
        # Prefixed with the folder's name, as a folder picker would, so
        # import_skill_from_files strips exactly that one level.
        (f"{source_dir.name}/{rel}", _LazyFile(path, rel))  # type: ignore[misc]
        for rel, path in plain_files(source_dir)
    ]
    return import_skill_from_files(files, dest_dir)
