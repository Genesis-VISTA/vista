"""
Tests for the S3M token introspection gate (`lib/olcf_token`) and the
project-based cluster gating in `submit_job_mcp._default_cluster`.
"""

import asyncio

import httpx
import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.lib import olcf_token
from vista_mcp_server.lib.olcf_token import get_s3m_token_project, require_s3m_project

INTROSPECT_URL = "https://s3m.example.gov/introspect"


@pytest.fixture(autouse=True)
def clear_cache():
    olcf_token._introspect_cache.clear()
    yield
    olcf_token._introspect_cache.clear()


@pytest.fixture
def mock_introspect(monkeypatch):
    """
    Replace httpx.AsyncClient inside olcf_token with one backed by a
    MockTransport. Returns a state dict: set `project` / `status_code` to shape
    the response, read `calls` to count round trips.
    """
    state = {"project": "gen150-vista", "status_code": 200, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(
            state["status_code"],
            json={"token": {"project": state["project"]}},
        )

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(olcf_token.httpx, "AsyncClient", client_factory)
    return state


def test_get_project(mock_introspect):
    project = asyncio.run(get_s3m_token_project("tok", introspect_url=INTROSPECT_URL))
    assert project == "gen150-vista"


def test_get_project_cached(mock_introspect):
    async def twice():
        await get_s3m_token_project("tok", introspect_url=INTROSPECT_URL)
        return await get_s3m_token_project("tok", introspect_url=INTROSPECT_URL)

    assert asyncio.run(twice()) == "gen150-vista"
    assert mock_introspect["calls"] == 1


def test_get_project_distinct_tokens_not_shared(mock_introspect):
    async def both():
        await get_s3m_token_project("tok-a", introspect_url=INTROSPECT_URL)
        await get_s3m_token_project("tok-b", introspect_url=INTROSPECT_URL)

    asyncio.run(both())
    assert mock_introspect["calls"] == 2


def test_get_project_http_error(mock_introspect):
    mock_introspect["status_code"] = 401
    with pytest.raises(ToolError, match="expired or invalid"):
        asyncio.run(get_s3m_token_project("tok", introspect_url=INTROSPECT_URL))


def test_get_project_missing_claim(mock_introspect):
    mock_introspect["project"] = None
    with pytest.raises(ToolError, match="no project claim"):
        asyncio.run(get_s3m_token_project("tok", introspect_url=INTROSPECT_URL))


def test_require_project_match(mock_introspect):
    asyncio.run(
        require_s3m_project(
            "tok",
            "gen150-vista",
            cluster="odo",
            introspect_url=INTROSPECT_URL,
        )
    )


def test_require_project_mismatch(mock_introspect):
    mock_introspect["project"] = "chm243"
    with pytest.raises(ToolError, match="'chm243'.*odo.*'gen150-vista'"):
        asyncio.run(
            require_s3m_project(
                "tok",
                "gen150-vista",
                cluster="odo",
                introspect_url=INTROSPECT_URL,
            )
        )


class TestDefaultCluster:
    """Per-cluster-token routing in submit_job_mcp._default_cluster."""

    @pytest.fixture
    def patched(self):
        # amscrot is lazy-imported inside IriClient; importing submit_job_mcp
        # no longer requires the private SDK (hermetic CI installs without `hpc`).
        from vista_mcp_server import submit_job_mcp
        from vista_mcp_server.lib.user_config import UserConfig

        return submit_job_mcp, UserConfig

    def test_odo_token(self, patched):
        submit_job_mcp, UserConfig = patched
        cfg = UserConfig(odo_s3m_token="tok")
        assert submit_job_mcp._default_cluster(cfg) == "odo"

    def test_frontier_token(self, patched):
        submit_job_mcp, UserConfig = patched
        cfg = UserConfig(frontier_s3m_token="tok")
        assert submit_job_mcp._default_cluster(cfg) == "frontier"

    def test_multiple_clusters(self, patched):
        submit_job_mcp, UserConfig = patched
        cfg = UserConfig(odo_s3m_token="tok", nersc_iri_token="ntok")
        with pytest.raises(ToolError, match="Multiple HPC clusters"):
            submit_job_mcp._default_cluster(cfg)

    def test_both_olcf_tokens(self, patched):
        submit_job_mcp, UserConfig = patched
        cfg = UserConfig(odo_s3m_token="tok", frontier_s3m_token="tok2")
        with pytest.raises(ToolError, match="Multiple HPC clusters"):
            submit_job_mcp._default_cluster(cfg)

    def test_nersc_only(self, patched):
        submit_job_mcp, UserConfig = patched
        cfg = UserConfig(nersc_iri_token="ntok")
        assert submit_job_mcp._default_cluster(cfg) == "perlmutter"

    def test_nothing_configured(self, patched):
        submit_job_mcp, UserConfig = patched
        cfg = UserConfig()
        with pytest.raises(ToolError, match="No HPC cluster configured"):
            submit_job_mcp._default_cluster(cfg)
