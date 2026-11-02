"""
Teams, from this installation's side: which team a new project, skill or
knowledge base joins, what a project may use, and what may be published.

Every project, skill and knowledge base here is either private (on this
computer only, as everything made with Private selected is, and everything
VISTA seeds or bundles) or in one team on a collaboration server. A team
means something only on its own server, so a row records both (`team_id`,
`team_server`), and is in its team only while that server is the one in
effect (`team_of`): on another, it is private, and it is in its team again
when its server is. Nothing is erased by changing servers.

A project uses only skills and knowledge bases of its own team, so that
whoever can see the project can see everything it uses: checked whenever it
gains one, not only when it is published. A private one is not published; it
is copied to a team (`team_copy`).

The teams themselves, and who is in them, are the collaboration server's
(`collaboration.CollaborationClient`).
"""

import uuid
from typing import Protocol

from fastapi import HTTPException
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import KnowledgeBaseTable, ProjectTable, SkillTable


class Teamed(Protocol):
    team_id: uuid.UUID | None
    team_server: str | None


def team_of(row: Teamed, url: str | None) -> uuid.UUID | None:
    """`row`'s team on `url`, the server in effect; None when it is private there."""
    if row.team_id is None or url is None or row.team_server != url:
        return None
    return row.team_id


def set_team(row: Teamed, team_id: uuid.UUID | None, url: str | None) -> None:
    """Put `row` in `team_id` on `url`, or make it private (None)."""
    row.team_id = team_id
    row.team_server = url if team_id is not None else None


async def current_url(session: AsyncSession) -> str | None:
    """The collaboration server in effect, from Settings or this installation's configuration."""
    # Imported here: `local_settings` reaches `db.seed`, which imports `skills`, which imports this.
    from . import local_settings
    from .collaboration import server_url

    url, _ = server_url(local_settings.to_public(await local_settings.get(session)))
    return url


def usable_in(resource_team: uuid.UUID | None, project_team: uuid.UUID | None) -> bool:
    """Whether a skill or knowledge base of `resource_team` can be used by a project of `project_team`."""
    return resource_team == project_team


def require_team(team_id: uuid.UUID | None, label: str) -> uuid.UUID:
    """The team `label` publishes to; refuses (400) a private one."""
    if team_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"{label} is private, on this computer only: copy it to a "
            "team to share it.",
        )
    return team_id


async def team_for_new(
    session: AsyncSession, project: ProjectTable | None = None
) -> tuple[uuid.UUID | None, str | None]:
    """
    The team a new skill or knowledge base joins, and its server: the
    project's, when it is made for one (so the project can use it), else the
    team selected here.
    """
    from . import local_settings

    url = await current_url(session)
    if project is not None:
        team = team_of(project, url)
    else:
        team = await local_settings.selected_team(session)
    return team, url if team is not None else None


def _refusal(project: str, kind: str, names: list[str]) -> HTTPException:
    many = len(names) > 1
    return HTTPException(
        status_code=400,
        detail=f"{project} cannot use the {kind}{'s' if many else ''} "
        f"{', '.join(names)}: {'they are' if many else 'it is'} in another team. "
        "A project uses only its own team's skills and knowledge bases (a "
        "private project, only private ones); copy them to its team first.",
    )


def require_skill_usable(
    project: ProjectTable, skill: SkillTable, url: str | None
) -> None:
    """Refuse loading `skill` into `project` when it is another team's."""
    if not usable_in(team_of(skill, url), team_of(project, url)):
        raise _refusal(project.name, "skill", [skill.name])


async def check_references(
    session: AsyncSession,
    project: str,
    team_id: uuid.UUID | None,
    skills: list[str],
    knowledge_bases: list[str],
) -> None:
    """
    Refuse a project of `team_id` (its team on the server in effect) that
    loads a skill, or searches a knowledge base, of another team. One not
    here is not refused: a published project can name one this VISTA lacks
    (`project_collaboration`).
    """
    url = await current_url(session)
    if skills:
        rows = (
            await session.exec(
                select(SkillTable).where(col(SkillTable.name).in_(skills))
            )
        ).all()
        foreign = sorted(
            r.name for r in rows if not usable_in(team_of(r, url), team_id)
        )
        if foreign:
            raise _refusal(project, "skill", foreign)
    if knowledge_bases:
        rows = (
            await session.exec(
                select(KnowledgeBaseTable).where(
                    col(KnowledgeBaseTable.slug).in_(knowledge_bases)
                )
            )
        ).all()
        foreign = sorted(
            r.slug for r in rows if not usable_in(team_of(r, url), team_id)
        )
        if foreign:
            raise _refusal(project, "knowledge base", foreign)
