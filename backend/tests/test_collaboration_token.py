"""
The collaboration server's token: pasted into Settings from its sign-in page,
sent as a Bearer token, and what the status says without one. Against a real
server in `oidc` mode, mounted in-process behind a stand-in for Obsidian's
gateway, with a stand-in Keycloak (`vista_collaboration_server.testing`).
"""

import httpx
import pytest

from harness.api import api_client
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import collaboration, local_settings
from vista_backend.services.collaboration import CollaborationUnauthorized

pytestmark = pytest.mark.anyio

PREFIX = "/vista/api"
URL = f"https://obsidian.test{PREFIX}"


@pytest.fixture
async def oidc_server(tmp_path, monkeypatch):
    """The collaboration server as deployed: it believes only the realm's tokens."""
    from vista_collaboration_server.api.app import create_app
    from vista_collaboration_server.config import CollabSettings
    from vista_collaboration_server.testing import FakeIssuer

    data_dir = tmp_path / "collaboration-server"
    collab_settings = CollabSettings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{data_dir / 'collaboration.db'}",
        auth_mode="oidc",
    )
    issuer = FakeIssuer(collab_settings)
    app = create_app(collab_settings, verifier=issuer.verifier())

    async def gateway(scope, receive, send):
        """Obsidian's gateway: strips `/vista/api` before forwarding."""
        if scope["type"] == "http" and scope["path"].startswith(PREFIX):
            path = scope["path"][len(PREFIX) :] or "/"
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await app(scope, receive, send)

    async with app.router.lifespan_context(app):
        monkeypatch.setattr(
            collaboration, "transport", httpx.ASGITransport(app=gateway)
        )
        yield issuer


async def _save(session, **fields):
    await local_settings.update(session, LocalSettingsUpdate.model_validate(fields))
    return await local_settings.get_public(session)


async def test_a_pasted_token_signs_in_and_publishes(oidc_server, session):
    local = await _save(
        session,
        collaboration_server_url=URL,
        collaboration_token=f"  Bearer {oidc_server.token()}\n",
    )
    assert local.has_collaboration_token
    status = await collaboration.status(local)
    assert status.reachable and status.signed_in and status.reason is None

    async with collaboration.client_for(local) as client:
        item = await client.create_item("skill", "salt", manifest={"v": 1})
    assert item.created_by == "a5b1c0de-0000-4000-8000-000000000001"
    assert item.created_by_name == "Ada Lovelace"


async def test_without_a_token_the_status_says_where_to_get_one(oidc_server, session):
    local = await _save(session, collaboration_server_url=URL)
    status = await collaboration.status(local)
    assert status.reachable and not status.signed_in and status.sign_in_required
    assert status.sign_in_url == f"{URL}/sign-in"
    assert f"Get a token from {URL}/sign-in" in status.reason

    with pytest.raises(CollaborationUnauthorized):
        async with collaboration.client_for(local) as client:
            await client.list_items()
    with api_client(session) as (client, _):
        res = await client.get("/collaboration/skills")
    assert res.status_code == 401
    assert res.json()["code"] == "collaboration_sign_in_required"


async def test_an_account_without_access_is_not_sent_to_sign_in_again(
    oidc_server, session
):
    """A new token would not have the role either: say so, not "sign in"."""
    local = await _save(
        session,
        collaboration_server_url=URL,
        collaboration_token=oidc_server.token(roles=[]),
    )
    status = await collaboration.status(local)
    assert status.reachable and not status.signed_in
    assert not status.sign_in_required
    assert "not been given access" in status.reason
    assert "ask the administrators" in status.reason

    with api_client(session) as (client, _):
        res = await client.get("/collaboration/skills")
    assert res.status_code == 403
    assert res.json()["code"] == "collaboration_forbidden"


async def test_the_gateway_asking_for_a_token(monkeypatch, session):
    """Obsidian's gateway answers 401 before the server is reached, even for `/version`."""
    monkeypatch.setattr(
        collaboration,
        "transport",
        httpx.MockTransport(
            lambda request: httpx.Response(
                401,
                json={"error": "access_denied", "message": "Authorization Required"},
            )
        ),
    )
    status = await collaboration.status(
        await _save(session, collaboration_server_url=URL)
    )
    assert not status.reachable and status.sign_in_required
    assert f"{URL}/sign-in" in status.reason


@pytest.mark.parametrize(
    "url", ["http://obsidian.test/vista/api", "http://10.0.0.5:8001"]
)
async def test_a_token_is_never_sent_unencrypted(monkeypatch, session, url):
    sent: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("authorization"))
        return httpx.Response(401, json={"detail": "Sign in."})

    monkeypatch.setattr(collaboration, "transport", httpx.MockTransport(handler))
    local = await _save(session, collaboration_server_url=url, collaboration_token="t")
    status = await collaboration.status(local)
    assert sent and all(h is None for h in sent)
    assert "only over https" in status.reason
    # No sign-in page to offer: a token pasted from it would never be sent.
    assert status.sign_in_url is None


def test_this_machine_may_have_it_over_http():
    for url in ("http://127.0.0.1:8002", "http://localhost:8002", "http://[::1]:8002"):
        assert collaboration._private_channel(url)


async def test_changing_the_server_forgets_the_token(session):
    await _save(session, collaboration_server_url=URL, collaboration_token="t")
    assert (await _save(session, collaboration_server_url=URL)).has_collaboration_token
    local = await _save(session, collaboration_server_url="https://elsewhere.test")
    assert not local.has_collaboration_token


async def test_the_token_is_write_only(session):
    """Not in `GET /settings`, nor in the settings MCP tools are handed."""
    local = await _save(
        session, collaboration_server_url=URL, collaboration_token="secret-t"
    )
    assert "collaboration_token" not in local.model_dump(mode="json")
    assert "secret-t" not in repr(local)
    with api_client(session) as (client, _):
        res = await client.get("/settings")
        assert res.json()["has_collaboration_token"] is True
        assert "secret-t" not in res.text
        removed = await client.put("/settings", json={"collaboration_token": ""})
    assert removed.json()["has_collaboration_token"] is False
