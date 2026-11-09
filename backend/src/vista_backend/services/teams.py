"""
Teams, from this installation's side: which team a new project, skill or
knowledge base joins, what a project may use, and what may be published.

Every project, skill and knowledge base here is either private (on this
computer only, as everything made outside a team's project is, and everything
VISTA seeds or bundles) or in one team on a collaboration server. A team
means something only on its own server, so a row records both (`team_id`,
`team_server`), and is in its team only while that server is the one in
effect (`team_of`): on another, it is private, and it is in its team again
when its server is. Nothing is erased by changing servers.

A team's project uses only skills and knowledge bases of its own team, so
that whoever can see the project can see everything it uses: checked whenever
it gains one, not only when it is published. A private project, seen by no one
else, may use any team's as well as private ones; it can then be published
only into that team. A private one joins a team when it is first published,
into the team the researcher chooses (`team_publish`); from then on it stays
in that team.

The teams themselves, and who is in them, are the collaboration server's
(`collaboration.CollaborationClient`).
"""

import functools
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, ParamSpec, Protocol, TypeVar

from fastapi import HTTPException
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import KnowledgeBaseTable, ProjectTable, SkillTable

if TYPE_CHECKING:
    from .collaboration import CollaborationClient


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
    """
    Whether a skill or knowledge base of `resource_team` can be used by a
    project of `project_team`: a team's project, only its own team's; a
    private project (None), any.
    """
    return project_team is None or resource_team == project_team


def require_team(team_id: uuid.UUID | None, label: str) -> uuid.UUID:
    """The team `label` publishes to; refuses (400) a private one."""
    if team_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"{label} is private, on this computer only: choose a team "
            "to publish it to.",
        )
    return team_id


class NotInTeam(Exception):
    """
    The researcher is not, or no longer, in the team a publish would go to.
    The API answers 400 with code `not_in_team`, so the interface asks for
    their teams again and offers the ones they are in.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# The researcher's teams, asked once per publish (`remembers_teams`): a project
# publish publishes each skill and knowledge base it uses, and each checks.
_membership: ContextVar[dict[str, frozenset[uuid.UUID]] | None] = ContextVar(
    "team_membership", default=None
)

P = ParamSpec("P")
R = TypeVar("R")


def remembers_teams(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """
    Ask the server for the researcher's teams at most once while `fn` runs,
    however many publishes it makes (`my_teams`). The outermost call owns
    what was asked, and forgets it when it returns.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if _membership.get() is not None:
            return await fn(*args, **kwargs)
        token = _membership.set({})
        try:
            return await fn(*args, **kwargs)
        finally:
            _membership.reset(token)

    return wrapper


async def my_teams(client: "CollaborationClient") -> frozenset[uuid.UUID]:
    """The teams the researcher is in, asked once per publish (`remembers_teams`)."""
    known = _membership.get()
    if known is not None and "teams" in known:
        return known["teams"]
    teams = frozenset(t.id for t in (await client.me()).teams)
    if known is not None:
        known["teams"] = teams
    return teams


async def require_in_team(
    client: "CollaborationClient", team_id: uuid.UUID, detail: str
) -> frozenset[uuid.UUID]:
    """The researcher's teams; refuses (`NotInTeam`, with `detail`) when `team_id` is not one."""
    mine = await my_teams(client)
    if team_id not in mine:
        raise NotInTeam(detail)
    return mine


async def require_still_in(
    client: "CollaborationClient", team_id: uuid.UUID, label: str
) -> None:
    """
    Refuse to publish a new version of `label` to `team_id` when the
    researcher has left that team: the server would refuse it as missing.
    Like a private one, it is published into another of their teams instead
    (`team_publish`). Asked before anything is uploaded.
    """
    await require_in_team(
        client,
        team_id,
        f"{label} is in a team you are no longer in: choose one of your teams "
        "to publish it to.",
    )


async def team_for_new(
    session: AsyncSession, project: ProjectTable | None = None
) -> tuple[uuid.UUID | None, str | None]:
    """
    The team a new project, skill or knowledge base joins, and its server: the
    project's, when it is made for one (so the project can use it), else none:
    it is private until it is first published.
    """
    if project is None:
        return None, None
    url = await current_url(session)
    team = team_of(project, url)
    return team, url if team is not None else None


def _refusal(project: str, kind: str, names: list[str]) -> HTTPException:
    many = len(names) > 1
    return HTTPException(
        status_code=400,
        detail=f"{project} cannot use the {kind}{'s' if many else ''} "
        f"{', '.join(names)}: {'they are' if many else 'it is'} not in its team. "
        "A team's project uses only its own team's skills and knowledge bases.",
    )


def require_skill_usable(
    project: ProjectTable, skill: SkillTable, url: str | None
) -> None:
    """Refuse loading `skill` into a team's `project` when it is not that team's."""
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
