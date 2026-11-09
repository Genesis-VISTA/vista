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

A private project here may use it in the team as well, so the row joins the
team even while private projects use it. A project of another team would
lose it, since a team's project uses only its own team's skills and knowledge
bases (only one in a team the researcher has left can be using a private
one); so one such a project uses is left as it was, and the team's item is
imported beside it under a free name instead. So is a skill bundled with
VISTA, which stays private.

A project is published with everything it uses: each of its skills and
knowledge bases first, then the project itself, published into the team as a
new item naming them. A private project may use another team's; it can then
be published only into that team.
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
    kept_for: list[str] = []
    """
    `imported` only: the projects here, of another team, that the row stayed as
    it was for; empty for a skill that comes with VISTA, which always stays.
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


async def _other_teams_users(
    session: AsyncSession,
    kind: _Kind,
    key: str,
    url: str | None,
    team_id: uuid.UUID,
    *,
    besides: set[uuid.UUID],
) -> list[str]:
    """
    The projects here, other than those in `besides`, that use `key` and are in
    a team other than `team_id`: those `key` joining `team_id` would break.
    A private project may use it there.
    """
    projects = (await session.exec(select(ProjectTable))).all()
    return sorted(
        p.name
        for p in projects
        if p.id not in besides
        and key in kind.uses(p)
        and team_of(p, url) not in (None, team_id)
    )


async def _place(
    session: AsyncSession,
    local: LocalSettingsPublic,
    kind: _Kind,
    key: str,
    team_id: uuid.UUID,
    *,
    kept_for: list[str] | None,
) -> PublishedResource:
    """
    Publish private skill or knowledge base `key` into `team_id`, as a new
    item. With `kept_for` (`_kept_for`), the row here stays as it was and the
    team's item is imported beside it; with None, the row joins the team.
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
    if kept_for is None:
        return PublishedResource(
            kind=kind.kind, name=key, local_name=key, how="published"
        )
    await reread(session, row)
    item_id = row.remote_id
    assert item_id is not None
    await restore()
    name = await kind.import_as(session, local, item_id, await kind.free(session, key))
    return PublishedResource(
        kind=kind.kind, name=key, local_name=name, how="imported", kept_for=kept_for
    )


async def _kept_for(
    session: AsyncSession,
    kind: _Kind,
    key: str,
    url: str | None,
    team_id: uuid.UUID,
    *,
    besides: set[uuid.UUID],
) -> list[str] | None:
    """
    Whether `key` must stay as it is, the team's item imported beside it: the
    projects of another team it stays for, [] for a bundled skill, or None
    when it can join `team_id`.
    """
    row = await kind.get(session, key)
    if _stays(row):
        return []
    users = await _other_teams_users(session, kind, key, url, team_id, besides=besides)
    return users or None


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
    kept_for = await _kept_for(session, kind, key, url, team_id, besides=set())
    published = await _place(session, local, kind, key, team_id, kept_for=kept_for)
    return TeamPublishResult(team_id=team_id, published=[published])


@remembers_teams
async def publish_skill_to_team(
    session: AsyncSession, local: LocalSettingsPublic, name: str, team_id: uuid.UUID
) -> TeamPublishResult:
    """Publish private skill `name` into `team_id`; imported beside it when it is bundled, or a project of another team here uses it."""
    return await _publish_one(session, local, SKILL, name, team_id)


@remembers_teams
async def publish_knowledge_base_to_team(
    session: AsyncSession, local: LocalSettingsPublic, slug: str, team_id: uuid.UUID
) -> TeamPublishResult:
    """Publish private knowledge base `slug` into `team_id`; imported beside it when a project of another team here uses it."""
    return await _publish_one(session, local, KNOWLEDGE_BASE, slug, team_id)


@remembers_teams
async def publish_project_to_team(
    session: AsyncSession, local: LocalSettingsPublic, name: str, team_id: uuid.UUID
) -> TeamPublishResult:
    """
    Publish private project `name` and everything it uses into `team_id`, the
    project last. Everything it uses must be here, and private, in a team the
    researcher has left, or in `team_id` already, checked before anything is
    sent; one in another of their teams is refused. A skill or knowledge base
    a project of another team here also uses (one they have left), or a
    bundled skill, stays as it is for it: the team's item is imported beside
    it, and this project switched to that.

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
                continue  # in it already, or moved there by a publish that stopped part way
            if team is not None:
                raise HTTPException(
                    status_code=400,
                    detail=f"{name} uses the {kind.label} {key}, which is in "
                    "another team: a team's project uses only its own team's "
                    f"skills and knowledge bases. Publish {name} to that team, "
                    f"or remove {key} from it first.",
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
            kept_for = await _kept_for(
                session, kind, key, url, team_id, besides={project_id}
            )
            was = _link(await kind.get(session, key))
            result = await _place(session, local, kind, key, team_id, kept_for=kept_for)
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
