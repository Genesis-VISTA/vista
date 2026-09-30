"""Credential resolution as a real job-submission call sees it.

`test_globus_token_resolution.py` proves `UserConfig.require_globus_token`'s
precedence directly, against the helper alone. This drives the same
precedence through `_submit_odo_job` -- the function a tool call actually
runs -- so a call site that got the token some other way, or an argument that
silently went missing on the way to `create_globus_client`, would show up
here even though the helper's own tests would stay green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as submit_job_mcp
from vista_mcp_server.config import settings
from vista_mcp_server.lib.types import GlobusTokens
from vista_mcp_server.lib.user_config import UserConfig
from vista_mcp_server.submit_job_mcp import _submit_odo_job
from fakes import FakeGlobusClient, FakeIriClient

REPO_ROOT = Path(__file__).resolve().parents[3]
HPC_JOBS_DIR = REPO_ROOT / "hpc_jobs"

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _odo_environment(monkeypatch):
    """Everything `_submit_odo_job` needs that is not the Globus credential
    itself, so each test below varies only the thing it is testing."""
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", HPC_JOBS_DIR)
    monkeypatch.setattr(settings, "odo_remote_dir", "/fake/odo/vista")
    monkeypatch.setattr(settings, "odo_account", "gen150-vista")
    monkeypatch.setattr(settings, "session_id", "test-session")
    monkeypatch.setattr(settings, "odo_globus_collection_id", "odo-collection")

    async def _noop_access(cfg, cluster, account=None):
        return None

    async def _odo_iri(*, iri_token: str):
        return FakeIriClient(job_id="odo-token-test")

    monkeypatch.setattr(submit_job_mcp, "_require_olcf_access", _noop_access)
    monkeypatch.setattr(submit_job_mcp, "create_odo_iri_client", _odo_iri)


def _capture_globus(monkeypatch) -> list[GlobusTokens]:
    """Patch `create_globus_client` to record the credential it is handed,
    rather than discarding it the way `test_submit_job_spec.py`'s stub does --
    that is exactly the gap this test closes."""
    calls: list[GlobusTokens] = []
    globus = FakeGlobusClient()
    globus.seed_odo_out_dir("/fake/odo/vista")

    def _create(*, tokens: GlobusTokens, cluster: str):
        calls.append(tokens)
        return globus

    monkeypatch.setattr(submit_job_mcp, "create_globus_client", _create)
    return calls


async def _submit(cfg: UserConfig) -> None:
    await _submit_odo_job(
        cfg, "example", node_count=None, duration_int=None, script_args=None
    )


@pytest.fixture
def deployment_vars(monkeypatch):
    """The variables that used to be a deployment-wide Globus login."""
    monkeypatch.setenv("VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN", "deployment-odo")
    monkeypatch.setenv(
        "VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN", "deployment-odo-https"
    )


async def test_the_researchers_own_cluster_token_wins(monkeypatch):
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(
        odo_s3m_token="s3m",
        odo_globus_token="mine-odo",
        odo_globus_https_token="mine-odo-https",
        globus_token="mine-shared",
        globus_https_token="mine-shared-https",
    )

    await _submit(cfg)

    assert calls == [GlobusTokens(transfer="mine-odo", https="mine-odo-https")]


async def test_the_shared_token_is_the_second_choice(monkeypatch):
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(
        odo_s3m_token="s3m",
        globus_token="mine-shared",
        globus_https_token="mine-shared-https",
    )

    await _submit(cfg)

    assert calls == [GlobusTokens(transfer="mine-shared", https="mine-shared-https")]


async def test_a_pre_https_connection_falls_through_to_the_shared_pair(monkeypatch):
    """A researcher who connected Odo before VISTA moved to the HTTPS interface
    has a Transfer token and nothing to read files with. Using it would list the
    output directory and fail on every file in it, so their complete shared
    pair is used instead."""
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(
        odo_s3m_token="s3m",
        odo_globus_token="stale-odo",
        globus_token="mine-shared",
        globus_https_token="mine-shared-https",
    )

    await _submit(cfg)

    assert calls == [GlobusTokens(transfer="mine-shared", https="mine-shared-https")]


async def test_nothing_connected_refuses_before_touching_globus(
    monkeypatch, deployment_vars
):
    """Refused even with the old deployment-wide variables set: they are not a
    source any more."""
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(odo_s3m_token="s3m")

    with pytest.raises(ToolError) as refusal:
        await _submit(cfg)

    assert calls == []
    assert "settings" in str(refusal.value).lower()
