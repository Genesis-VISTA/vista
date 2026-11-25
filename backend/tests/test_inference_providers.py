"""
The hosted `server` inference provider (openspec change `multi-user-hosted`,
design D10): offered and default only when hosted, Custom never, and an
availability probe of the server's endpoint that keeps MAG/OLCF working.
"""

import httpx
import pytest

from vista_backend.agents import inference
from vista_backend.config import settings
from vista_backend.services import inference_probe


@pytest.fixture(autouse=True)
def _fresh_probe():
    inference_probe.reset_for_tests()
    yield
    inference_probe.reset_for_tests()


@pytest.fixture
def server_endpoint(monkeypatch):
    """A fake model server; `state["up"]` flips it, `state["calls"]` counts probes."""
    state = {"up": True, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if not state["up"]:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    real = httpx.AsyncClient

    def factory(*, timeout=None, **_):
        return real(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(inference_probe.httpx, "AsyncClient", factory)
    monkeypatch.setattr(settings, "server_inference_url", "http://beam.test/v1")
    monkeypatch.setattr(settings, "server_inference_model", "beam-model")
    return state


class _Local:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_desktop_never_offers_the_server_provider(monkeypatch):
    monkeypatch.setattr(settings, "deployment", "desktop")
    assert "server" not in inference.offered_presets()
    # A row naming it is ignored: the default provider answers.
    target = inference.resolve_inference_target(_Local(inference_provider="server"))
    assert target.provider != "server"


def test_hosted_defaults_to_the_server_model(monkeypatch, server_endpoint):
    monkeypatch.setattr(settings, "deployment", "hosted")
    assert "custom" not in inference.offered_presets()
    for local in (None, _Local(), _Local(inference_provider="custom")):
        target = inference.resolve_inference_target(local)
        assert target.provider == "server"
        assert target.base_url == "http://beam.test/v1"
        assert target.model == "openai:beam-model"
        assert target.has_credential  # the server's key is optional


@pytest.mark.anyio
async def test_with_no_model_name_set_the_server_uses_the_one_it_lists(
    monkeypatch, server_endpoint
):
    """vLLM serves one model under its `--served-model-name` (Beam: `beam`): no setting needed."""
    monkeypatch.setattr(settings, "deployment", "hosted")
    monkeypatch.setattr(settings, "server_inference_model", "")
    assert inference.server_model_name() is None  # not probed yet
    await inference_probe.server_model_status()
    assert inference.server_model_name() == "m"  # the fake endpoint lists `m`
    assert inference.resolve_inference_target(_Local()).model == "openai:m"


@pytest.mark.anyio
async def test_listing_models_while_the_server_is_down_is_503_not_500(
    session, hosted, server_endpoint
):
    from harness import api_client
    from harness.tokens import bearer

    server_endpoint["up"] = False
    with api_client(session) as (client, _):
        me = bearer(hosted.token(sub="alice"))
        assert (await client.get("/projects", headers=me)).status_code == 200
        r = await client.get("/projects/hello-olcf/models", headers=me)
    assert r.status_code == 503
    assert r.json()["code"] == "server_model_unavailable"
    assert "not answering" in r.json()["detail"]


def test_hosted_users_keep_their_own_mag_key(monkeypatch):
    monkeypatch.setattr(settings, "deployment", "hosted")
    local = _Local(inference_provider="mag", inference_mag_api_key="sk-mine")
    target = inference.resolve_inference_target(local)
    assert (target.provider, target.api_key) == ("mag", "sk-mine")


@pytest.mark.anyio
async def test_probe_is_cached_and_reports_the_reason(server_endpoint):
    assert (await inference_probe.server_model_status()).available
    await inference_probe.server_model_status()
    assert server_endpoint["calls"] == 1
    inference_probe.reset_for_tests()
    server_endpoint["up"] = False
    status = await inference_probe.server_model_status()
    assert not status.available and "not answering" in status.reason
    with pytest.raises(inference_probe.ServerModelUnavailable):
        await inference_probe.require_server_model("server")
    await inference_probe.require_server_model("mag")  # others are unaffected


@pytest.mark.anyio
async def test_a_debate_or_slack_start_is_refused_while_the_server_model_is_down(
    monkeypatch, server_endpoint
):
    """`require_model_reachable`, which debates and Slack check before a turn."""
    monkeypatch.setattr(settings, "deployment", "hosted")
    target = await inference_probe.require_model_reachable(_Local(), None)
    assert target.provider == "server"
    inference_probe.reset_for_tests()
    server_endpoint["up"] = False
    with pytest.raises(inference.ServerModelUnavailable) as refused:
        await inference_probe.require_model_reachable(_Local(), None)
    assert "not answering" in inference.describe_run_failure(refused.value)
    mine = _Local(inference_provider="mag", inference_mag_api_key="sk-mine")
    assert (await inference_probe.require_model_reachable(mine, None)).provider == "mag"


@pytest.mark.anyio
async def test_the_picker_view_marks_the_server_model_unavailable(
    session, hosted, server_endpoint
):
    from harness import api_client
    from harness.tokens import bearer

    headers = bearer(hosted.token())
    server_endpoint["up"] = False
    with api_client(session) as (client, _):
        got = (await client.get("/settings/inference", headers=headers)).json()
    by_id = {p["id"]: p for p in got["providers"]}
    assert got["provider"] == "server"
    assert by_id["server"]["available"] is False
    assert by_id["server"]["unavailable_reason"]
    assert by_id["mag"]["available"] is True and "custom" not in by_id
