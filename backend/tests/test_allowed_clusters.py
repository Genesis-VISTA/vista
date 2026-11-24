"""
A hosted deployment supports only its allowed clusters, Odo by default (openspec
change `multi-user-hosted`, spec `hpc-availability`): status, settings saves,
Globus logins, the S3M enclave check, the MCP metadata and the agent's rule.
"""

import httpx
import pytest

from vista_backend.agents.agents import clusters_rule
from vista_backend.config import settings
from vista_backend.services import hpc_status
from vista_backend.services.hosted import allowed_clusters, restricted_clusters

from test_hosted_refusals import _request, hosted as _hosted_fixture

hosted = _hosted_fixture  # the signed-in hosted deployment, as a fixture here


@pytest.fixture
def s3m(monkeypatch):
    """A faked S3M introspection endpoint; set `answer` to a dict, a status, or None (unreachable)."""
    box = {"answer": {"securityEnclave": "open"}}

    def handler(request: httpx.Request) -> httpx.Response:
        a = box["answer"]
        if a is None:
            raise httpx.ConnectError("down")
        if isinstance(a, int):
            return httpx.Response(a)
        return httpx.Response(200, json={"token": a})

    monkeypatch.setattr(
        hpc_status.hpc_status_service, "_transport", httpx.MockTransport(handler)
    )
    return box


def test_the_default_is_odo_when_hosted_and_everything_on_desktop(monkeypatch):
    monkeypatch.setattr(settings, "allowed_clusters", None)
    monkeypatch.setattr(settings, "deployment", "desktop")
    assert allowed_clusters() == ["odo", "frontier", "lux", "perlmutter"]
    assert restricted_clusters() is None
    assert clusters_rule() is None
    monkeypatch.setattr(settings, "deployment", "hosted")
    assert allowed_clusters() == ["odo"]
    assert restricted_clusters() == ["odo"]
    rule = clusters_rule()
    assert rule and "Odo" in rule and "Frontier" not in rule


def test_a_configured_list_restricts_a_desktop_too(monkeypatch):
    monkeypatch.setattr(settings, "allowed_clusters", ["odo", "frontier"])
    assert restricted_clusters() == ["odo", "frontier"]


@pytest.mark.anyio
async def test_the_version_and_status_list_odo_only(session, hosted, s3m):
    s3m["answer"] = 503
    got = (await _request(session, "GET", "/version")).json()
    assert got["allowed_clusters"] == ["odo"]
    status = (await _request(session, "GET", "/settings/hpc-status")).json()
    assert [c["cluster"] for c in status["clusters"]] == ["odo"]
    one = await _request(session, "GET", "/settings/hpc-status?cluster=frontier")
    assert one.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field", ["frontier_s3m_token", "lux_remote_dir", "frontier_globus_token"]
)
async def test_a_credential_for_another_cluster_is_refused(
    session,
    hosted,
    field,
):
    r = await _request(session, "PUT", "/settings", json={field: "/x"})
    assert r.status_code == 403
    assert "only Odo available" in r.json()["detail"]


@pytest.mark.anyio
async def test_the_shared_globus_pair_is_refused(session, hosted):
    r = await _request(session, "PUT", "/settings", json={"globus_token": "t"})
    assert r.status_code == 403
    assert "shared" in r.json()["detail"]


@pytest.mark.anyio
@pytest.mark.parametrize("cluster", ["frontier", "lux"])
async def test_a_globus_login_for_another_cluster_is_refused(
    session,
    hosted,
    cluster,
):
    r = await _request(session, "POST", f"/settings/globus/{cluster}/login")
    assert r.status_code == 403


@pytest.mark.anyio
async def test_an_open_enclave_s3m_token_is_saved(session, hosted, s3m):
    r = await _request(session, "PUT", "/settings", json={"odo_s3m_token": "tok"})
    assert r.status_code == 200
    assert r.json()["has_odo_s3m_token"] is True


@pytest.mark.anyio
async def test_a_moderate_enclave_token_is_not_stored(session, hosted, s3m):
    s3m["answer"] = {"securityEnclave": "moderate"}
    r = await _request(session, "PUT", "/settings", json={"odo_s3m_token": "tok2"})
    assert r.status_code == 422
    assert "open-enclave" in r.json()["detail"]
    got = (await _request(session, "GET", "/settings")).json()
    assert got["has_odo_s3m_token"] is False


@pytest.mark.anyio
async def test_an_unreachable_s3m_means_the_token_is_not_stored(
    session,
    hosted,
    s3m,
):
    s3m["answer"] = None
    r = await _request(session, "PUT", "/settings", json={"odo_s3m_token": "tok3"})
    assert r.status_code == 422
    assert "could not be checked" in r.json()["detail"]
    got = (await _request(session, "GET", "/settings")).json()
    assert got["has_odo_s3m_token"] is False
