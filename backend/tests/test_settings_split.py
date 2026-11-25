"""
The settings split (openspec change `multi-user-hosted`, design D3; specs
`user-accounts` and `local-settings`): server settings on `local_settings`, each
user's on `user_settings`, merged by `SettingsView`.
"""

import sqlite3

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlmodel import Session as SyncSession, SQLModel, select

from harness import api_client
from harness.tokens import bearer
from vista_backend.config import settings
from vista_backend.db import db as db_module
from vista_backend.db.schemas import (
    LOCAL_SETTINGS_ID,
    LOCAL_USER_ID,
    LocalSettingsTable,
    LocalSettingsUpdate,
    UserSettingsTable,
    UserTable,
)
from vista_backend.services import current_user, local_settings
from vista_backend.services.hpc_status import hpc_status_service
from vista_backend.services.settings_view import SERVER_FIELDS, USER_FIELDS

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# The split itself
# ---------------------------------------------------------------------------


def test_every_settings_column_is_the_servers_or_a_users_and_never_both():
    assert not USER_FIELDS & SERVER_FIELDS
    assert USER_FIELDS | SERVER_FIELDS | {"id"} == set(LocalSettingsTable.model_fields)


def test_the_servers_fields_are_the_spec_ones():
    assert SERVER_FIELDS == {
        "install_id",
        "collaboration_server_url",
        "collaboration_enabled",
        "collaboration_team_id",
        "inference_api_key",
        "update_checks",
        "update_prereleases",
        "update_checked_at",
    }


# ---------------------------------------------------------------------------
# One user's credential is not another's (hosted)
# ---------------------------------------------------------------------------


async def _as(client, who, method, path, **kwargs):
    return await client.request(method, path, headers=bearer(who), **kwargs)


async def test_alices_odo_token_is_not_in_bobs_settings(session, hosted, monkeypatch):
    # A hosted S3M save is checked with S3M first (hpc-availability); fake an open token.
    async def _open(client, url, token):
        return 200, {"securityEnclave": "open"}

    monkeypatch.setattr(hpc_status_service, "_introspect", _open)
    alice = hosted.token(sub="alice-sub", name="Alice")
    bob = hosted.token(sub="bob-sub", name="Bob")
    with api_client(session) as (client, _):
        saved = await _as(
            client, alice, "PUT", "/settings", json={"odo_s3m_token": "alice-s3m"}
        )
        assert saved.status_code == 200
        assert saved.json()["has_odo_s3m_token"] is True

        mine = (await _as(client, alice, "GET", "/settings")).json()
        theirs = (await _as(client, bob, "GET", "/settings")).json()

    assert mine["has_odo_s3m_token"] is True
    assert theirs["has_odo_s3m_token"] is False
    assert "odo_s3m_token" not in mine


async def test_each_user_has_a_settings_row_of_their_own(session, hosted):
    alice = hosted.token(sub="alice-sub")
    bob = hosted.token(sub="bob-sub")
    with api_client(session) as (client, _):
        await _as(client, alice, "PUT", "/settings", json={"research_role": "chemist"})
        await _as(client, bob, "PUT", "/settings", json={"research_role": "welder"})

    rows = (await session.exec(select(UserSettingsTable))).all()
    users = {u.sub: u.id for u in (await session.exec(select(UserTable))).all()}
    by_user = {r.user_id: r.research_role for r in rows}
    assert by_user[users["alice-sub"]] == "chemist"
    assert by_user[users["bob-sub"]] == "welder"
    # One installation row, whoever asks.
    assert len((await session.exec(select(LocalSettingsTable))).all()) == 1


async def test_a_users_fields_are_read_back_for_that_user_only(session, hosted):
    alice = hosted.token(sub="alice-sub")
    bob = hosted.token(sub="bob-sub")
    with api_client(session) as (client, _):
        await _as(
            client,
            alice,
            "PUT",
            "/settings",
            json={"email": "a@example.org", "github_token": "gh-alice"},
        )
        got = (await _as(client, bob, "GET", "/settings")).json()
    assert got["email"] is None
    assert got["has_github_token"] is False


# ---------------------------------------------------------------------------
# Server settings: read-only when hosted, editable on a desktop
# ---------------------------------------------------------------------------


async def test_hosted_put_leaves_server_settings_and_says_why(
    session, hosted, monkeypatch
):
    monkeypatch.setattr(settings, "collaboration_server_url", "https://env.example")
    monkeypatch.setattr(settings, "collaboration_enabled", True)
    alice = hosted.token(sub="alice-sub")
    with api_client(session) as (client, _):
        got = await _as(
            client,
            alice,
            "PUT",
            "/settings",
            json={
                "collaboration_server_url": "https://mine.example",
                "collaboration_enabled": False,
                "inference_api_key": "sk-server",
                "update_checks": True,
                "research_role": "chemist",
            },
        )
        again = (await _as(client, alice, "GET", "/settings")).json()
    body = got.json()
    assert got.status_code == 200
    # What the environment says, not what was sent.
    assert body["collaboration_server_url"] == "https://env.example"
    assert body["collaboration_enabled"] is True
    assert body["update_checks"] is False
    assert "collaboration_server_url" in body["set_by_server"]
    assert "set by the server" in body["set_by_server_reason"]
    assert body["has_inference_api_key"] is False
    # The user's own field in the same save still lands.
    assert body["research_role"] == "chemist"
    assert again["set_by_server"] == body["set_by_server"]

    # The stored server row was never written.
    server = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    await session.refresh(server)
    assert server.collaboration_server_url is None
    assert server.collaboration_enabled is None
    assert server.inference_api_key is None


async def test_hosted_server_settings_read_from_the_environment(
    session, hosted, monkeypatch
):
    monkeypatch.setattr(settings, "collaboration_server_url", "https://env.example")
    monkeypatch.setattr(settings, "collaboration_enabled", False)
    with api_client(session) as (client, _):
        body = (await _as(client, hosted.token(), "GET", "/settings")).json()
    assert body["collaboration_server_url"] == "https://env.example"
    assert body["collaboration_enabled"] is False


async def test_desktop_server_settings_are_still_editable(session):
    with api_client(session) as (client, _):
        got = await client.put(
            "/settings",
            json={
                "collaboration_server_url": "https://mine.example",
                "collaboration_enabled": False,
                "update_prereleases": True,
            },
        )
        again = (await client.get("/settings")).json()
    assert got.status_code == 200
    body = got.json()
    assert body["set_by_server"] == []
    assert body["set_by_server_reason"] is None
    assert again["collaboration_server_url"] == "https://mine.example"
    assert again["collaboration_enabled"] is False
    server = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    assert server.collaboration_server_url == "https://mine.example"
    assert server.update_prereleases is True


async def test_a_desktop_save_goes_to_local_users_row(session):
    await local_settings.update(session, LocalSettingsUpdate(research_role="chemist"))
    row = await session.get(UserSettingsTable, LOCAL_USER_ID)
    assert row.research_role == "chemist"


# ---------------------------------------------------------------------------
# Outside a request
# ---------------------------------------------------------------------------


async def test_outside_a_request_a_desktop_acts_as_local(session):
    await local_settings.update(session, LocalSettingsUpdate(research_role="chemist"))
    view = await local_settings.get(session)
    assert view.user_id == LOCAL_USER_ID
    assert view.research_role == "chemist"


async def test_outside_a_request_a_hosted_deployment_raises(session, hosted):
    with pytest.raises(current_user.NoUserContext):
        await local_settings.get(session)
    with pytest.raises(current_user.NoUserContext):
        await local_settings.get_public(session)


async def test_use_user_sets_the_user_for_the_block(session, hosted):
    with api_client(session) as (client, _):
        await _as(
            client,
            hosted.token(sub="alice-sub"),
            "PUT",
            "/settings",
            json={"research_role": "chemist"},
        )
    alice = (
        await session.exec(select(UserTable).where(UserTable.sub == "alice-sub"))
    ).one()
    with current_user.use_user(alice.id):
        assert (await local_settings.get(session)).research_role == "chemist"
    with pytest.raises(current_user.NoUserContext):
        await local_settings.get(session)


async def test_a_request_does_not_leave_its_user_behind(session, hosted):
    with api_client(session) as (client, _):
        await _as(client, hosted.token(sub="alice-sub"), "GET", "/settings")
    with pytest.raises(current_user.NoUserContext):
        current_user.current_user_id()


# ---------------------------------------------------------------------------
# An installation from before the split
# ---------------------------------------------------------------------------

BEFORE = {
    "display_name": "Ada",
    "email": "ada@example.org",
    "research_role": "chemist",
    "research_interests": ["salts", "viscosity"],
    "personalize_responses": False,
    "inference_provider": "olcf",
    "odo_remote_dir": "/lustre/odo/ada",
    "odo_s3m_token": "s3m-saved-before-the-split",
    "github_token": "gh-saved-before-the-split",
    "odo_globus_token": "globus-saved-before-the-split",
    "hpc_hidden_clusters": ["nersc"],
    "onboarding_tour": {"status": "finished", "step": "done"},
    "odo_globus_connected_at": "2026-11-01T00:00:00+00:00",
}
SERVER_BEFORE = {
    "collaboration_server_url": "https://collab.example",
    "update_prereleases": True,
}


@pytest.fixture
def old_database(tmp_path, monkeypatch):
    """
    A `vista.db` as it was before this change: the old `local_settings` row with
    credentials set, `app_user` with `local`, and no `user_settings` table.
    Returns a coroutine function that runs `init_db` on it.
    """
    key = Fernet.generate_key().decode()
    from pydantic import SecretStr

    from vista_backend.utils.crypto import get_fernet

    monkeypatch.setattr(settings, "encryption_key", SecretStr(key))
    get_fernet.cache_clear()
    path = tmp_path / "vista.db"
    sync = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(sync)
    with SyncSession(sync) as s:
        s.add(LocalSettingsTable(**BEFORE, **SERVER_BEFORE))
        s.add(UserTable(id=LOCAL_USER_ID, sub=None))
        s.commit()
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE user_settings"))
    sync.dispose()

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")

    async def nothing(*_a, **_k) -> None:
        return None

    for name in ("seed_db", "sync_default_projects", "sync_bundled_skills"):
        monkeypatch.setattr(db_module, name, nothing)
    db_module.get_engine.cache_clear()

    async def start() -> None:
        try:
            await db_module.init_db()
        finally:
            await db_module.get_engine().dispose()
            db_module.get_engine.cache_clear()

    start.path = path
    yield start
    db_module.get_engine.cache_clear()
    get_fernet.cache_clear()


async def test_a_pre_split_database_starts_and_every_field_shows_its_value(
    old_database,
):
    await old_database()

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    engine = create_async_engine(f"sqlite+aiosqlite:///{old_database.path}")
    try:
        async with AsyncSession(engine) as s:
            view = await local_settings.get(s)
            for name, value in BEFORE.items():
                assert getattr(view, name) == value, name
            for name, value in SERVER_BEFORE.items():
                assert getattr(view, name) == value, name
            public = await local_settings.get_public(s)
            assert public.has_odo_s3m_token and public.has_github_token
            assert public.odo_s3m_token == BEFORE["odo_s3m_token"]
            assert public.research_interests == BEFORE["research_interests"]
            assert len((await s.exec(select(UserSettingsTable))).all()) == 1
            assert len((await s.exec(select(LocalSettingsTable))).all()) == 1
    finally:
        await engine.dispose()


async def test_the_copy_keeps_tokens_encrypted_at_rest(old_database):
    await old_database()
    with sqlite3.connect(old_database.path) as raw:
        (stored,) = raw.execute(
            "SELECT odo_s3m_token FROM user_settings WHERE user_id = 'local'"
        ).fetchone()
    assert stored and BEFORE["odo_s3m_token"] not in stored
    assert BEFORE["odo_s3m_token"].encode() not in old_database.path.read_bytes()


async def test_a_second_start_copies_nothing_again(old_database):
    await old_database()
    # The researcher changes a value; the old columns still hold the old one.
    with sqlite3.connect(old_database.path) as raw:
        raw.execute("UPDATE user_settings SET research_role = 'welder'")
        raw.commit()

    await old_database()

    with sqlite3.connect(old_database.path) as raw:
        rows = raw.execute("SELECT research_role FROM user_settings").fetchall()
        (old_role,) = raw.execute("SELECT research_role FROM local_settings").fetchone()
        (users,) = raw.execute("SELECT count(*) FROM app_user").fetchone()
        (servers,) = raw.execute("SELECT count(*) FROM local_settings").fetchone()
    assert rows == [("welder",)]
    assert old_role == "chemist"  # unread, untouched
    assert (users, servers) == (1, 1)


async def test_the_pre_split_database_passes_the_additive_check(old_database):
    # `init_db` raises DatabaseIncompatible, prompting a reset, if it does not.
    await old_database()
