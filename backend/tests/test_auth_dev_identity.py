"""
Tests for the dev-only `X-Vista-User-Email` identity header (evaluation
plan M6/E15): it selects an existing user in dev mode, falls back to the
default admin, and is inert in prod (the SSO 501 returns first).
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from vista_backend.config import settings
from vista_backend.db.schemas import UserTable
import vista_backend.services.auth as auth


def request_with_header(email: str | None):
    headers = {"X-Vista-User-Email": email} if email is not None else {}
    return SimpleNamespace(headers=headers)


@pytest.fixture
async def seeded(session):
    default = UserTable(
        id=uuid.uuid4(),
        email="vista-test-admin@americansciencecloud.org",
        is_admin=True,
    )
    alice = UserTable(id=uuid.uuid4(), email="loadgen-user-0@example.com")
    session.add_all([default, alice])
    await session.flush()
    return default, alice


@pytest.mark.anyio
async def test_header_selects_identity(monkeypatch, session, seeded):
    monkeypatch.setattr(settings, "env", "dev")
    _, alice = seeded
    user = await auth.get_user(session, request_with_header(alice.email))
    assert user.email == alice.email


@pytest.mark.anyio
async def test_falls_back_to_default_without_header(monkeypatch, session, seeded):
    monkeypatch.setattr(settings, "env", "dev")
    default, _ = seeded
    user = await auth.get_user(session, request_with_header(None))
    assert user.email == default.email


@pytest.mark.anyio
async def test_prod_ignores_header(monkeypatch, session, seeded):
    monkeypatch.setattr(settings, "env", "prod")
    _, alice = seeded
    with pytest.raises(HTTPException) as exc:
        await auth.get_user(session, request_with_header(alice.email))
    assert exc.value.status_code == 501


@pytest.mark.anyio
async def test_unknown_identity_rejected(monkeypatch, session, seeded):
    monkeypatch.setattr(settings, "env", "dev")
    with pytest.raises(HTTPException) as exc:
        await auth.get_user(session, request_with_header("nobody@example.com"))
    assert exc.value.status_code == 401
