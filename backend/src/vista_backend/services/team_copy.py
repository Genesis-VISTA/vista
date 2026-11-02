"""
Copy to team: how a project, skill or knowledge base reaches a team.

A private one (on this computer only) is published into the team as a new
item. One already in a team on this server, unchanged since its version and
at the server's newest, is copied there by the server, which shares its blob,
so nothing is uploaded; one changed here is published as a new item, so the
copy holds the change. Either way the original item stays as it is, for the
team it is in: copying never moves anything on the server.

Here, the copy is what the row is from then on: it is re-pointed at the new
item and joins the team, so a page showing that team lists it. That would
break a project that uses it and stays behind, since a project uses only its
own team's skills and knowledge bases; so one used by a project that is not
being copied is left as it was, and the copy is imported beside it under a
free name instead.

A project is copied with everything it uses: each of its skills and knowledge
bases first, then the project itself, published into the team as a new item
naming the copies.
"""

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
from .collaboration import CollaborationClient, client_for, required_url
from .knowledge_base import get_kb
from .project import get_project_by_name
from .project_agent import invalidate_agents
from .skill_collaboration import reread
from .skills import get_skill
from .teams import usable_in

Kind = Literal["project", "skill", "knowledge-base"]
How = Literal["copied", "published", "imported"]


class CopiedResource(BaseModel):
    kind: Kind
    name: str
    """ Its name (a knowledge base's slug) here before the copy. """
    local_name: str
    """
    The name the team's copy has here: the same, the row having become the
    copy; or a new one, the copy imported beside a row that stayed behind.
    """
    how: How
    """
    `copied` by the server, sharing its blob; `published` from this computer,
    as a new item; `imported` beside the original, under `local_name`.
    """


class TeamCopyResult(BaseModel):
    team_id: uuid.UUID
    copied: list[CopiedResource]
    """ What was copied, the project last. """


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


SKILL = _Kind(
    kind="skill",
    label="skill",
    key=lambda row: row.name,  # type: ignore[union-attr]
    uses=lambda project: list(project.skills),
    get=get_skill,
    publish=_publish_skill,
    import_as=_import_skill,
    free=_free_skill,
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
)


async def _require_member(client: CollaborationClient, team_id: uuid.UUID) -> None:
    """Refuse a team the researcher is not in, before anything is sent."""
    me = await client.me()
    if team_id not in {t.id for t in me.teams}:
        raise HTTPException(
            status_code=404,
            detail=f"You are not in that team on {client.base_url}.",
        )


def _require_elsewhere(label: str, current: uuid.UUID | None, team_id: uuid.UUID):
    if current == team_id:
        raise HTTPException(status_code=400, detail=f"{label} is in that team already.")


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
    client: CollaborationClient,
    kind: _Kind,
    key: str,
    team_id: uuid.UUID,
    *,
    keep: bool,
) -> CopiedResource:
    """
    Put a copy of skill or knowledge base `key` in `team_id`. With `keep`, the
    row here stays as it is and the copy is imported beside it; otherwise the
    row becomes the copy.
    """
    url = required_url(local)
    row = await kind.get(session, key)
    current = (
        row.origin_server == url
        and row.remote_id is not None
        and row.team_id is not None
        and not row.modified
    )
    if current:
        assert row.remote_id is not None
        current = (
            await client.get_item(row.remote_id)
        ).head_version == row.remote_version
    if current:
        assert row.remote_id is not None
        copy = await client.copy_item(row.remote_id, team_id)
        if keep:
            name = await kind.import_as(
                session, local, copy.id, await kind.free(session, key)
            )
            return CopiedResource(
                kind=kind.kind, name=key, local_name=name, how="imported"
            )
        row.remote_id, row.remote_version, row.team_id = copy.id, 1, team_id
        session.add(row)
        await session.commit()
        return CopiedResource(kind=kind.kind, name=key, local_name=key, how="copied")

    # Published from here, as a new item in the team.
    was = (
        row.origin_server,
        row.remote_id,
        row.remote_version,
        row.team_id,
        row.modified,
    )

    async def restore() -> None:
        await reread(session, row)
        (
            row.origin_server,
            row.remote_id,
            row.remote_version,
            row.team_id,
            row.modified,
        ) = was
        session.add(row)
        await session.commit()

    row.team_id = team_id
    session.add(row)
    await session.commit()
    try:
        await kind.publish(session, local, key)
    except BaseException:
        await restore()
        raise
    if not keep:
        return CopiedResource(kind=kind.kind, name=key, local_name=key, how="published")
    await reread(session, row)
    item_id = row.remote_id
    assert item_id is not None
    await restore()
    name = await kind.import_as(session, local, item_id, await kind.free(session, key))
    return CopiedResource(kind=kind.kind, name=key, local_name=name, how="imported")


async def _copy_one(
    session: AsyncSession,
    local: LocalSettingsPublic,
    kind: _Kind,
    key: str,
    team_id: uuid.UUID,
) -> TeamCopyResult:
    row = await kind.get(session, key)
    _require_elsewhere(f"The {kind.label} {key}", row.team_id, team_id)
    keep = bool(await _users(session, kind, key, besides=set()))
    async with client_for(local) as client:
        await _require_member(client, team_id)
        copied = await _place(session, local, client, kind, key, team_id, keep=keep)
    return TeamCopyResult(team_id=team_id, copied=[copied])


async def copy_skill(
    session: AsyncSession, local: LocalSettingsPublic, name: str, team_id: uuid.UUID
) -> TeamCopyResult:
    """Copy skill `name` to `team_id`; imported beside it when a project here uses it."""
    return await _copy_one(session, local, SKILL, name, team_id)


async def copy_knowledge_base(
    session: AsyncSession, local: LocalSettingsPublic, slug: str, team_id: uuid.UUID
) -> TeamCopyResult:
    """Copy knowledge base `slug` to `team_id`; imported beside it when a project here uses it."""
    return await _copy_one(session, local, KNOWLEDGE_BASE, slug, team_id)


async def copy_project(
    session: AsyncSession, local: LocalSettingsPublic, name: str, team_id: uuid.UUID
) -> TeamCopyResult:
    """
    Copy project `name` and everything it uses to `team_id`, the project last.
    Everything it uses must be here and in its team, checked before anything
    is sent. A skill or knowledge base another project here also uses is
    imported beside the original, and the project switched to the copy. If
    the project's own publish then fails, it is left in the team with its
    copies, to publish again.
    """
    from .knowledge_base_collaboration import require_idle
    from .project_collaboration import publish_project

    project = await get_project_by_name(session, name)
    _require_elsewhere(f"The project {name}", project.team_id, team_id)
    # Read now: every copy below commits, expiring the row.
    project_id, project_team = project.id, project.team_id
    plan: list[tuple[_Kind, str]] = []
    for kind in (SKILL, KNOWLEDGE_BASE):
        for key in dict.fromkeys(kind.uses(project)):
            try:
                row = await kind.get(session, key)
            except HTTPException:
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} uses the {kind.label} {key}, which is not on "
                    "this computer. Remove it from the project before copying it.",
                )
            if not usable_in(row.team_id, project_team):
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} uses the {kind.label} {key}, which is in "
                    "another team. Remove it from the project before copying it.",
                )
            if isinstance(row, KnowledgeBaseTable):
                require_idle(row, f"copy {name}")
            plan.append((kind, key))

    copied: list[CopiedResource] = []
    async with client_for(local) as client:
        await _require_member(client, team_id)
        for kind, key in plan:
            keep = bool(await _users(session, kind, key, besides={project_id}))
            copied.append(
                await _place(session, local, client, kind, key, team_id, keep=keep)
            )

    renamed = {(c.kind, c.name): c.local_name for c in copied}
    project = await get_project_by_name(session, name)
    project.skills = [renamed.get(("skill", s), s) for s in project.skills]
    project.knowledge_bases = [
        renamed.get(("knowledge-base", k), k) for k in project.knowledge_bases
    ]
    project.team_id = team_id
    session.add(project)
    invalidate_agents(session, project_id=project.id)
    await session.commit()
    await publish_project(session, local, name, as_new=True)
    copied.append(
        CopiedResource(kind="project", name=name, local_name=name, how="published")
    )
    return TeamCopyResult(team_id=team_id, copied=copied)
