"""
HTTP contract for `GET /projects/{name}/models` (model-picker change).

The unit tests below exercise the route in-process against the real FastAPI
app and a real DB session, the same way `test_agent_api.py` does, with the
outbound call to the inference gateway faked via `httpx.MockTransport` so
`raise_for_status()`/`.json()` behave exactly as they would against a real
`httpx.Response` -- only the network hop is fake. One `live` test at the
bottom hits the real, running backend and a real configured gateway; it is
skipped unless `VISTA_RUN_LIVE=1` (see `tests/conftest.py`), following the
pattern in `tests/live/test_golden_prompts.py`.
"""

from __future__ import annotations

import os

import httpx
import pytest
from harness import api_client, seed_project, seed_user

from vista_backend.agents.inference import SETTINGS_LOCATION, rejected_credential_detail
from vista_backend.api import models as models_api
from vista_backend.config import settings
from vista_backend.db.schemas import UserSelfUpdate
from vista_backend.services import user as user_service

pytestmark = pytest.mark.anyio

MODELS = "/projects/{name}/models"


def _headers(user) -> dict[str, str]:
    return {"X-Vista-User-Email": user.email}


# Captured before any test monkeypatches `models_api.httpx.AsyncClient` --
# `models_api.httpx` is the real `httpx` module object, so patching its
# `AsyncClient` attribute would otherwise make the factory below call itself.
_RealAsyncClient = httpx.AsyncClient


def _mock_client(handler):
    """
    A drop-in for `httpx.AsyncClient` whose transport is faked.

    Keeps `raise_for_status()` and `.json()` behaving exactly as they would
    against a real response -- only the network hop is replaced -- rather
    than hand-rolling a fake response object.
    """

    def factory(*, base_url: str, timeout: float | None = None):
        return _RealAsyncClient(
            base_url=base_url, transport=httpx.MockTransport(handler)
        )

    return factory


async def _with_credential(session, user, *, model: str = "openai:claude-sonnet"):
    return await user_service.update_user(
        session,
        user.id,
        UserSelfUpdate(inference_model=model, inference_api_key="row-secret-key"),
        user,
    )


# ---------------------------------------------------------------------------
# Successful listing
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_lists_models_from_the_configured_endpoint(session, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:claude-sonnet")

    alice = await seed_user(session)
    project = await seed_project(session, alice)
    await _with_credential(session, alice)

    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-sonnet", "object": "model", "owned_by": "openai"},
                    {"id": "claude-opus", "object": "model", "owned_by": "openai"},
                ]
            },
        )

    monkeypatch.setattr(models_api.httpx, "AsyncClient", _mock_client(handler))

    with api_client(session) as (client, _):
        response = await client.get(
            MODELS.format(name=project.name), headers=_headers(alice)
        )

    assert response.status_code == 200
    assert response.json() == {
        "supported": True,
        "models": [
            {"id": "claude-sonnet", "owned_by": "openai"},
            {"id": "claude-opus", "owned_by": "openai"},
        ],
    }
    assert len(seen_requests) == 1
    assert seen_requests[0].url.path == "/v1/models"
    assert seen_requests[0].headers["authorization"] == "Bearer row-secret-key"


# ---------------------------------------------------------------------------
# No credential configured
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_no_credential_reports_missing_credential(session, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:claude-sonnet")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the gateway with no credential")

    monkeypatch.setattr(models_api.httpx, "AsyncClient", _mock_client(handler))

    alice = await seed_user(session)
    project = await seed_project(session, alice)

    with api_client(session) as (client, _):
        response = await client.get(
            MODELS.format(name=project.name), headers=_headers(alice)
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert SETTINGS_LOCATION in detail
    assert "claude-sonnet" in detail


# ---------------------------------------------------------------------------
# Provider outside the configured-endpoint set
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_unconfigured_provider_reports_unsupported(session, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call a provider outside our endpoint")

    monkeypatch.setattr(models_api.httpx, "AsyncClient", _mock_client(handler))

    alice = await seed_user(session)
    project = await seed_project(session, alice)
    await user_service.update_user(
        session,
        alice.id,
        UserSelfUpdate(inference_model="anthropic:claude-sonnet-4-5"),
        alice,
    )

    with api_client(session) as (client, _):
        response = await client.get(
            MODELS.format(name=project.name), headers=_headers(alice)
        )

    assert response.status_code == 200
    assert response.json() == {"supported": False, "models": []}


# ---------------------------------------------------------------------------
# Gateway rejects a present-but-wrong credential
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_rejected_credential_is_reported(session, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:claude-sonnet")

    alice = await seed_user(session)
    project = await seed_project(session, alice)
    await _with_credential(session, alice)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_api_key"})

    monkeypatch.setattr(models_api.httpx, "AsyncClient", _mock_client(handler))

    with api_client(session) as (client, _):
        response = await client.get(
            MODELS.format(name=project.name), headers=_headers(alice)
        )

    assert response.status_code == 409
    assert response.json()["detail"] == rejected_credential_detail(
        "openai:claude-sonnet"
    )


# ---------------------------------------------------------------------------
# Access control, matching every other project-scoped route
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_missing_project_is_404(session, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    alice = await seed_user(session)

    with api_client(session) as (client, _):
        response = await client.get(
            MODELS.format(name="no-such-project"), headers=_headers(alice)
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


@pytest.mark.unit
async def test_non_member_cannot_list_models(session, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    alice = await seed_user(session)
    bob = await seed_user(session)
    project = await seed_project(session, alice, name="alices-project")

    with api_client(session) as (client, _):
        response = await client.get(
            MODELS.format(name=project.name), headers=_headers(bob)
        )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Live: the real, running backend against the real configured gateway
# ---------------------------------------------------------------------------

ADMIN_EMAIL = "vista-test-admin@americansciencecloud.org"
LIVE_PROJECT = "molten-salt"


def _live_base_url() -> str:
    return os.environ.get("VISTA_LIVE_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


@pytest.mark.live
def test_live_lists_models_from_the_real_gateway() -> None:
    """
    Matches this change's own feasibility check: a real key against the real
    AmSC gateway returns a non-empty, real model list. Skipped unless
    VISTA_RUN_LIVE=1 (conftest.py) against a stack with a working credential.
    """
    base = _live_base_url()
    headers = {"X-Vista-User-Email": ADMIN_EMAIL}
    try:
        with httpx.Client(base_url=base, timeout=30.0) as client:
            response = client.get(MODELS.format(name=LIVE_PROJECT), headers=headers)
    except httpx.HTTPError as exc:
        pytest.skip(f"backend unavailable at {base}: {exc}")

    if response.status_code == 409:
        pytest.skip(f"no inference credential configured on {base}")

    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True
    assert len(body["models"]) > 0
    assert all("id" in m for m in body["models"])
