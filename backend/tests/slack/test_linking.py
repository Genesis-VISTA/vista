"""Linking needs both the Slack account and the desktop app; codes are single-use and expire."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from vista_backend.api.slack import (
    SlackLinkCreate,
    SlackLinkUpdate,
    create_slack_link,
    delete_slack_link,
    list_slack_links,
    update_slack_link,
)
from vista_backend.db.schemas import SlackLinkCodeTable
from vista_backend.slack import linking, store

from .conftest import TEAM


async def _code(session, user="U1"):
    return await linking.issue_code(
        session, team_id=TEAM, slack_user_id=user, dm_channel_id=f"D{user}"
    )


@pytest.mark.anyio
async def test_a_code_links_the_slack_user_who_asked(session, alice):
    code = await _code(session)
    assert len(code) == 6 and code.isdigit()

    view = await create_slack_link(SlackLinkCreate(code=code), session, alice)

    assert (view.team_id, view.slack_user_id) == (TEAM, "U1")
    assert [v.id for v in await list_slack_links(session, alice)] == [view.id]


@pytest.mark.anyio
async def test_the_code_is_stored_only_as_a_hash(session):
    code = await _code(session)
    rows = (await session.exec(select(SlackLinkCodeTable))).all()
    assert len(rows) == 1 and code not in rows[0].code_hash


@pytest.mark.anyio
async def test_a_wrong_code_links_nobody(session, alice):
    code = await _code(session)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    with pytest.raises(HTTPException) as err:
        await create_slack_link(SlackLinkCreate(code=wrong), session, alice)
    assert err.value.status_code == 400 and "isn't right" in err.value.detail
    assert await list_slack_links(session, alice) == []


@pytest.mark.anyio
async def test_an_expired_code_links_nobody(session, alice, monkeypatch):
    code = await _code(session)
    later = store.now() + timedelta(minutes=11)
    monkeypatch.setattr(linking, "now", lambda: later)

    with pytest.raises(HTTPException) as err:
        await create_slack_link(SlackLinkCreate(code=code), session, alice)
    assert "expired" in err.value.detail
    assert await list_slack_links(session, alice) == []


@pytest.mark.anyio
async def test_a_code_works_once(session, alice, bob):
    code = await _code(session)
    await create_slack_link(SlackLinkCreate(code=code), session, alice)

    with pytest.raises(HTTPException):
        await create_slack_link(SlackLinkCreate(code=code), session, bob)
    assert await list_slack_links(session, bob) == []


@pytest.mark.anyio
async def test_asking_again_replaces_the_old_code(session, alice):
    first = await _code(session)
    second = await _code(session)

    if first != second:
        with pytest.raises(HTTPException):
            await create_slack_link(SlackLinkCreate(code=first), session, alice)
    await create_slack_link(SlackLinkCreate(code=second), session, alice)


@pytest.mark.anyio
async def test_relinking_moves_the_slack_user(session, alice, bob):
    await create_slack_link(SlackLinkCreate(code=await _code(session)), session, alice)
    await create_slack_link(SlackLinkCreate(code=await _code(session)), session, bob)

    assert await list_slack_links(session, alice) == []
    assert len(await list_slack_links(session, bob)) == 1


@pytest.mark.anyio
async def test_only_the_owner_changes_or_removes_a_link(session, alice, bob):
    view = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, alice
    )

    for call in (
        update_slack_link(
            view.id, SlackLinkUpdate(notify_in_thread=True), session, bob
        ),
        delete_slack_link(view.id, session, bob),
    ):
        with pytest.raises(HTTPException) as err:
            await call
        assert err.value.status_code == 404

    updated = await update_slack_link(
        view.id, SlackLinkUpdate(notify_in_thread=True), session, alice
    )
    assert updated.notify_in_thread is True
    await delete_slack_link(view.id, session, alice)
    assert await list_slack_links(session, alice) == []
