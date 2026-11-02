import uuid
from typing import Annotated as A

from fastapi import APIRouter, Depends, Query

from ..db.db import SessionDep
from ..db.schemas import MemberCreate, MemberPublic, TeamCreate, TeamPublic, UserPublic
from ..services import teams, users
from ..services.identity import UserDep, get_user

# Every route needs a signed-in user, and a team they are not in is 404
# (`services/teams.py`).
router = APIRouter(tags=["teams"], dependencies=[Depends(get_user)])


@router.get("/teams")
async def list_teams(session: SessionDep, user: UserDep) -> list[TeamPublic]:
    """The caller's teams: the public team first, then by name."""
    return [teams.team_public(t) for t in await teams.list_teams(session, user)]


@router.post("/teams", status_code=201)
async def create_team(
    data: TeamCreate, session: SessionDep, user: UserDep
) -> TeamPublic:
    """A new team, with the caller as its first member. `409` if the name is taken, in any case."""
    return teams.team_public(await teams.create_team(session, data.name, user))


@router.get("/teams/{team_id}")
async def get_team(
    team_id: uuid.UUID, session: SessionDep, user: UserDep
) -> TeamPublic:
    return teams.team_public(await teams.get_team(session, team_id, user))


@router.get("/teams/{team_id}/members")
async def list_members(
    team_id: uuid.UUID, session: SessionDep, user: UserDep
) -> list[MemberPublic]:
    """A team's members. `400` for the public team, which everyone is in."""
    return await teams.list_members(session, team_id, user)


@router.post("/teams/{team_id}/members", status_code=201)
async def add_member(
    team_id: uuid.UUID, data: MemberCreate, session: SessionDep, user: UserDep
) -> MemberPublic:
    """Add someone who has signed in (`GET /users`); adding a member again changes nothing."""
    return await teams.add_member(session, team_id, data.sub, user)


@router.delete("/teams/{team_id}/members/me", status_code=204)
async def leave_team(team_id: uuid.UUID, session: SessionDep, user: UserDep) -> None:
    """Leave the team. `409` for its last member."""
    await teams.leave(session, team_id, user)


@router.get("/users")
async def search_users(
    session: SessionDep,
    q: A[str | None, Query(max_length=200)] = None,
    limit: A[int, Query(ge=1, le=100)] = 20,
) -> list[UserPublic]:
    """Everyone who has signed in whose name or email contains `q`, by name: who can be added to a team."""
    return [UserPublic.model_validate(u) for u in await users.search(session, q, limit)]
