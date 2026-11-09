"""
Publish to a team: how a private project, skill or knowledge base is first
published, into a team the researcher chooses.

A private one (on this computer only) is published into the team as a new
item, and the row joins that team: from then on it is in that team alone, and
Publish sends its new versions there (`skill_collaboration`,
`project_collaboration`, `knowledge_base_collaboration`). One already in a
team is refused: an item stays in the team it was first published to. One
in a team the researcher has left counts as private here, since it can no
longer be published there.

The row becoming the team's would break a project here that uses it and stays
private, since a project uses only its own team's skills and knowledge bases;
so one used by another project is left as it was, and the team's item is
imported beside it under a free name instead. So is a skill bundled with
VISTA, which stays private.

A project is published with everything it uses: each of its skills and
knowledge bases first, then the project itself, published into the team as a
new item naming them.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    KnowledgeBaseTable,
    LocalSettingsPublic,
    ProjectTable,
    SkillTable,
)
from .collaboration import client_for, required_url
from .knowledge_base import get_kb
from .project import get_project_by_name
from .project_agent import invalidate_agents
from .skill_collaboration import reread
from .skills import get_skill
from .teams import (
    Teamed,
    remembers_teams,
    require_in_team,
    set_team,
    team_of,
    usable_in,
)

logger = logging.getLogger(__name__)

Kind = Literal["project", "skill", "knowledge-base"]
How = Literal["published", "imported"]


class PublishedResource(BaseModel):
    kind: Kind
    name: str
    """ Its name (a knowledge base's slug) here before the publish. """
    local_name: str
    """
    The name the team's item has here: the same, the row having joined the
    team; or a new one, the item imported beside a row that stayed private.
    """
    how: How
    """
    `published`: the row joined the team; `imported`: the row stayed private,
    and the team's item was imported beside it under `local_name`.
    """


class TeamPublishResult(BaseModel):
    team_id: uuid.UUID
    published: list[PublishedResource]
    """ What was published, the project last. """
    forum_error: str | None = None
    """ A project's: why its hosted forum was not linked to it; the publish stands. """


@dataclass(frozen=True)
class _Kind:
    kind: Kind
    label: str
    key: Callable[[SkillTable | KnowledgeBaseTable], str]
    uses: Callable[[ProjectTable], list[str]]
    get: Callable[[AsyncSession, str], Awaitable[SkillTable | KnowledgeBaseTable]]
    publish: Callable[[AsyncSession, LocalSettingsPublic, str], Awaitable[None]]
    import_as: Callable[
        [AsyncSession, LocalSettingsPublic, uuid.UUID, str], Awaitable[str]
    ]
    free: Callable[[AsyncSession, str], Awaitable[str]]
    delete: Callable[[AsyncSession, str], Awaitable[None]]
    """ Remove one here, its files too: a copy imported beside an original, on rollback. """


async def _publish_skill(session, local, name) -> None:
    from .skill_collaboration import publish_skill

    await publish_skill(session, local, name, as_new=True)


async def _import_skill(session, local, item_id, name) -> str:
    from .skill_collaboration import import_skill

    row, _ = await import_skill(session, local, item_id, rename_to=name)
    return row.name


async def _publish_kb(session, local, slug) -> None:
    from .knowledge_base_collaboration import publish_knowledge_base

    await publish_knowledge_base(session, local, slug, as_new=True)


async def _import_kb(session, local, item_id, slug) -> str:
    from .knowledge_base_collaboration import import_knowledge_base

    result = await import_knowledge_base(session, local, item_id, rename_to=slug)
    return result.knowledge_base.slug


async def _free_skill(session, base) -> str:
    from .project_collaboration import _free_skill_name

    return await _free_skill_name(session, base)


async def _free_kb(session, base) -> str:
    from .project_collaboration import _free_slug

    return await _free_slug(session, base)


async def _delete_skill(session, name) -> None:
    from .skills import delete_skill

    await delete_skill(session, name)


async def _delete_kb(session, slug) -> None:
    # The route's deletion: the row, its PDFs and its store, under its lock.
    from ..api.knowledge_bases import delete_knowledge_base

    await delete_knowledge_base(slug, session)


SKILL = _Kind(
    kind="skill",
    label="skill",
    key=lambda row: row.name,  # type: ignore[union-attr]
    uses=lambda project: list(project.skills),
    get=get_skill,
    publish=_publish_skill,
    import_as=_import_skill,
    free=_free_skill,
    delete=_delete_skill,
)
KNOWLEDGE_BASE = _Kind(
    kind="knowledge-base",
    label="knowledge base",
    key=lambda row: row.slug,  # type: ignore[union-attr]
    uses=lambda project: list(project.knowledge_bases),
    get=get_kb,
    publish=_publish_kb,
    import_as=_import_kb,
    free=_free_kb,
    delete=_delete_kb,
)


async def _my_teams(
    local: LocalSettingsPublic, team_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    """
    The teams the researcher is in; refuses `team_id` when it is not one of
    them (`teams.NotInTeam`), before anything is sent.
    """
    async with client_for(local) as client:
        return await require_in_team(
            client, team_id, f"You are not in that team on {client.base_url}."
        )


def _team_here(
    row: Teamed, url: str | None, mine: frozenset[uuid.UUID]
) -> uuid.UUID | None:
    """
    `row`'s team, for publishing: None, as if private, when it is a team the
    researcher has left, where it can no longer be published.
    """
    team = team_of(row, url)
    return team if team in mine else None


def _require_private(
    label: str, row: Teamed, url: str | None, mine: frozenset[uuid.UUID]
) -> None:
    """
    Refuse (400) to publish `row` into a team when it is in one already: an
    item stays in the team it was first published to.
    """
    if _team_here(row, url, mine) is not None:
        raise HTTPException(
            status_code=400,
            detail=f"{label} is in a team already: Publish sends its new "
            "versions there. An item stays in the team it was first published to.",
        )


def _link(row: SkillTable | KnowledgeBaseTable) -> tuple:
    """Where `row` stands with the server and its team, to put back with `_restore_link`."""
    return (
        row.origin_server,
        row.remote_id,
        row.remote_version,
        row.team_id,
        row.team_server,
        row.modified,
    )


def _restore_link(row: SkillTable | KnowledgeBaseTable, was: tuple) -> None:
    (
        row.origin_server,
        row.remote_id,
        row.remote_version,
        row.team_id,
        row.team_server,
        row.modified,
    ) = was


def _stays(row: SkillTable | KnowledgeBaseTable) -> bool:
    """
    A skill bundled with VISTA stays private: it cannot be deleted or
    re-imported, so the team's item is imported beside it instead.
    """
    return isinstance(row, SkillTable) and row.bundled


async def _users(
    session: AsyncSession, kind: _Kind, key: str, *, besides: set[uuid.UUID]
) -> list[str]:
    """The projects here that use `key`, other than those in `besides`."""
    projects = (await session.exec(select(ProjectTable))).all()
    return sorted(
        p.name for p in projects if p.id not in besides and key in kind.uses(p)
    )


async def _place(
    session: AsyncSession,
    local: LocalSettingsPublic,
    kind: _Kind,
    key: str,
    team_id: uuid.UUID,
    *,
    keep: bool,
) -> PublishedResource:
    """
    Publish private skill or knowledge base `key` into `team_id`, as a new
    item. With `keep`, the row here stays private and the team's item is
    imported beside it; otherwise the row joins the team.
    """
    url = required_url(local)
    row = await kind.get(session, key)
    was = _link(row)

    async def restore() -> None:
        await reread(session, row)
        _restore_link(row, was)
        session.add(row)
        await session.commit()

    # In the team before publishing, which publishes to the row's own team.
    set_team(row, team_id, url)
    session.add(row)
    await session.commit()
    try:
        await kind.publish(session, local, key)
    except BaseException:
        await restore()
        raise
    if not keep:
        return PublishedResource(
            kind=kind.kind, name=key, local_name=key, how="published"
        )
    await reread(session, row)
    item_id = row.remote_id
    assert item_id is not None
    await restore()
    name = await kind.import_as(session, local, item_id, await kind.free(session, key))
    return PublishedResource(kind=kind.kind, name=key, local_name=name, how="imported")


async def _publish_one(
    session: AsyncSession,
    local: LocalSettingsPublic,
    kind: _Kind,
    key: str,
    team_id: uuid.UUID,
) -> TeamPublishResult:
    row = await kind.get(session, key)
    url = required_url(local)
    mine = await _my_teams(local, team_id)
    _require_private(f"The {kind.label} {key}", row, url, mine)
    keep = _stays(row) or bool(await _users(session, kind, key, besides=set()))
    published = await _place(session, local, kind, key, team_id, keep=keep)
    return TeamPublishResult(team_id=team_id, published=[published])


@remembers_teams
async def publish_skill_to_team(
    session: AsyncSession, local: LocalSettingsPublic, name: str, team_id: uuid.UUID
) -> TeamPublishResult:
    """Publish private skill `name` into `team_id`; imported beside it when a project here uses it."""
    return await _publish_one(session, local, SKILL, name, team_id)


@remembers_teams
async def publish_knowledge_base_to_team(
    session: AsyncSession, local: LocalSettingsPublic, slug: str, team_id: uuid.UUID
) -> TeamPublishResult:
    """Publish private knowledge base `slug` into `team_id`; imported beside it when a project here uses it."""
    return await _publish_one(session, local, KNOWLEDGE_BASE, slug, team_id)


@remembers_teams
async def publish_project_to_team(
    session: AsyncSession, local: LocalSettingsPublic, name: str, team_id: uuid.UUID
) -> TeamPublishResult:
    """
    Publish private project `name` and everything it uses into `team_id`, the
    project last. Everything it uses must be here and private, or in a team
    the researcher has left (or in `team_id` already, from a publish that
    stopped part way), checked before
    anything is sent. A skill or knowledge base another project here also uses
    stays private for it: the team's item is imported beside it, and this
    project switched to that.

    If publishing what it uses fails, what joined `team_id` here is made
    private again, and what was imported beside an original is removed (their
    items stay on the server, unreferenced), so the project is as it was. If
    the project's own publish fails after that, it is left in the team with
    what it uses; publishing it into `team_id` again (or plain Publish)
    finishes it there.
    """
    from .knowledge_base_collaboration import require_idle
    from .project_collaboration import publish_project

    project = await get_project_by_name(session, name)
    url = required_url(local)
    # Read now: every publish below commits, expiring the row.
    project_id = project.id
    mine = await _my_teams(local, team_id)
    # Left in `team_id`, never published there, by a publish whose last step
    # failed: this finishes it, everything it uses being there already.
    resuming = _team_here(project, url, mine) == team_id and project.remote_id is None
    if not resuming:
        _require_private(f"The project {name}", project, url, mine)
    plan: list[tuple[_Kind, str]] = []
    for kind in (SKILL, KNOWLEDGE_BASE):
        for key in dict.fromkeys(kind.uses(project)):
            try:
                row = await kind.get(session, key)
            except HTTPException:
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} uses the {kind.label} {key}, which is not on "
                    "this computer. Remove it from the project before publishing it.",
                )
            team = _team_here(row, url, mine)
            if team == team_id:
                continue  # moved there by a publish that stopped part way
            if not usable_in(team, None):
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} uses the {kind.label} {key}, which is in "
                    "a team. Remove it from the project before publishing it.",
                )
            if isinstance(row, KnowledgeBaseTable):
                require_idle(row, f"publish {name}")
            plan.append((kind, key))

    published: list[PublishedResource] = []
    # What each row that joined the team was before, to put back if a later one
    # fails; and each copy imported beside an original, to remove then.
    moved: list[tuple[_Kind, str, tuple]] = []
    imported: list[tuple[_Kind, str]] = []
    try:
        for kind, key in plan:
            row = await kind.get(session, key)
            keep = _stays(row) or bool(
                await _users(session, kind, key, besides={project_id})
            )
            was = _link(row)
            result = await _place(session, local, kind, key, team_id, keep=keep)
            published.append(result)
            if result.how == "imported":
                imported.append((kind, result.local_name))
            else:
                moved.append((kind, key, was))
    except BaseException:
        for kind, key, was in moved:
            row = await kind.get(session, key)
            _restore_link(row, was)
            session.add(row)
        await session.commit()
        # Nothing uses them yet, and a retry would import them again.
        for kind, local_name in imported:
            try:
                await kind.delete(session, local_name)
            except Exception:  # noqa: BLE001 - the publish's own error is the one to raise
                logger.warning(
                    "Could not remove %s %s, imported for a publish that failed",
                    kind.label,
                    local_name,
                    exc_info=True,
                )
        raise

    renamed = {(c.kind, c.name): c.local_name for c in published}
    project = await get_project_by_name(session, name)
    project.skills = [renamed.get(("skill", s), s) for s in project.skills]
    project.knowledge_bases = [
        renamed.get(("knowledge-base", k), k) for k in project.knowledge_bases
    ]
    # A new item in the team, never a version of one it had on another server
    # (a team counts only with its server). Its fields may have changed too
    # (the imported names).
    set_team(project, team_id, url)
    project.origin_server = project.remote_id = project.remote_version = None
    project.modified = True
    session.add(project)
    invalidate_agents(session, project_id=project.id)
    await session.commit()
    done = await publish_project(session, local, name, as_new=True)
    published.append(
        PublishedResource(kind="project", name=name, local_name=name, how="published")
    )
    return TeamPublishResult(
        team_id=team_id, published=published, forum_error=done.forum_error
    )
