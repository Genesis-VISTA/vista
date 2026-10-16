"""
Slack settings from the desktop app: off by default, tokens write-only and kept
in the OS secret store, and enabling refused until it can work.
"""

import keyring
import pytest
from fastapi import HTTPException
from keyring.backends.fail import Keyring as FailKeyring

from vista_backend.api.slack import (
    SlackConfigUpdate,
    get_slack_config,
    update_slack_config,
)
from vista_backend.db.schemas import SlackConfigTable
from vista_backend.utils import secret_store

BOT = "xoxb-1111-2222-notreal"
APP = "xapp-1-A111-2222-notreal"


async def _put(session, user, **fields):
    return await update_slack_config(SlackConfigUpdate(**fields), session, user)


@pytest.mark.anyio
async def test_a_default_install_has_slack_off(session, alice):
    view = await get_slack_config(session, alice)

    assert view.enabled is False
    assert view.handle_here is False
    assert view.has_bot_token is False and view.has_app_token is False
    assert view.locked is False
    assert (view.max_nodes, view.max_walltime_minutes, view.max_jobs_per_day) == (
        4,
        120,
        5,
    )
    assert view.prevent_sleep is False


@pytest.mark.anyio
async def test_tokens_are_only_reported_as_set(session, alice, memory_keyring):
    view = await _put(session, alice, bot_token=BOT, app_token=APP)

    dumped = view.model_dump(mode="json")
    assert dumped["has_bot_token"] is True and dumped["has_app_token"] is True
    assert BOT not in str(dumped) and APP not in str(dumped)
    assert BOT in memory_keyring.store.values()
    row = await session.get(SlackConfigTable, 1)
    assert BOT not in str(row.model_dump()) and APP not in str(row.model_dump())


@pytest.mark.anyio
async def test_null_keeps_a_token_and_blank_removes_it(session, alice):
    await _put(session, alice, bot_token=BOT, app_token=APP)

    await _put(session, alice, max_nodes=2)
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) == BOT

    view = await _put(session, alice, bot_token="")
    assert view.has_bot_token is False and view.has_app_token is True


@pytest.mark.anyio
async def test_a_token_of_the_wrong_kind_is_refused(session, alice):
    with pytest.raises(HTTPException) as err:
        await _put(session, alice, bot_token=APP)
    assert err.value.status_code == 400
    assert "xoxb-" in err.value.detail

    with pytest.raises(HTTPException):
        await _put(session, alice, bot_token=BOT, app_token=BOT)
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) is None  # neither written


@pytest.mark.anyio
async def test_no_secret_store_refuses_and_says_so(session, alice):
    keyring.set_keyring(FailKeyring())

    with pytest.raises(HTTPException) as err:
        await _put(session, alice, bot_token=BOT)
    assert "secret store" in err.value.detail

    with pytest.raises(HTTPException) as err:
        await _put(session, alice, enabled=True)
    assert "secret store" in err.value.detail
    assert (await get_slack_config(session, alice)).secret_store_available is False


@pytest.mark.anyio
async def test_enabling_needs_both_tokens_and_a_workspace(session, alice):
    await _put(session, alice, bot_token=BOT)
    with pytest.raises(HTTPException) as err:
        await _put(session, alice, enabled=True)
    assert "app-level token" in err.value.detail

    await _put(session, alice, app_token=APP)
    with pytest.raises(HTTPException) as err:
        await _put(session, alice, enabled=True)
    assert "workspace" in err.value.detail
    assert (await get_slack_config(session, alice)).enabled is False

    view = await _put(session, alice, enabled=True, allowed_team_ids=[" t0123 "])
    assert view.enabled is True
    assert view.allowed_team_ids == ["T0123"]


@pytest.mark.anyio
async def test_removing_a_token_while_enabled_is_refused(session, alice):
    await _put(
        session,
        alice,
        bot_token=BOT,
        app_token=APP,
        allowed_team_ids=["T1"],
        enabled=True,
    )
    with pytest.raises(HTTPException):
        await _put(session, alice, app_token="")
    # A refused save changes nothing, the token included.
    view = await get_slack_config(session, alice)
    assert view.enabled is True and view.has_app_token is True

    view = await _put(session, alice, enabled=False, app_token="")
    assert view.enabled is False and view.has_app_token is False


@pytest.mark.anyio
async def test_lock_and_unlock_from_the_desktop(session, alice):
    view = await _put(session, alice, locked=True)
    assert view.locked is True
    assert view.locked_at is not None
    assert view.locked_by == f"desktop:{alice.email}"

    view = await _put(session, alice, locked=False)
    assert view.locked is False and view.locked_at is None and view.locked_by is None


@pytest.mark.anyio
async def test_limits_must_be_positive(session, alice):
    with pytest.raises(ValueError):
        SlackConfigUpdate(max_nodes=0)
    view = await _put(
        session, alice, max_nodes=1, max_walltime_minutes=30, max_jobs_per_day=0
    )
    assert (view.max_nodes, view.max_walltime_minutes, view.max_jobs_per_day) == (
        1,
        30,
        0,
    )
