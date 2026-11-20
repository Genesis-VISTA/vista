"""
Folders on this computer linked to a project, mounted read-only into its sandboxes
(openspec change linked-folders).
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, Sequence

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


def resolve_linked_volumes(links: Sequence[LinkedFolder]) -> LinkedVolumes:
    """
    Turn a project's links into read-only volumes for the ones a sandbox can mount now, and
    the reason for each one it can't.

    Each stored path is resolved again: it was stored resolved, but a folder on the way may
    since have become a symlink, and microsandbox fails the whole start, not just the mount,
    on a host path through a symlink.
    """
    out = LinkedVolumes()
    for link in links:
        try:
            host = Path(link.host_path).resolve()
        except OSError, RuntimeError:  # a symlink loop, or a path the OS refuses
            out.unavailable.append((link.name, "unreadable"))
            continue
        if not host.exists():
            out.unavailable.append((link.name, "missing"))
        elif not host.is_dir():
            out.unavailable.append((link.name, "not a folder"))
        elif not os.access(host, os.R_OK | os.X_OK):
            out.unavailable.append((link.name, "unreadable"))
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
