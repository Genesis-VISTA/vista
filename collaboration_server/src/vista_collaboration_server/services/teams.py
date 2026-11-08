"""
Teams: who can see an item, and publish to it.

Anyone signed in can create a team, and is its first member. Any member can add
anyone who has signed in (`services/users.py`), and can leave, except the last:
a team nobody is in would hold items nobody could reach. Everyone is in the
public team, which has no member rows and so no members to manage.

A team the caller is not in is `NotFound`, never forbidden: whether it exists
is not theirs to know. `visible_teams` is the one rule every item query uses.
"""

import uuid
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import ColumnElement, Select
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    PUBLIC_TEAM_ID,
    MemberPublic,
    TeamMemberTable,
    TeamPublic,
    TeamTable,
    UserTable,
)
from .errors import Conflict, Invalid, NotFound
from .identity import User


def _member_teams(user: User) -> Select:
    return select(TeamMemberTable.team_id).where(TeamMemberTable.sub == user.sub)


def visible_teams(team_id: Any, user: User) -> ColumnElement[bool]:
    """
    Whether `team_id` (a column) is a team `user` is in: the public team, or
    one with a member row for them. A condition, not a set read beforehand, so
    that a write can check it in the same statement.
    """
    return or_(team_id == PUBLIC_TEAM_ID, col(team_id).in_(_member_teams(user)))


def team_public(row: TeamTable) -> TeamPublic:
    return TeamPublic(
        id=row.id,
        name=row.name,
        public=row.id == PUBLIC_TEAM_ID,
        created_by=row.created_by,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
    )


async def list_teams(session: AsyncSession, user: User) -> list[TeamTable]:
    """The teams `user` is in: the public team first, then by name."""
    query = (
        select(TeamTable)
        .where(visible_teams(col(TeamTable.id), user))
        .order_by(col(TeamTable.id) != PUBLIC_TEAM_ID, func.lower(TeamTable.name))
    )
    return list((await session.exec(query)).all())


async def get_team(session: AsyncSession, team_id: uuid.UUID, user: User) -> TeamTable:
    """The team, if `user` is in it; `NotFound` whether or not it exists otherwise."""
    query = select(TeamTable).where(
        TeamTable.id == team_id, visible_teams(col(TeamTable.id), user)
    )
    row = (await session.exec(query)).first()
    if row is None:
        raise NotFound("No such team.")
    return row


async def _private_team(
    session: AsyncSession, team_id: uuid.UUID, user: User
) -> TeamTable:
    row = await get_team(session, team_id, user)
    if row.id == PUBLIC_TEAM_ID:
        raise Invalid("Everyone is in the public team; it has no members to manage.")
    return row


async def create_team(session: AsyncSession, name: str, user: User) -> TeamTable:
    """A new team, with `user` as its first member, committed together."""
    team = TeamTable(name=name, created_by=user.sub, created_by_name=user.name)
    session.add(team)
    try:
        # The team before its member: with no relationship between them, the
        # unit of work does not order the inserts by their foreign key.
        await session.flush()
    except IntegrityError as e:
        # The name, whatever its case: the id is new.
        await session.rollback()
        raise Conflict(f"A team named {name!r} exists already.") from e
    # The creator has signed in: `identity.get_user` recorded them.
    session.add(TeamMemberTable(team_id=team.id, sub=user.sub, added_by=user.sub))
    await session.commit()
    await session.refresh(team)
    return team


def _member_public(member: TeamMemberTable, person: UserTable) -> MemberPublic:
    return MemberPublic(
        sub=person.sub,
        email=person.email,
        name=person.name,
        added_by=member.added_by,
        added_at=member.added_at,
    )


async def list_members(
    session: AsyncSession, team_id: uuid.UUID, user: User
) -> list[MemberPublic]:
    """A team's members, by name, for a member of it."""
    await _private_team(session, team_id, user)
    query = (
        select(TeamMemberTable, UserTable)
        .join(UserTable, col(UserTable.sub) == col(TeamMemberTable.sub))
        .where(TeamMemberTable.team_id == team_id)
        .order_by(
            col(UserTable.name).is_(None), col(UserTable.name), col(UserTable.email)
        )
    )
    return [_member_public(m, u) for m, u in (await session.exec(query)).all()]


async def add_member(
    session: AsyncSession, team_id: uuid.UUID, sub: str, user: User
) -> MemberPublic:
    """
    Add `sub`, someone who has signed in, to a team `user` is in. Adding a
    member already there changes nothing and answers with them.
    """
    await _private_team(session, team_id, user)
    person = await session.get(UserTable, sub)
    if person is None:
        raise NotFound("No such user: they must sign in to this server once first.")
    member = await session.get(TeamMemberTable, (team_id, sub))
    if member is None:
        member = TeamMemberTable(team_id=team_id, sub=sub, added_by=user.sub)
        session.add(member)
        try:
            await session.commit()
        except IntegrityError:
            # Someone else added them meanwhile: they are a member either way.
            await session.rollback()
            member = await session.get(TeamMemberTable, (team_id, sub))
            assert member is not None
        else:
            await session.refresh(member)
        await session.refresh(person)  # a commit expires what it loaded
    return _member_public(member, person)


async def leave(session: AsyncSession, team_id: uuid.UUID, user: User) -> None:
    """
    Take `user` out of a team they are in, unless they are its last member.
    One statement, so two last members leaving at once cannot both succeed.
    """
    await _private_team(session, team_id, user)
    others = (
        select(func.count())
        .select_from(TeamMemberTable)
        .where(TeamMemberTable.team_id == team_id, TeamMemberTable.sub != user.sub)
        .scalar_subquery()
    )
    result = await session.exec(
        delete(TeamMemberTable).where(
            col(TeamMemberTable.team_id) == team_id,
            col(TeamMemberTable.sub) == user.sub,
            others > 0,
        )
    )
    await session.commit()
    if result.rowcount != 1:
        raise Conflict(
            "You are this team's last member: add someone else before leaving, "
            "or its items would be out of everyone's reach."
        )
