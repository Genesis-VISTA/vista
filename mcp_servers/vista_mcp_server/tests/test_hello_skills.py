"""The bundled hello skills (`hello-odo`, `hello-frontier`, `hello-lux`) through the
submit tool: dry-run acceptance, what is uploaded, and the refusals the skills tell the
agent to relay. IRI, Globus and SSH are faked; nothing reaches a cluster.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server import dry_run
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeGlobusClient, FakeIriClient, FakeSshConn

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

SKILLS = (
    Path(__file__).resolve().parents[3]
    / "backend"
    / "src"
    / "vista_backend"
    / "db"
    / "skills"
)
FRONTIER = "/fake/frontier/vista"
LUX_BASE = "/lustre/orion/abc123/proj-shared/vista"


def _ctx(user: UserConfig):
    vista = {"user": user.model_dump(), "project_paths": {"skills_dir": str(SKILLS)}}
    return SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista))
    )


@pytest.fixture(autouse=True)
def _clean_dry_registry():
    dry_run.reset()
    yield
    dry_run.reset()


def _patch_frontier(monkeypatch, *, iri, globus):
    async def _introspect(token, *, introspect_url):
        return "abc123"

    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)
    monkeypatch.setattr(m, "create_globus_client", lambda **kwargs: globus)
    monkeypatch.setattr(m, "get_s3m_token_project", _introspect)
    monkeypatch.setattr(
        settings, "frontier_globus_collection_id", "frontier-collection"
    )


FRONTIER_CFG = UserConfig(
    frontier_s3m_token="frontier-token",
    frontier_remote_dir=FRONTIER,
    globus_token="fake-transfer-refresh",
    globus_https_token="fake-https-refresh",
)


async def test_hello_frontier_is_accepted_by_the_dry_run_backend(monkeypatch):
    monkeypatch.setattr(settings, "hpc_dry_run", True)
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 0.0)

    summary = await m.submit_hpc_job(
        _ctx(UserConfig()), job="hello-frontier/hello", cluster="frontier"
    )
    assert "job_id: dry-" in summary
    assert "cluster: frontier" in summary

    job_id = summary.splitlines()[0].split(": ")[1]
    status = await m.get_hpc_job_status(_ctx(UserConfig()), job_id, "frontier")
    assert "COMPLETED" in status


async def test_hello_frontier_uploads_its_input_and_sees_run_dir(monkeypatch):
    monkeypatch.setattr(settings, "hpc_dry_run", False)
    iri = FakeIriClient(job_id="fr-1")
    globus = FakeGlobusClient()
    globus.seed_remote_dir(FRONTIER)
    _patch_frontier(monkeypatch, iri=iri, globus=globus)

    summary = await m.submit_hpc_job(
        _ctx(FRONTIER_CFG), job="hello-frontier/hello", cluster="frontier"
    )
    assert "job_id: fr-1" in summary

    # Only input.txt is uploaded: README, defaults and the script are VISTA's own.
    [(_, remote)] = globus.uploads
    assert ".jobs/hello-frontier/hello/" in remote
    assert remote.endswith("/input.txt")
    local = SKILLS / "hello-frontier" / "hpc_jobs" / "hello" / "input.txt"
    assert globus.files[remote] == local.read_bytes()

    [(spec, name)] = iri.submitted
    assert name == "vista-hello-frontier/hello"
    env = spec["environment"]
    assert env["RUN_DIR_Frontier"] == remote.removesuffix("/input.txt")
    job_cmd = spec["arguments"][2]
    script = SKILLS / "hello-frontier" / "hpc_jobs" / "hello" / "job.frontier.slurm"
    assert script.read_text(encoding="utf-8") in job_cmd
    assert spec["attributes"]["account"] == "abc123"


async def test_hello_odo_sees_run_dir(monkeypatch):
    monkeypatch.setattr(settings, "hpc_dry_run", False)
    monkeypatch.setattr(settings, "odo_globus_collection_id", "odo-collection")
    odo = "/fake/odo/vista"
    iri = FakeIriClient(job_id="odo-1")
    globus = FakeGlobusClient()
    globus.seed_remote_dir(odo)

    async def _introspect(token, *, introspect_url):
        return "abc123"

    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)
    monkeypatch.setattr(m, "create_globus_client", lambda **kwargs: globus)
    monkeypatch.setattr(m, "get_s3m_token_project", _introspect)
    cfg = UserConfig(
        odo_s3m_token="odo-token",
        odo_remote_dir=odo,
        globus_token="t",
        globus_https_token="h",
    )

    await m.submit_hpc_job(_ctx(cfg), job="hello-odo/hello", cluster="odo")

    [(_, remote)] = globus.uploads
    assert remote.endswith("/input.txt")
    [(spec, _)] = iri.submitted
    assert spec["environment"]["RUN_DIR_Odo"] == remote.removesuffix("/input.txt")


async def test_frontier_without_globus_is_refused_and_nothing_is_submitted(
    monkeypatch,
):
    monkeypatch.setattr(settings, "hpc_dry_run", False)
    iri = FakeIriClient()
    globus = FakeGlobusClient()
    _patch_frontier(monkeypatch, iri=iri, globus=globus)
    cfg = UserConfig(frontier_s3m_token="frontier-token", frontier_remote_dir=FRONTIER)

    with pytest.raises(
        ToolError, match="Globus file transfer is not connected for Frontier"
    ):
        await m.submit_hpc_job(
            _ctx(cfg), job="hello-frontier/hello", cluster="frontier"
        )
    assert iri.submitted == []
    assert globus.uploads == []


async def test_hello_lux_without_a_lux_account_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hpc_dry_run", False)
    conn = FakeSshConn(tmp_path / "remote")
    conn.on("sbatch --parsable", (0, "4242\n", ""))
    asked: list[str] = []

    async def fake_lux_conn(ctx, tool, **call_args):
        asked.append(tool)
        return conn

    monkeypatch.setattr(m, "_lux_conn", fake_lux_conn)

    cfg = UserConfig(lux_remote_dir=LUX_BASE)  # no lux_account
    with pytest.raises(ToolError, match="No Lux account is set"):
        await m.submit_hpc_job(_ctx(cfg), job="hello-lux/hello", cluster="lux")
    assert asked == [] and conn.commands == [] and conn.puts == []

    # With the account set, the same call submits.
    cfg = UserConfig(lux_remote_dir=LUX_BASE, lux_account="abc123")
    summary = await m.submit_hpc_job(_ctx(cfg), job="hello-lux/hello", cluster="lux")
    assert "job_id: 4242" in summary
    [uploaded] = conn.puts
    assert uploaded.endswith("/input.txt")


def test_the_built_in_catalog_no_longer_lists_lux_hello():
    assert "lux-hello" not in m.AVAILABLE_JOBS
    assert "lux-hello" not in m.get_available_jobs()
