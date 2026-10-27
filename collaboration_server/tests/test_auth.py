"""Authentication: Keycloak tokens in `oidc` mode, and the dev-headers stub."""

import pytest
from httpx import ASGITransport, AsyncClient

from vista_collaboration_server.api import app as app_module
from vista_collaboration_server.api.app import create_app, main
from vista_collaboration_server.config import CollabSettings
from vista_collaboration_server.services.identity import (
    INSTALL_ID_HEADER,
)
from vista_collaboration_server.testing import FakeIssuer

from conftest import INSTALL_ID

pytestmark = pytest.mark.anyio

SUB = "a5b1c0de-0000-4000-8000-000000000001"


@pytest.fixture
def oidc_settings(settings) -> CollabSettings:
    return settings.model_copy(update={"auth_mode": "oidc"})


@pytest.fixture
def issuer(oidc_settings) -> FakeIssuer:
    return FakeIssuer(oidc_settings)


@pytest.fixture
async def oidc_app(oidc_settings, issuer):
    app = create_app(oidc_settings, verifier=issuer.verifier())
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def http(oidc_app):
    async with AsyncClient(
        transport=ASGITransport(app=oidc_app), base_url="http://collab"
    ) as client:
        yield client


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create(http, headers):
    return await http.post(
        "/items",
        json={"kind": "skill", "name": "salt", "manifest": {"v": 1}},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# oidc
# ---------------------------------------------------------------------------


async def test_a_valid_token_is_the_user(http, issuer):
    res = await http.get("/me", headers=bearer(issuer.token()))
    assert res.status_code == 200, res.text
    assert res.json() == {"sub": SUB, "email": "ada@ornl.gov", "name": "Ada Lovelace"}


async def test_the_name_falls_back_to_the_username(http, issuer):
    res = await http.get("/me", headers=bearer(issuer.token(name=None)))
    assert res.json()["name"] == "ada"


async def test_a_write_records_who_made_it(http, issuer):
    res = await _create(
        http, {**bearer(issuer.token()), INSTALL_ID_HEADER: str(INSTALL_ID)}
    )
    assert res.status_code == 201, res.text
    item = res.json()
    assert item["created_by"] == SUB
    assert item["created_by_name"] == "Ada Lovelace"
    assert "created_by_email" not in item  # kept, not shown to other users
    assert item["created_by_install"] == str(INSTALL_ID)
    version = (
        await http.get(
            f"/items/{item['id']}/versions/1", headers=bearer(issuer.token())
        )
    ).json()
    assert version["created_by"] == SUB


async def test_an_id_token_is_not_an_access_token(http, issuer):
    res = await http.get("/me", headers=bearer(issuer.token(typ="ID")))
    assert res.status_code == 401


async def test_a_malformed_install_id_does_not_refuse_a_signed_in_caller(http, issuer):
    res = await _create(http, {**bearer(issuer.token()), INSTALL_ID_HEADER: "abc"})
    assert res.status_code == 201, res.text
    assert res.json()["created_by_install"] is None


async def test_the_install_id_is_not_read_before_the_token(http):
    res = await _create(http, {INSTALL_ID_HEADER: "abc"})
    assert res.status_code == 401


async def test_install_id_headers_alone_do_not_sign_in(http):
    res = await _create(http, {INSTALL_ID_HEADER: str(INSTALL_ID)})
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "path", ["/items", "/me", "/items/00000000-0000-4000-8000-000000000009"]
)
async def test_reads_need_a_token(http, path):
    assert (await http.get(path)).status_code == 401


async def test_version_needs_no_token(http):
    assert (await http.get("/version")).status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_in": -120},
        {"iss": "https://elsewhere.example/realms/obsidian"},
        {"azp": "some-other-client"},
    ],
    ids=["expired", "wrong-issuer", "wrong-client"],
)
async def test_a_token_this_server_does_not_accept_is_401(http, issuer, overrides):
    res = await http.get("/me", headers=bearer(issuer.token(**overrides)))
    assert res.status_code == 401, res.text


async def test_a_token_signed_by_another_key_is_401(http, oidc_settings):
    impostor = FakeIssuer(oidc_settings)  # same kid, different key
    res = await http.get("/me", headers=bearer(impostor.token()))
    assert res.status_code == 401


async def test_a_garbled_token_is_401(http):
    assert (await http.get("/me", headers=bearer("not.a.jwt"))).status_code == 401
    assert (
        await http.get("/me", headers={"Authorization": "Basic abc"})
    ).status_code == 401


async def test_a_token_without_the_role_is_403(http, issuer):
    res = await http.get("/me", headers=bearer(issuer.token(roles=["other"])))
    assert res.status_code == 403
    assert "not been given access" in res.json()["detail"]


async def test_another_client_is_accepted_when_allowed(oidc_settings):
    settings = oidc_settings.model_copy(
        update={"oidc_allowed_azp": ["apisix", "obsidian-public"]}
    )
    issuer = FakeIssuer(settings)
    app = create_app(settings, verifier=issuer.verifier())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://c"
        ) as http:
            res = await http.get(
                "/me", headers=bearer(issuer.token(azp="obsidian-public"))
            )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Fetching the realm's keys
# ---------------------------------------------------------------------------


def _fetching(monkeypatch, fetch):
    """Every `PyJWKClient` fetches with `fetch` instead of the network."""
    import jwt

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: fetch(self.uri))


async def _me_with_fetched_keys(oidc_settings, token):
    app = create_app(oidc_settings)  # its verifier fetches the realm's keys
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://c"
        ) as http:
            return await http.get("/me", headers=bearer(token))


async def test_keys_are_fetched_from_the_realm(oidc_settings, issuer, monkeypatch):
    asked: list[str] = []
    _fetching(monkeypatch, lambda uri: asked.append(uri) or issuer.jwks())
    res = await _me_with_fetched_keys(oidc_settings, issuer.token())
    assert res.status_code == 200, res.text
    assert asked == [
        "https://obsidian.ccs.ornl.gov/auth/realms/obsidian/protocol/openid-connect/certs"
    ]


async def test_an_unreachable_realm_is_503_not_a_bad_token(
    oidc_settings, issuer, monkeypatch
):
    """A Keycloak outage must not read as "get a new token"."""
    import jwt

    def down(uri):
        raise jwt.PyJWKClientConnectionError("no route")

    _fetching(monkeypatch, down)
    res = await _me_with_fetched_keys(oidc_settings, issuer.token())
    assert res.status_code == 503
    assert "cannot check tokens right now" in res.json()["detail"]


# ---------------------------------------------------------------------------
# dev-headers, root_path, startup
# ---------------------------------------------------------------------------


async def test_dev_headers_still_sign_in_by_install_id(anon):
    res = await anon.get("/me", headers={INSTALL_ID_HEADER: str(INSTALL_ID)})
    assert res.status_code == 200
    assert res.json()["sub"] == str(INSTALL_ID)


async def test_the_docs_link_under_the_proxys_prefix(app):
    """
    What uvicorn hands the app with `root_path` set, behind a proxy that
    strips the prefix: the prefix back on the path, and as `root_path`.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app, root_path="/vista/api"), base_url="http://c"
    ) as http:
        assert (await http.get("/vista/api/version")).status_code == 200
        spec = await http.get("/vista/api/openapi.json")
        assert spec.status_code == 200
        assert spec.json()["servers"] == [{"url": "/vista/api"}]


def _run_main(monkeypatch, **env) -> list:
    for key, value in env.items():
        monkeypatch.setenv(f"VISTA_COLLAB_{key.upper()}", value)
    ran: list = []
    monkeypatch.setattr(app_module.uvicorn, "run", lambda *a, **kw: ran.append(kw))
    main()
    return ran


def test_dev_headers_refuse_to_listen_beyond_loopback(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _run_main(
            monkeypatch,
            auth_mode="dev-headers",
            host="0.0.0.0",
            data_dir=str(tmp_path),
        )


def test_a_deployment_listens_with_tls(monkeypatch, tmp_path):
    ran = _run_main(
        monkeypatch,
        auth_mode="oidc",
        host="0.0.0.0",
        port="8001",
        data_dir=str(tmp_path),
        ssl_certfile=str(tmp_path / "server.crt"),
        ssl_keyfile=str(tmp_path / "server.key"),
        root_path="/vista/api",
    )
    assert ran[0]["host"] == "0.0.0.0" and ran[0]["port"] == 8001
    assert ran[0]["root_path"] == "/vista/api"
    assert ran[0]["ssl_certfile"] == tmp_path / "server.crt"


def test_half_a_tls_setting_refuses_to_start(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _run_main(
            monkeypatch,
            data_dir=str(tmp_path),
            ssl_certfile=str(tmp_path / "server.crt"),
        )


# ---------------------------------------------------------------------------
# The sign-in page
# ---------------------------------------------------------------------------


async def test_the_sign_in_page_shows_the_gateways_token_to_its_owner(http, issuer):
    token = issuer.token()
    res = await http.get("/sign-in", headers=bearer(token))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert token in res.text
    assert "Ada Lovelace" in res.text
    assert res.headers["cache-control"] == "no-store"
    csp = res.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp


async def test_the_sign_in_page_needs_the_gateways_token(http):
    assert (await http.get("/sign-in")).status_code == 401


async def test_the_sign_in_page_shows_no_token_without_access(http, issuer):
    token = issuer.token(roles=[])
    res = await http.get("/sign-in", headers=bearer(token))
    assert res.status_code == 403
    assert token not in res.text


async def test_the_sign_in_page_escapes_what_the_token_says(http, issuer):
    res = await http.get(
        "/sign-in", headers=bearer(issuer.token(name="<script>alert(1)</script>"))
    )
    assert "<script>alert(1)</script>" not in res.text
    assert "&lt;script&gt;" in res.text
