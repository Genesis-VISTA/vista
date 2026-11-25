"""
Hosted collaboration sign-in (multi-user-hosted, task 11.1, design D11): the
client sends the access token of the request it serves, and has none outside
one. Against the real collaboration server in `dev-headers` mode, with a
recording transport to see what reached it.
"""

import httpx
import pytest
from harness.api import api_client
from harness.tokens import bearer
from sqlalchemy import text
from vista_backend.config import settings
from vista_backend.db.schemas import LOCAL_USER_ID, LocalSettingsUpdate
from vista_backend.services import collaboration, current_user, local_settings
from vista_backend.services.collaboration import (
    NEEDS_WEB_APP,
    CollaborationUnavailable,
)

pytestmark = pytest.mark.anyio

URL = "https://collab.test"


@pytest.fixture
async def seen(collab_server, monkeypatch):
    """The `Authorization` headers the server received, in order."""
    headers: list[str | None] = []
    inner = httpx.ASGITransport(app=collab_server)

    class Recording(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            headers.append(request.headers.get("authorization"))
            return await inner.handle_async_request(request)

    monkeypatch.setattr(collaboration, "transport", Recording())
    return headers


@pytest.fixture(autouse=True)
def server_url(monkeypatch):
    """Hosted users start with no settings of their own: the installation's address applies."""
    monkeypatch.setattr(settings, "collaboration_server_url", URL)


async def _local(session, **fields):
    """Settings as a background job for `local` would read them (hosted has no default user)."""
    with current_user.use_user(LOCAL_USER_ID):
        await local_settings.update(session, LocalSettingsUpdate.model_validate(fields))
        return await local_settings.get_public(session)


async def test_status_uses_the_requests_token(hosted, seen, session):
    token = hosted.token()
    with api_client(session) as (client, _):
        res = await client.get("/collaboration/status", headers=bearer(token))
    body = res.json()
    assert body["signed_in"] and body["shown"] and body["sign_in_url"] is None
    assert seen and set(seen) == {f"Bearer {token}"}  # never the saved token


async def test_a_listing_route_sends_it_too(hosted, seen, session):
    token = hosted.token()
    with api_client(session) as (client, _):
        res = await client.get("/collaboration/skills", headers=bearer(token))
    assert res.status_code == 200
    assert set(seen) == {f"Bearer {token}"}


async def test_outside_a_request_there_is_no_token(hosted, seen, session):
    local = await _local(session, collaboration_token="saved")
    assert current_user.request_access_token() is None
    status = await collaboration.status(local)
    assert not status.signed_in and status.reason == NEEDS_WEB_APP
    with pytest.raises(CollaborationUnavailable, match="web app"):
        collaboration.client_for(local)
    assert seen == []  # nothing reached the server


async def test_the_token_does_not_outlive_the_request(hosted, seen, session):
    with api_client(session) as (client, _):
        await client.get("/collaboration/status", headers=bearer(hosted.token()))
    assert current_user.request_access_token() is None


async def test_the_token_is_never_written_to_the_database(hosted, seen, session):
    token = hosted.token()
    with api_client(session) as (client, _):
        await client.get("/collaboration/status", headers=bearer(token))
        await client.get("/collaboration/skills", headers=bearer(token))
    tables = (
        await session.exec(text("select name from sqlite_master where type='table'"))
    ).all()
    for (name,) in tables:
        rows = (await session.exec(text(f'select * from "{name}"'))).all()
        assert token not in repr(rows), name


async def test_desktop_keeps_the_saved_token(collab_server, session):
    local = await _local(
        session, collaboration_server_url=URL, collaboration_token="pasted"
    )
    async with collaboration.client_for(local) as client:
        assert client._http.headers["authorization"] == "Bearer pasted"
