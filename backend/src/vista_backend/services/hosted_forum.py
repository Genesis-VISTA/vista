"""
A project's forum hosted on the collaboration server: its life cycle, from this
backend's side (openspec/changes/archive/2026-11-05-forum-collab-hosting, design D5).

The server owns the repository and its rules; this module only asks it for a
forum when a project is made, links the forum to the project's item when the
project is published, finds it again when the project is imported, deletes it
when an unpublished project is, and moves a project's existing threads onto one.

Nothing here may make a project fail for want of the server. Creating, linking
and deleting are best effort and say why in a message; only the move, which the
researcher asked for by name, raises, and leaves the project as it was.
"""

import logging
import uuid

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.forum import project_forum
from ..agents.forum.project_forum import (
    ForumSetupError,
    ensure_forum,
    forum_config_for,
    forum_url_of,
    hosted_forum_id,
)
from ..config import settings
from ..db.schemas import LocalSettingsPublic, ProjectTable
from . import local_settings
from .collaboration import (
    CollaborationClient,
    CollaborationError,
    CollaborationRefused,
    RemoteForum,
    client_for,
    server_url,
)
from .forum_git import ForumError
from .git_check import git_status

logger = logging.getLogger(__name__)


def lab_possible() -> bool:
    """This deployment runs labs and this machine has a usable git."""
    return settings.forum.enabled and git_status().ok


async def _delete_quietly(local: LocalSettingsPublic, forum_id: uuid.UUID) -> None:
    """Delete a forum the server holds, never failing: a forum nobody has is only disk."""
    try:
        async with client_for(local) as client:
            await client.delete_forum(forum_id)
    except CollaborationError as e:
        logger.info("hosted forum %s was not deleted: %s", forum_id, e)


async def give_new_project_a_forum(
    session: AsyncSession, project: ProjectTable
) -> str | None:
    """
    Make a hosted forum the forum of a project that has none, when a
    collaboration server is set and answers. Returns why it could not, in words
    for the researcher, or None when it did or there was nothing to do (no
    server, or no labs here). The project is left with no forum on any failure.
    """
    if not lab_possible():
        return None
    local = await local_settings.get_public(session)
    if server_url(local)[0] is None:
        return None
    try:
        async with client_for(local) as client:
            forum = await client.create_forum()
    except CollaborationError as e:
        return f"No forum was made on the collaboration server: {e}"
    project.forum_repo_url = forum.git_url
    session.add(project)
    await session.flush()
    try:
        await ensure_forum(project, await local_settings.get(session))
    except ForumSetupError as e:
        project.forum_repo_url = None
        session.add(project)
        await session.flush()
        await _delete_quietly(local, forum.id)
        return f"The forum made on the collaboration server could not be used: {e}"
    return None


async def link_after_publish(
    local: LocalSettingsPublic, project: ProjectTable, item_id: uuid.UUID
) -> str | None:
    """
    Link the project's hosted forum to its item, so the item's team is the
    forum's. Run after a publish, which stands whatever this answers; returns why
    it could not, or None. A forum already linked to this item (an earlier
    publish, or the publisher's, for an imported project) is not a failure. One
    linked to another item, or gone, is: a project published as a new item
    (into another team, after leaving its own) keeps a forum that only the
    first item's team can read, and the researcher, out of it, sees as gone.
    """
    forum_id = hosted_forum_id(forum_url_of(project), local)
    if forum_id is None:
        return None
    try:
        async with client_for(local) as client:
            try:
                await client.link_forum(forum_id, item_id)
            except CollaborationRefused as e:
                if e.status_code not in (403, 404, 409):
                    raise
                # Linked already, or not ours to link: to this item, or another?
                try:
                    linked = await client.item_forum(item_id)
                except CollaborationRefused as missing:
                    if missing.status_code != 404:
                        raise
                    linked = None
                if linked is not None and linked.id == forum_id:
                    return None
                return (
                    "The project is published, but its Hypothesis Lab forum "
                    "stays with the item it was first published as (or is "
                    "gone), so this item's team cannot read or post to it."
                )
    except CollaborationError as e:
        return f"The project is published, but its forum was not linked to it: {e}"
    return None


async def forum_of_item(
    client: CollaborationClient, item_id: uuid.UUID
) -> tuple[str | None, str | None]:
    """
    (the hosted forum's URL, why not) for a manifest saying `"forum": "hosted"`:
    what the server has linked to the item.
    """
    try:
        forum = await client.item_forum(item_id)
    except CollaborationError as e:
        return None, f"The project's forum could not be found on the server: {e}"
    return forum.git_url, None


async def delete_for_project(local: LocalSettingsPublic, url: str | None) -> None:
    """
    Delete a deleted project's hosted forum. The server refuses (`409`) one that
    was linked to an item, which stays for its team, and anything it will not
    do is not this project's concern.
    """
    forum_id = hosted_forum_id(url, local)
    if forum_id is not None:
        await _delete_quietly(local, forum_id)


async def move_to_hosted(session: AsyncSession, project: ProjectTable) -> ProjectTable:
    """
    Host the project's forum on the collaboration server: create a forum there,
    sync the forum the project has now, push every thread of it with its whole
    history (`vista-import`), and only then make the new one the project's. A
    forge repository it used before is not touched. Any failure leaves the
    project's URL as it was and deletes the new, then empty, forum.
    """
    local_row = await local_settings.get(session)
    local = local_settings.to_public(local_row)
    old_url = forum_url_of(project)
    if hosted_forum_id(old_url, local) is not None:
        raise HTTPException(
            status_code=409, detail="This project's forum is hosted there already."
        )
    if server_url(local)[0] is None:
        raise HTTPException(
            status_code=400,
            detail="No collaboration server is set. Add its address under Settings "
            "→ Collaboration first.",
        )
    git = git_status()
    if not settings.forum.enabled or not git.ok:
        raise HTTPException(
            status_code=503,
            detail=f"The Hypothesis Lab needs git. {git.reason or ''}".strip(),
        )

    try:
        if old_url is not None:
            # What the old forum holds, here, and what waits to be published, there.
            await ensure_forum(project, local_row)
        async with client_for(local) as client:
            forum: RemoteForum = await client.create_forum()
    except (ForumSetupError, CollaborationError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"The forum was not moved; the project still uses {old_url or 'none'}: {e}",
        )

    before = project.modified
    try:
        if old_url is not None:
            target = ProjectTable(
                id=project.id,
                name=project.name,
                forum_repo_url=forum.git_url,
                skills=[],
                knowledge_bases=[],
                tools=[],
                usage_limits={},
            )
            config = forum_config_for(target, local_row)
            assert config is not None
            mover = project_forum.client_factory(config)
            await mover.ensure_repo()
            await mover.push_threads_for_import()
        project.forum_repo_url = forum.git_url
        project.modified = True
        session.add(project)
        await session.flush()
        await ensure_forum(project, local_row)
    except (ForumSetupError, ForumError, OSError) as e:
        project.forum_repo_url = old_url
        project.modified = before
        session.add(project)
        await session.flush()
        await _restore_remote(project, local_row, old_url)
        await _delete_quietly(local, forum.id)
        raise HTTPException(
            status_code=400,
            detail=f"The forum was not moved; the project still uses {old_url or 'none'}: {e}",
        )
    return project


async def _restore_remote(
    project: ProjectTable, local_row, old_url: str | None
) -> None:
    """Point the project's repository back at the forum it still uses, if it can."""
    if old_url is None:
        return
    try:
        config = forum_config_for(project, local_row)
        if config is not None:
            await project_forum.client_factory(config).set_remote(old_url)
    except (ForumError, OSError) as e:
        logger.info("could not point the forum back at %s: %s", old_url, e)
