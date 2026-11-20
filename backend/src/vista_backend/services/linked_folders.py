"""
Folders on this computer linked to a project, mounted read-only into its sandboxes
(openspec change linked-folders).
"""

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, Sequence

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.schemas import ProjectLinkedFolderTable
from ..utils.misc import path_is_under

logger = logging.getLogger(__name__)

LINKED_SANDBOX_ROOT = PurePosixPath("/mnt/linked")
""" Where linked folders appear in the sandbox, one folder per link name. """

LINKED_VOLUME_DIR = "linked"
""" `LINKED_SANDBOX_ROOT` within the project volume (mounted at `/mnt`), where the mount points are. """

UnavailableReason = Literal["missing", "not a folder", "unreadable"]

LinkedVolume = tuple[str, str, Literal["r"], Literal[False]]
""" A dev server volume: (host path, sandbox path, read-only, not owned by VISTA). """


class LinkedFolder(Protocol):
    """A project's link, as stored: its name and the folder's path on this computer."""

    @property
    def name(self) -> str: ...

    @property
    def host_path(self) -> str: ...


@dataclass(frozen=True)
class LinkedVolumes:
    """A project's links, split by whether a sandbox starting now can mount them."""

    available: list[LinkedVolume] = field(default_factory=list)
    unavailable: list[tuple[str, UnavailableReason]] = field(default_factory=list)

    @property
    def available_names(self) -> set[str]:
        return {PurePosixPath(sandbox).name for _, sandbox, _, _ in self.available}


def sandbox_path(name: str) -> str:
    return str(LINKED_SANDBOX_ROOT / name)


def link_status(host_path: str) -> tuple[Path, None] | tuple[None, UnavailableReason]:
    """
    A stored link's folder as a sandbox starting now would mount it, resolved again, or why
    it can't be mounted.

    Resolved again because it was stored resolved, but a folder on the way may since have
    become a symlink, and microsandbox fails the whole start, not just the mount, on a host
    path through a symlink.
    """
    try:
        host = Path(host_path).resolve()
    except OSError, RuntimeError:  # a symlink loop, or a path the OS refuses
        return None, "unreadable"
    if not host.exists():
        return None, "missing"
    if not host.is_dir():
        return None, "not a folder"
    if not os.access(host, os.R_OK | os.X_OK):
        return None, "unreadable"
    return host, None


def resolve_linked_volumes(links: Sequence[LinkedFolder]) -> LinkedVolumes:
    """
    Turn a project's links into read-only volumes for the ones a sandbox can mount now, and
    the reason for each one it can't.
    """
    out = LinkedVolumes()
    for link in links:
        host, reason = link_status(link.host_path)
        if host is None:
            assert reason is not None
            out.unavailable.append((link.name, reason))
        else:
            out.available.append((str(host), sandbox_path(link.name), "r", False))
    return out


def clear_leftover_mount_points(volume_root: Path, keep: set[str]) -> None:
    """
    Remove the empty mount points a sandbox left in the project volume for links that are no
    longer available, so the next sandbox has no empty `/mnt/linked/<old name>`.

    Both sandbox backends create `linked/<name>` in the project volume to mount a link over,
    and leave it when they close. Only empty folders go, and never recursively, so anything
    the agent wrote under `/mnt/linked` stays. A removal the host refuses (Docker Desktop on
    macOS, while another conversation's container still has it mounted) is skipped: it
    leaves that sandbox's mount as it was either way.
    """
    root = volume_root / LINKED_VOLUME_DIR
    if not root.is_dir() or root.is_symlink():
        return
    for entry in root.iterdir():
        if entry.name in keep or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            entry.rmdir()
        except OSError as e:
            logger.debug("Leaving mount point %s: %s", entry, e)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

_NAME_OK = re.compile(r"[A-Za-z0-9._-]+")
_NOT_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class LinkRefused(Exception):
    """A link, rename or unlink refused: `status`, a `code` the UI tells apart, the sentence."""

    def __init__(self, status: int, code: str, detail: str, **extra: Any):
        super().__init__(detail)
        self.status, self.code, self.detail, self.extra = status, code, detail, extra


def _taken(names: Sequence[str]) -> set[str]:
    # Case-insensitively: the mount points share a folder in the project volume, and on
    # macOS and Windows `Data` and `data` would be one folder.
    return {n.casefold() for n in names}


def default_name(folder: str, taken: Sequence[str]) -> str:
    """
    A link's name from its folder's: each run of characters other than ASCII letters,
    digits, `.`, `_` and `-` to one `-`, leading and trailing `-` and `.` removed, `folder`
    if nothing is left, then `-2`, `-3`, … while another link has it.
    """
    base = _NOT_NAME.sub("-", folder).strip("-.") or "folder"
    used = _taken(taken)
    name, n = base, 1
    while name.casefold() in used:
        n += 1
        name = f"{base}-{n}"
    return name


def check_name(name: str, taken: Sequence[str]) -> None:
    """Refuse a name a link can't have, or that another link of the project has."""
    if name in ("", ".", "..") or not _NAME_OK.fullmatch(name):
        raise LinkRefused(
            400,
            "invalid_name",
            f"“{name}” can't name a linked folder: use letters, digits, “.”, “_” and “-”.",
        )
    if name.casefold() in _taken(taken):
        raise LinkRefused(
            409, "name_taken", f"Another linked folder is already named “{name}”."
        )


# ---------------------------------------------------------------------------
# What can be linked
# ---------------------------------------------------------------------------


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a == b or os.path.samefile(a, b)
    except OSError:
        return False


def _overlaps(a: Path, b: Path) -> bool:
    """Equal, either inside the other."""
    return _same_folder(a, b) or path_is_under(a, b) or path_is_under(b, a)


def is_broad(path: Path) -> bool:
    """The home folder or a filesystem root (`/`, `C:\\`): the agent could read everything."""
    return path.parent == path or _same_folder(path, Path.home().resolve())


def check_linkable(
    path: str, existing: Sequence[LinkedFolder], confirm_broad: bool = False
) -> Path:
    """
    The folder to link, resolved, or why it can't be: not an existing folder, already
    linked, VISTA's own data (or a folder inside or holding it), or a broad folder the
    researcher hasn't confirmed.
    """
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise LinkRefused(
            400, "not_a_folder", f"{path} is not an absolute path to a folder."
        )
    try:
        folder = raw.resolve()
    except OSError, RuntimeError:
        folder = None
    if folder is None or not folder.is_dir():
        raise LinkRefused(400, "not_a_folder", f"{path} is not an existing folder.")
    for link in existing:
        if _same_folder(folder, Path(link.host_path)):
            raise LinkRefused(
                409,
                "already_linked",
                f"This folder is already linked as “{link.name}”.",
                link=link.name,
            )
    if _overlaps(folder, settings.data_dir.resolve()):
        raise LinkRefused(
            400,
            "vista_data",
            "VISTA's own data can't be linked: this folder is, holds, or is inside "
            "VISTA's data folder.",
        )
    if is_broad(folder) and not confirm_broad:
        raise LinkRefused(
            409,
            "broad_folder",
            f"{folder} is your home folder or a whole drive: the agent will be able to "
            "read everything in it. Link it anyway?",
            path=str(folder),
        )
    return folder


# ---------------------------------------------------------------------------
# A project's links
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkedFolderStatus:
    """A link as the settings and Files page show it."""

    name: str
    host_path: str
    sandbox_path: str
    available: bool
    reason: UnavailableReason | None


def status_of(row: ProjectLinkedFolderTable) -> LinkedFolderStatus:
    host, reason = link_status(row.host_path)
    return LinkedFolderStatus(
        name=row.name,
        host_path=row.host_path,
        sandbox_path=sandbox_path(row.name),
        available=host is not None,
        reason=reason,
    )


async def list_links(
    session: AsyncSession, project_id: uuid.UUID
) -> list[ProjectLinkedFolderTable]:
    return list(
        (
            await session.exec(
                select(ProjectLinkedFolderTable)
                .where(ProjectLinkedFolderTable.project_id == project_id)
                .order_by(col(ProjectLinkedFolderTable.created_at))
            )
        ).all()
    )


async def _get_link(
    session: AsyncSession, project_id: uuid.UUID, name: str
) -> ProjectLinkedFolderTable:
    for row in await list_links(session, project_id):
        if row.name == name:
            return row
    raise LinkRefused(404, "no_such_link", f"No linked folder is named “{name}”.")


async def add_link(
    session: AsyncSession,
    project_id: uuid.UUID,
    path: str,
    name: str | None = None,
    confirm_broad: bool = False,
) -> ProjectLinkedFolderTable:
    existing = await list_links(session, project_id)
    folder = check_linkable(path, existing, confirm_broad)
    names = [row.name for row in existing]
    if name is None:
        name = default_name(folder.name, names)
    else:
        check_name(name, names)
    row = ProjectLinkedFolderTable(
        project_id=project_id,
        name=name,
        host_path=str(folder),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(row)
    await session.flush()
    return row


async def rename_link(
    session: AsyncSession, project_id: uuid.UUID, name: str, new_name: str
) -> ProjectLinkedFolderTable:
    row = await _get_link(session, project_id, name)
    if new_name == name:
        return row
    others = [r.name for r in await list_links(session, project_id) if r.id != row.id]
    check_name(new_name, others)
    row.name = new_name
    session.add(row)
    await session.flush()
    return row


async def remove_link(session: AsyncSession, project_id: uuid.UUID, name: str) -> None:
    await session.delete(await _get_link(session, project_id, name))
    await session.flush()
