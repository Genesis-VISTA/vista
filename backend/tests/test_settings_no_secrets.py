"""`GET`/`PUT /settings` never return a credential, only whether each is saved."""

import pytest

from harness.api import api_client
from vista_backend.db.schemas import SECRET_SETTINGS_FIELDS
from vista_backend.services import local_settings

pytestmark = pytest.mark.anyio


def _value(field: str) -> str:
    return f"secret-value-of-{field}-7d41"


async def test_no_response_body_contains_a_saved_credential(session):
    values = {field: _value(field) for field in SECRET_SETTINGS_FIELDS}
    with api_client(session) as (client, _pool):
        put = await client.put("/settings", json=values)
        got = await client.get("/settings")
    for response in (put, got):
        assert response.status_code == 200
        body = response.json()
        for field, value in values.items():
            assert value not in response.text, field
            assert field not in body, field
            assert body[f"has_{field}"] is True, field


async def test_unset_credentials_report_false(session):
    with api_client(session) as (client, _pool):
        body = (await client.get("/settings")).json()
    for field in SECRET_SETTINGS_FIELDS:
        assert body[f"has_{field}"] is False, field


async def test_omitting_keeps_and_empty_clears(session):
    keep, clear = "odo_s3m_token", "frontier_s3m_token"
    with api_client(session) as (client, _pool):
        await client.put("/settings", json={keep: "a", clear: "b"})
        body = (await client.put("/settings", json={"display_name": "Ada"})).json()
        assert body[f"has_{keep}"] and body[f"has_{clear}"]
        body = (await client.put("/settings", json={clear: ""})).json()
        assert body[f"has_{keep}"] is True
        assert body[f"has_{clear}"] is False


async def test_agents_still_read_the_values(session):
    """Only the response drops them: tools are handed the decrypted row."""
    with api_client(session) as (client, _pool):
        await client.put("/settings", json={"odo_s3m_token": "tok-123"})
    public = await local_settings.get_public(session)
    assert public.odo_s3m_token == "tok-123"
    assert public.model_dump(mode="json")["odo_s3m_token"] == "tok-123"
