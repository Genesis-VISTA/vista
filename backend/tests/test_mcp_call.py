"""`POST /projects/{name}/mcp/call`: a desktop tool for the researcher, off when hosted."""

import pytest

from harness.api import api_client
from vista_backend.api import mcp as mcp_api
from vista_backend.config import settings

pytestmark = pytest.mark.anyio


async def test_hosted_answers_404_before_looking_at_the_project(session, hosted):
    """Signed in, so the 404 is the hosted gate's, not the sign-in's 401."""
    from harness.tokens import bearer

    with api_client(session) as (client, _pool):
        response = await client.post(
            "/projects/anything/mcp/call",
            json={"name": "view", "arguments": {}},
            headers=bearer(hosted.token()),
        )
    assert response.status_code == 404


async def test_desktop_still_reaches_the_project(session, monkeypatch):
    """Not 404 from the hosted gate: the project lookup answers instead."""
    monkeypatch.setattr(settings, "deployment", "desktop")
    seen = []

    async def get_project_by_name(_session, name):
        seen.append(name)
        raise mcp_api.HTTPException(status_code=418, detail="reached")

    monkeypatch.setattr(
        mcp_api.project_service, "get_project_by_name", get_project_by_name
    )
    with api_client(session) as (client, _pool):
        response = await client.post(
            "/projects/anything/mcp/call", json={"name": "view", "arguments": {}}
        )
    assert seen == ["anything"]
    assert response.status_code == 418
