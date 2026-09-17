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
    monkeypatch.setattr(
        type(settings),
        "vista_globus_collection_id",
        property(lambda self: "vista-gcs-id"),
    )
    monkeypatch.setattr(settings, "odo_globus_collection_id", "odo-collection")

    async def _noop_access(cfg, cluster):
        return None

    async def _odo_iri(*, iri_token: str):
        return FakeIriClient(job_id="odo-token-test")

    async def _collection(_globus, _cluster):
        return settings.vista_globus_collection_id

    monkeypatch.setattr(submit_job_mcp, "_require_olcf_access", _noop_access)
    monkeypatch.setattr(submit_job_mcp, "create_odo_iri_client", _odo_iri)
    monkeypatch.setattr(submit_job_mcp, "ensure_local_collection", _collection)


def _capture_globus(monkeypatch) -> list[str]:
    """Patch `create_globus_client` to record the refresh token it is handed,
    rather than discarding it the way `test_submit_job_spec.py`'s stub does --
    that is exactly the gap this test closes."""
    calls: list[str] = []
    globus = FakeGlobusClient()
    globus.seed_odo_out_dir("/fake/odo/vista")

    def _create(*, refresh_token: str):
        calls.append(refresh_token)
        return globus

    monkeypatch.setattr(submit_job_mcp, "create_globus_client", _create)
    return calls


async def _submit(cfg: UserConfig) -> None:
    await _submit_odo_job(
        cfg, "example", node_count=None, duration_int=None, script_args=None
    )


async def test_the_researchers_own_cluster_token_wins(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "deployment-odo")
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(
        odo_s3m_token="s3m", odo_globus_token="mine-odo", globus_token="mine-shared"
    )

    await _submit(cfg)

    assert calls == ["mine-odo"]


async def test_the_shared_token_is_the_second_choice(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "deployment-odo")
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(odo_s3m_token="s3m", globus_token="mine-shared")

    await _submit(cfg)

    assert calls == ["mine-shared"]


async def test_the_deployment_token_is_the_last_resort(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "deployment-odo")
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(odo_s3m_token="s3m")

    await _submit(cfg)

    assert calls == ["deployment-odo"]


async def test_nothing_configured_refuses_before_touching_globus(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", None)
    calls = _capture_globus(monkeypatch)
    cfg = UserConfig(odo_s3m_token="s3m")

    with pytest.raises(ToolError) as refusal:
        await _submit(cfg)

    assert calls == []
    assert "settings" in str(refusal.value).lower()
