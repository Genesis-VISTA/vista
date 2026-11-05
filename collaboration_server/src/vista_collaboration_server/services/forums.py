"""
Hosted forums: a git repository per forum, and the rows that say whose it is.

A forum is owned by whoever created it and is theirs alone until it is linked,
once, to a `project` item; from then on it belongs to that item's team, checked
by `teams.visible_teams` in the query, as for items. Anyone else, and anyone
asking for a forum that is not there, gets `NotFound`.

The repository is `data_dir/forums/<id>.git`, bare, with the rules git itself can
enforce switched on, and the hooks (`forum_hook.py`) that enforce the rest.
"""

import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from shlex import quote

from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import and_, col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ForumPushTable, ForumTable, ItemTable
from .errors import Conflict, NotFound
from .identity import User
from .teams import visible_teams

MIN_GIT = (2, 34)

HOOK_MODULE = "vista_collaboration_server.services.forum_hook"


MACOS_GIT_SHIM = Path("/usr/bin/git")
""" The macOS stub that offers to install the developer tools. A module global so tests can stand one in. """


def _is_macos_shim(path: str) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        return Path(path).resolve() == MACOS_GIT_SHIM.resolve()
    except OSError:
        return False


def _developer_tools_installed() -> bool:
    """`xcode-select -p` exits 0 only when the developer tools are installed."""
    xcode_select = shutil.which("xcode-select")
    if xcode_select is None:
        return False
    try:
        proc = subprocess.run([xcode_select, "-p"], capture_output=True, timeout=10)
    except OSError, subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def git_version() -> tuple[int, int] | None:
    """
    The system git's (major, minor), or None when there is no git to run.

    On macOS `/usr/bin/git` is a stub that pops an install dialog when run
    unless the developer tools are installed (the backend's `git_check.py`,
    copied): it is asked about with `xcode-select -p` first, and never run
    when that fails.
    """
    git = shutil.which("git")
    if git is None:
        return None
    if _is_macos_shim(git) and not _developer_tools_installed():
        return None
    try:
        out = subprocess.run(
            [git, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=True,
        ).stdout
    except OSError, subprocess.SubprocessError:
        return None
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def git_usable() -> bool:
    version = git_version()
    return version is not None and version >= MIN_GIT


def parse_forum_id(value: str) -> uuid.UUID:
    """
    The forum id in a path, as a UUID in its canonical spelling: anything else
    is `NotFound`, before any path is built from it.
    """
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise NotFound("No such forum.")
    if str(parsed) != value:
        raise NotFound("No such forum.")
    return parsed


def repo_path(forums_dir: Path, forum_id: uuid.UUID) -> Path:
    return forums_dir / f"{forum_id}.git"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


async def create_forum(session: AsyncSession, user: User) -> ForumTable:
    forum = ForumTable(owner_sub=user.sub)
    session.add(forum)
    await session.commit()
    await session.refresh(forum)
    return forum


async def get_forum(
    session: AsyncSession, forum_id: uuid.UUID, user: User
) -> ForumTable:
    """
    The forum, if `user` may use it: its owner while it is not linked, a member
    of its item's team once it is. `NotFound` otherwise, whether or not it exists.
    """
    query = (
        select(ForumTable)
        .join(ItemTable, col(ItemTable.id) == col(ForumTable.item_id), isouter=True)
        .where(
            ForumTable.id == forum_id,
            or_(
                and_(
                    col(ForumTable.item_id).is_(None),
                    ForumTable.owner_sub == user.sub,
                ),
                and_(
                    col(ForumTable.item_id).is_not(None),
                    visible_teams(col(ItemTable.team_id), user),
                ),
            ),
        )
    )
    row = (await session.exec(query)).first()
    if row is None:
        raise NotFound("No such forum.")
    return row


async def forum_of_item(session: AsyncSession, item_id: uuid.UUID) -> ForumTable | None:
    """The forum linked to an item, with no access check: the caller has seen the item."""
    query = select(ForumTable).where(ForumTable.item_id == item_id)
    return (await session.exec(query)).first()


async def link(
    session: AsyncSession, forum_id: uuid.UUID, item_id: uuid.UUID, user: User
) -> ForumTable:
    """
    Link the forum to a `project` item `user` can see, once. Owner only: `403`
    for anyone else who can see the forum. A forum already linked, or an item
    that already has a forum, is `Conflict`.
    """
    from .items import get_item

    forum = await get_forum(session, forum_id, user)
    if forum.owner_sub != user.sub:
        raise HTTPException(
            status_code=403, detail="Only the forum's owner can do this."
        )
    item = await get_item(session, item_id, user)
    if item.kind != "project":
        raise Conflict("A forum can be linked only to a project.")
    try:
        result = await session.exec(
            update(ForumTable)
            .where(
                col(ForumTable.id) == forum_id,
                col(ForumTable.owner_sub) == user.sub,
                col(ForumTable.item_id).is_(None),
            )
            .values(item_id=item_id)
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict("That project already has a forum.")
    if result.rowcount != 1:
        raise Conflict("This forum is already linked to a project.")
    session.expire_all()
    return await get_forum(session, forum_id, user)


async def delete_forum(session: AsyncSession, forum_id: uuid.UUID, user: User) -> None:
    """Delete an unlinked forum's rows. Owner only; a linked forum is `Conflict`."""
    forum = await get_forum(session, forum_id, user)
    if forum.owner_sub != user.sub:
        raise HTTPException(
            status_code=403, detail="Only the forum's owner can do this."
        )
    if forum.item_id is not None:
        raise Conflict("This forum belongs to a published project and stays.")
    result = await session.exec(
        delete(ForumTable).where(
            col(ForumTable.id) == forum_id, col(ForumTable.item_id).is_(None)
        )
    )
    await session.exec(
        delete(ForumPushTable).where(col(ForumPushTable.forum_id) == forum_id)
    )
    await session.commit()
    if result.rowcount != 1:
        raise Conflict("This forum belongs to a published project and stays.")


async def copy_forum_row(
    session: AsyncSession,
    source_item_id: uuid.UUID,
    copy_item_id: uuid.UUID,
    user: User,
) -> ForumTable | None:
    """
    When an item has a forum, a new, empty one linked to its copy and owned by
    the copier, added to `session` (the caller commits). The original's forum
    is not touched, and the copy's team cannot read it.
    """
    if await forum_of_item(session, source_item_id) is None:
        return None
    forum = ForumTable(owner_sub=user.sub, item_id=copy_item_id)
    session.add(forum)
    return forum


async def pushes(
    session: AsyncSession, forum_id: uuid.UUID, after: int, limit: int
) -> list[ForumPushTable]:
    query = (
        select(ForumPushTable)
        .where(ForumPushTable.forum_id == forum_id, col(ForumPushTable.seq) > after)
        .order_by(col(ForumPushTable.seq))
        .limit(limit)
    )
    return list((await session.exec(query)).all())


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def create_repo(forums_dir: Path, forum_id: uuid.UUID) -> Path:
    """
    The forum's bare repository, with its settings and hooks, if it is not
    there. Blocking: run it off the event loop.
    """
    repo = repo_path(forums_dir, forum_id)
    if (repo / "HEAD").is_file():
        return repo
    forums_dir.mkdir(parents=True, exist_ok=True)
    tmp = forums_dir / f".{forum_id}.git.new"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        subprocess.run(
            ["git", "init", "--bare", "--quiet", str(tmp)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        for key, value in (
            ("receive.denyNonFastForwards", "true"),
            ("receive.denyDeletes", "true"),
            ("receive.advertisePushOptions", "true"),
            ("http.receivepack", "true"),
        ):
            _git(tmp, "config", key, value)
        install_hooks(tmp)
        tmp.rename(repo)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return repo


def install_hooks(repo: Path) -> None:
    """
    `pre-receive` and `post-receive` that run this package's hook entry point
    with the interpreter running the server, by absolute path: a hook runs with
    git's environment, which need not find the right Python.
    """
    hooks = repo / "hooks"
    hooks.mkdir(exist_ok=True)
    for stage, name in (("pre", "pre-receive"), ("post", "post-receive")):
        path = hooks / name
        path.write_text(
            f"#!/bin/sh\nexec {quote(sys.executable)} -m {HOOK_MODULE} {stage}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)


def delete_repo(forums_dir: Path, forum_id: uuid.UUID) -> None:
    shutil.rmtree(repo_path(forums_dir, forum_id), ignore_errors=True)
