"""
`preview_hpc_job` and `get_hpc_queue_status`: the read-only tools Slack runs
without a confirmation. Preview contacts nothing; queue status is answered by
a fake IRI client or a fake Lux login node.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib import olcf_token
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeIriClient, FakeSshConn

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

CFG = UserConfig(
    frontier_s3m_token="frontier-token",
    lux_account="abc123",
    lux_remote_dir="/lustre/orion/abc123/proj-shared/vista",
)


def _ctx(user: UserConfig = CFG):
    vista = {"user": user.model_dump(), "project_paths": {}}
    return SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista))
    )


@pytest.fixture(autouse=True)
def jobs(tmp_path, monkeypatch):
    job = tmp_path / "hpc_jobs" / "flibe-md"
    job.mkdir(parents=True)
    (job / "README.md").write_text("# flibe-md\n\nFLiBe MD.\n", encoding="utf-8")
    (job / "cluster_defaults.json").write_text(
        json.dumps(
            {
                "frontier": {"duration": 3600, "resources": {"node_count": 2}},
                "lux": {"duration": 1800, "resources": {"node_count": 1}},
            }
        ),
        encoding="utf-8",
    )
    (job / "job.frontier.slurm").write_text("echo md\n", encoding="utf-8")
    (job / "job.lux.slurm").write_text("echo md\n", encoding="utf-8")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", tmp_path / "hpc_jobs")
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    olcf_token._introspect_cache.clear()


@pytest.fixture
def no_network(monkeypatch):
    async def refuse(*args, **kwargs):
        raise AssertionError("preview must not contact anything")

    for name in (
        "create_odo_iri_client",
        "create_olcf_iri_client",
        "create_iri_client",
        "get_s3m_token_project",
        "_lux_conn",
    ):
        monkeypatch.setattr(m, name, refuse)


# --- preview ---------------------------------------------------------------


async def test_preview_applies_the_jobs_defaults(no_network):
    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert "cluster: frontier" in text
    assert "nodes: 2" in text
    assert "duration: 1:00:00 (3600s)" in text
    assert "script: flibe-md/job.frontier.slurm" in text
    assert "account: the S3M token's project" in text


async def test_preview_shows_what_was_asked_for(no_network):
    text = await m.preview_hpc_job(
        _ctx(),
        job="flibe-md",
        cluster="lux",
        node_count=4,
        duration="0:30:00",
        script_args="--steps 1000 --salt 'Li F'",
    )

    assert "account: abc123" in text
    assert "nodes: 4" in text and "duration: 0:30:00" in text
    assert "job.lux.slurm --steps 1000 --salt 'Li F'" in text


async def test_preview_names_a_recently_introspected_project(no_network):
    olcf_token._introspect_cache[
        (settings.frontier_introspect_url, "frontier-token")
    ] = "mat201"

    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert "account: mat201" in text


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"node_count": m.MAX_NODES + 1}, "node_count out of range"),
        ({"duration": "9:00:00"}, "Time limit too large"),
        ({"job": "nope"}, "not recognized"),
        ({"cluster": "odo"}, 'no "odo" section'),
    ],
)
async def test_preview_rejects_what_submit_would(no_network, args, error):
    call = {"job": "flibe-md", "cluster": "frontier", **args}
    with pytest.raises((ToolError, ValueError), match=error):
        await m.preview_hpc_job(_ctx(), **call)


# --- queue status, IRI -----------------------------------------------------


@pytest.fixture
def iri(monkeypatch):
    client = FakeIriClient()

    async def make(*, iri_token: str):
        return client

    monkeypatch.setattr(m, "create_olcf_iri_client", make)
    monkeypatch.setattr(m, "create_odo_iri_client", make)
    monkeypatch.setattr(m, "create_iri_client", make)
    return client


async def test_iri_queue_status_reports_the_facility_and_own_jobs(iri):
    iri.facility = {
        "status": "degraded",
        "incidents": [
            {
                "name": "Orion slowdown",
                "type": "planned",
                "status": "degraded",
                "start": "2026-10-16T08:00:00+00:00",
                "end": None,
            }
        ],
    }
    iri.jobs = [
        {"id": "4471", "name": "vista-flibe-md", "state": "QUEUED"},
        {"id": "4470", "name": "vista-flibe-md", "state": "COMPLETED"},
    ]

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "status: degraded" in text
    assert "Orion slowdown" in text
    assert "queue depth: not available from IRI" in text
    assert "4471 vista-flibe-md QUEUED" in text
    assert "4470" not in text, "finished jobs are not in the queue"


async def test_iri_job_listing_failing_still_reports_the_facility(iri):
    iri.jobs = RuntimeError("403")

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "status: up" in text and "open incidents: none" in text
    assert "IRI could not list them" in text


async def test_no_token_is_a_tool_error(iri):
    with pytest.raises(ToolError):
        await m.get_hpc_queue_status(_ctx(UserConfig()), cluster="frontier")


# --- queue status, Lux -----------------------------------------------------


async def test_lux_queue_status_summarises_squeue(tmp_path, monkeypatch):
    conn = FakeSshConn(tmp_path / "remote")
    conn.on("--me", (0, "4242|vista-flibe-md|PENDING|0:00\n", ""))
    conn.on("squeue -h -o", (0, "RUNNING|4\nRUNNING|2\nPENDING|8\n", ""))

    async def lux_conn(ctx, tool, **call_args):
        return conn

    monkeypatch.setattr(m, "_lux_conn", lux_conn)

    text = await m.get_hpc_queue_status(_ctx(), cluster="lux")

    assert "- RUNNING: 2 jobs, 6 nodes" in text
    assert "- PENDING: 1 jobs, 8 nodes" in text
    assert "4242 vista-flibe-md PENDING" in text


async def test_tools_are_annotated_read_only():
    tools = {t.name: t for t in await m.mcp.list_tools()}
    for name in ("preview_hpc_job", "get_hpc_queue_status"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["submit_hpc_job"].annotations.readOnlyHint is False


def test_the_cluster_scripts_cover_every_cluster():
    assert set(m._CLUSTER_SCRIPTS) == set(m.Cluster.__args__)
