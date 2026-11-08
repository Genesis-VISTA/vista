"""Linking needs both the Slack account and the desktop app; codes are single-use and expire."""

import uuid
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
from vista_backend.db.schemas import SlackLinkCodeTable, SlackLinkTable
from vista_backend.services import collaboration
from vista_backend.slack import linking, store

from .conftest import TEAM, project

SIGNED_OUT = collaboration.CollaborationStatus(
    configured=False, reachable=False, reason="not configured", url=None, source=None
)


def _signed_in(sub="kc-123", name="Ada Lovelace", email="ada@ornl.gov"):
    return SIGNED_OUT.model_copy(
        update={
            "configured": True,
            "reachable": True,
            "reason": None,
            "signed_in": True,
            "user": collaboration.RemoteUser(sub=sub, email=email, name=name),
        }
    )


@pytest.fixture(autouse=True)
def who(monkeypatch):
    """What `collaboration.status` says; signed out unless a test sets it."""
    current = {"status": SIGNED_OUT}

    async def status(local):
        return current["status"]

    monkeypatch.setattr(collaboration, "status", status)
    return current


async def _code(session, user="U1"):
    return await linking.issue_code(
        session, team_id=TEAM, slack_user_id=user, dm_channel_id=f"D{user}"
    )


@pytest.mark.anyio
async def test_a_code_links_the_slack_user_who_asked(session, local):
    code = await _code(session)
    assert len(code) == 6 and code.isdigit()

    view = await create_slack_link(SlackLinkCreate(code=code), session, local)

    assert (view.team_id, view.slack_user_id) == (TEAM, "U1")
    assert [v.id for v in await list_slack_links(session)] == [view.id]


@pytest.mark.anyio
async def test_linking_records_who_the_researcher_is_on_the_collaboration_server(
    session, local, who
):
    who["status"] = _signed_in()

    view = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )

    assert view.collab_name == "Ada Lovelace"
    link = await session.get(SlackLinkTable, view.id)
    assert link is not None and link.collab_sub == "kc-123"


@pytest.mark.anyio
async def test_the_email_stands_in_for_a_missing_name(session, local, who):
    who["status"] = _signed_in(name=None)

    view = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )

    assert view.collab_name == "ada@ornl.gov"


@pytest.mark.anyio
async def test_linking_signed_out_records_no_identity(session, local):
    view = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )

    assert view.collab_name is None
    link = await session.get(SlackLinkTable, view.id)
    assert link is not None and link.collab_sub is None


@pytest.mark.anyio
async def test_the_code_is_stored_only_as_a_hash(session):
    code = await _code(session)
    rows = (await session.exec(select(SlackLinkCodeTable))).all()
    assert len(rows) == 1 and code not in rows[0].code_hash


@pytest.mark.anyio
async def test_a_wrong_code_links_nobody(session, local):
    code = await _code(session)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    with pytest.raises(HTTPException) as err:
        await create_slack_link(SlackLinkCreate(code=wrong), session, local)
    assert err.value.status_code == 400 and "isn't right" in err.value.detail
    assert await list_slack_links(session) == []


@pytest.mark.anyio
async def test_an_expired_code_links_nobody(session, local, monkeypatch):
    code = await _code(session)
    later = store.now() + timedelta(minutes=11)
    monkeypatch.setattr(linking, "now", lambda: later)

    with pytest.raises(HTTPException) as err:
        await create_slack_link(SlackLinkCreate(code=code), session, local)
    assert "expired" in err.value.detail
    assert await list_slack_links(session) == []


@pytest.mark.anyio
async def test_a_code_works_once(session, local):
    code = await _code(session)
    await create_slack_link(SlackLinkCreate(code=code), session, local)

    with pytest.raises(HTTPException):
        await create_slack_link(SlackLinkCreate(code=code), session, local)
    assert len(await list_slack_links(session)) == 1


@pytest.mark.anyio
async def test_asking_again_replaces_the_old_code(session, local):
    first = await _code(session)
    second = await _code(session)

    if first != second:
        with pytest.raises(HTTPException):
            await create_slack_link(SlackLinkCreate(code=first), session, local)
    await create_slack_link(SlackLinkCreate(code=second), session, local)


@pytest.mark.anyio
async def test_relinking_the_same_slack_account_updates_its_link(session, local, who):
    first = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )
    who["status"] = _signed_in()

    again = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )

    assert again.id == first.id
    assert [v.id for v in await list_slack_links(session)] == [first.id]
    assert again.collab_name == "Ada Lovelace"


@pytest.mark.anyio
async def test_every_linked_account_is_listed(session, local):
    a = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U1")), session, local
    )
    b = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U2")), session, local
    )

    assert {v.id for v in await list_slack_links(session)} == {a.id, b.id}


@pytest.mark.anyio
async def test_a_link_is_changed_and_removed(session, local):
    view = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )

    updated = await update_slack_link(
        view.id, SlackLinkUpdate(notify_in_thread=True), session
    )
    assert updated.notify_in_thread is True
    await delete_slack_link(view.id, session)
    assert await list_slack_links(session) == []


@pytest.mark.anyio
async def test_an_unknown_link_is_not_found(session):
    missing = uuid.uuid4()
    for call in (
        update_slack_link(missing, SlackLinkUpdate(notify_in_thread=True), session),
        delete_slack_link(missing, session),
    ):
        with pytest.raises(HTTPException) as err:
            await call
        assert err.value.status_code == 404


@pytest.mark.anyio
async def test_a_link_runs_in_an_existing_project_only(session, local):
    view = await create_slack_link(
        SlackLinkCreate(code=await _code(session)), session, local
    )

    with pytest.raises(HTTPException) as err:
        await update_slack_link(
            view.id, SlackLinkUpdate(project_id=uuid.uuid4()), session
        )
    assert err.value.status_code == 400

    project_id = (await project(session)).id
    updated = await update_slack_link(
        view.id, SlackLinkUpdate(project_id=project_id), session
    )
    assert updated.project_id == project_id
