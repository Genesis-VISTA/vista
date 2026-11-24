"""
`CurrentUser`: who a request is from (multi-user-hosted, task 4; spec
`user-accounts`). Fails until `services/current_user.py` is implemented and
attached router-wide in `api/api.py`.
"""

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from harness.api import api_client
from harness.tokens import FakeIssuer, bearer
from vista_backend.db.db import _get_session
from vista_backend.db.schemas import LOCAL_USER_ID, UserTable
from vista_backend.services.current_user import CurrentUser

pytestmark = pytest.mark.anyio


@pytest.fixture
async def whoami(session):
    """A one-route app that answers with the current user, on the test's database."""
    app = FastAPI()

    @app.get("/whoami")
    async def _whoami(user: CurrentUser) -> dict:
        return {
            "id": user.id,
            "sub": user.sub,
            "name": user.display_name,
            "email": user.email,
        }

    async def override_session():
        yield session

    app.dependency_overrides[_get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as http:
        yield http


# --- Desktop -----------------------------------------------------------------


async def test_desktop_needs_no_token(whoami):
    """Scenario: Desktop needs no token."""
    r = await whoami.get("/whoami")
    assert r.status_code == 200
    assert r.json()["id"] == LOCAL_USER_ID


async def test_desktop_ignores_a_token(whoami, issuer):
    """A desktop install has one user; a stray token changes nothing."""
    r = await whoami.get("/whoami", headers=bearer(issuer.token()))
    assert r.json()["id"] == LOCAL_USER_ID


# --- Hosted: refusals ----------------------------------------------------------


async def test_hosted_without_a_token_is_401(whoami, hosted):
    """Scenario: Hosted without a token."""
    r = await whoami.get("/whoami")
    assert r.status_code == 401


async def test_hosted_expired_token_is_401(whoami, hosted):
    r = await whoami.get("/whoami", headers=bearer(hosted.token(expires_in=-120)))
    assert r.status_code == 401


async def test_hosted_token_from_another_issuer_is_401(whoami, hosted):
    stranger = FakeIssuer(issuer="https://elsewhere.test/realms/other")
    r = await whoami.get("/whoami", headers=bearer(stranger.token()))
    assert r.status_code == 401


async def test_hosted_token_signed_by_another_key_is_401(whoami, hosted):
    forger = FakeIssuer()  # same issuer name, different key
    r = await whoami.get("/whoami", headers=bearer(forger.token()))
    assert r.status_code == 401


async def test_an_id_token_is_not_an_access_token(whoami, hosted):
    """Scenario: An ID token is not an access token."""
    r = await whoami.get("/whoami", headers=bearer(hosted.token(typ="ID")))
    assert r.status_code == 401


async def test_another_client_is_401(whoami, hosted):
    r = await whoami.get(
        "/whoami", headers=bearer(hosted.token(azp="some-other-client"))
    )
    assert r.status_code == 401


async def test_signed_in_without_the_role_is_403(whoami, hosted):
    """Scenario: Signed in without the role."""
    r = await whoami.get(
        "/whoami", headers=bearer(hosted.token(roles=["publish-only"]))
    )
    assert r.status_code == 403


async def test_unreachable_keys_are_503(whoami, hosted, monkeypatch):
    """An unreachable key endpoint answers 503: it neither admits nor refuses anyone."""
    from harness.tokens import AZP, ISSUER, ROLE
    from vista_backend.services import current_user

    def down(self, *a, **kw):
        raise jwt.PyJWKClientConnectionError("realm unreachable")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", down)
    fetching = current_user.TokenVerifier(ISSUER, [AZP], ROLE)  # no keys: it must fetch
    monkeypatch.setattr(current_user, "get_verifier", lambda: fetching)
    r = await whoami.get("/whoami", headers=bearer(hosted.token()))
    assert r.status_code == 503


# --- Hosted: users --------------------------------------------------------------


async def test_first_sign_in_creates_the_user_once(whoami, hosted, session):
    """Scenario: First and second request."""
    token = hosted.token(sub="alice-sub", name="Alice", email="alice@ornl.gov")
    first = await whoami.get("/whoami", headers=bearer(token))
    second = await whoami.get("/whoami", headers=bearer(token))
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] != LOCAL_USER_ID
    assert first.json()["name"] == "Alice"
    rows = (
        await session.exec(select(UserTable).where(UserTable.sub == "alice-sub"))
    ).all()
    assert len(rows) == 1


async def test_name_and_email_follow_the_token(whoami, hosted):
    await whoami.get("/whoami", headers=bearer(hosted.token(sub="bob", name="Bob")))
    r = await whoami.get(
        "/whoami",
        headers=bearer(hosted.token(sub="bob", name="Robert", email="rob@ornl.gov")),
    )
    assert r.json()["name"] == "Robert"
    assert r.json()["email"] == "rob@ornl.gov"


async def test_two_subjects_are_two_users(whoami, hosted):
    a = await whoami.get("/whoami", headers=bearer(hosted.token(sub="alice")))
    b = await whoami.get("/whoami", headers=bearer(hosted.token(sub="bob")))
    assert a.json()["id"] != b.json()["id"]


async def test_local_never_matches_a_token(whoami, hosted):
    """`local` has no subject: a token whose sub is "local" is a new, different user."""
    r = await whoami.get("/whoami", headers=bearer(hosted.token(sub=LOCAL_USER_ID)))
    assert r.status_code == 200
    assert r.json()["id"] != LOCAL_USER_ID


async def test_a_first_sign_in_is_committed_before_the_route_runs(session, hosted):
    """
    The request's session lives until the response is sent (a chat stream can
    last minutes), so the user's write must not wait for it: another session
    sees the row while the route is still running.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    app = FastAPI()

    @app.get("/peek")
    async def _peek(user: CurrentUser) -> dict:
        async with AsyncSession(session.bind) as other:
            seen = (
                await other.exec(select(UserTable).where(UserTable.sub == "carol"))
            ).first()
        return {"seen": seen is not None}

    async def override_session():
        yield session

    app.dependency_overrides[_get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as http:
        r = await http.get("/peek", headers=bearer(hosted.token(sub="carol")))
    assert r.json() == {"seen": True}


# --- The real app: attached router-wide ------------------------------------------


async def test_hosted_app_routes_need_a_token(session, hosted):
    """Every route but health and version resolves the user first (task 4.2)."""
    with api_client(session) as (http, _):
        assert (await http.get("/projects")).status_code == 401
        signed_in = await http.get("/projects", headers=bearer(hosted.token()))
        assert signed_in.status_code == 200


async def test_hosted_version_needs_no_token(session, hosted):
    with api_client(session) as (http, _):
        assert (await http.get("/version")).status_code == 200


async def test_desktop_app_routes_need_no_token(session):
    with api_client(session) as (http, _):
        assert (await http.get("/projects")).status_code == 200
