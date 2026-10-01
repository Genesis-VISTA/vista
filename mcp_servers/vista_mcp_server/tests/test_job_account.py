"""
The Slurm account an Odo or Frontier job is charged to is its S3M token's own
project. IRI runs the job as that project's automation user, so no other
account could work, and VISTA accepts a token from any project: there is no
configured project for the token to be compared with.

Only submission asks S3M. Status, outputs and cancel go straight to IRI and to
the researcher's own Globus identity, which the facility authorizes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeGlobusClient, FakeIriClient

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

PROJECT = "abc123"
""" A project no deployment has ever been configured for. """


@pytest.fixture
def introspected(monkeypatch):
    """Record every introspection; each token belongs to `PROJECT`."""
    calls: list[tuple[str, str]] = []

    async def fake(token, *, introspect_url):
        calls.append((token, introspect_url))
        return PROJECT

    monkeypatch.setattr(m, "get_s3m_token_project", fake)
    return calls


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
async def test_the_account_is_the_tokens_project(introspected, cluster):
    cfg = UserConfig(**{f"{cluster}_s3m_token": "tok"})
    assert await m._olcf_project(cfg, cluster) == ("tok", PROJECT)
    url = (
        settings.odo_introspect_url
        if cluster == "odo"
        else settings.frontier_introspect_url
    )
    assert introspected == [("tok", url)]


async def test_no_token_is_refused_before_introspection(introspected):
    with pytest.raises(ToolError, match="No S3M token configured"):
        await m._olcf_project(UserConfig(), "frontier")
    assert introspected == []


@pytest.fixture
def submit_env(monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", REPO_ROOT / "hpc_jobs")
    monkeypatch.setattr(settings, "session_id", "test-session")
    iri = FakeIriClient(job_id="42")
    globus = FakeGlobusClient()
    globus.seed_odo_out_dir("/fake/odo/vista")

    async def _iri(*, iri_token: str):
        return iri

    monkeypatch.setattr(m, "create_odo_iri_client", _iri)
    monkeypatch.setattr(m, "create_olcf_iri_client", _iri)
    monkeypatch.setattr(m, "create_globus_client", lambda **kwargs: globus)
    return iri, globus


CFG = UserConfig(
    odo_s3m_token="odo-tok",
    frontier_s3m_token="fr-tok",
    globus_token="gt",
    globus_https_token="gh",
    odo_remote_dir="/fake/odo/vista",
    frontier_remote_dir="/fake/frontier/vista",
)


@pytest.mark.parametrize(
    ("cluster", "submit"),
    [("odo", m._submit_odo_job), ("frontier", m._submit_frontier_job)],
)
async def test_a_job_is_charged_to_any_tokens_project(
    introspected, submit_env, cluster, submit
):
    iri, _ = submit_env
    await submit(CFG, "example", node_count=None, duration_int=None, script_args=None)
    spec, _ = iri.submitted[0]
    assert spec["attributes"]["account"] == PROJECT


@pytest.mark.parametrize(
    ("cluster", "submit"),
    [("odo", m._submit_odo_job), ("frontier", m._submit_frontier_job)],
)
async def test_an_unreadable_token_moves_no_files(
    monkeypatch, submit_env, cluster, submit
):
    """Introspection comes first: a token S3M refuses, or one with no project,
    stops the submission before any source is uploaded."""
    _, globus = submit_env

    async def refuse(token, *, introspect_url):
        raise ToolError("S3M token introspection failed (401).")

    monkeypatch.setattr(m, "get_s3m_token_project", refuse)
    with pytest.raises(ToolError, match="introspection failed"):
        await submit(
            CFG, "example", node_count=None, duration_int=None, script_args=None
        )
    assert globus.uploads == []


@pytest.fixture
def no_introspection(monkeypatch):
    async def forbidden(token, *, introspect_url):
        raise AssertionError("status and outputs must not introspect the token")

    monkeypatch.setattr(m, "get_s3m_token_project", forbidden)


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(
        m,
        "_submitted_jobs",
        {"10": m.SubmittedJob(cluster="frontier", output_dir="/o/10")},
    )


async def test_status_does_not_introspect(
    no_introspection, registry, monkeypatch, tmp_path
):
    async def iri_for(cluster, cfg):
        return FakeIriClient(status={"state": "QUEUED"})

    monkeypatch.setattr(m, "_create_olcf_iri_for", iri_for)
    text = await m._get_olcf_job_status(
        UserConfig(frontier_s3m_token="tok"), tmp_path, "10", cluster="frontier"
    )
    assert "STATE=QUEUED" in text


async def test_outputs_do_not_introspect(
    no_introspection, registry, monkeypatch, tmp_path
):
    globus = FakeGlobusClient()
    monkeypatch.setattr(m, "create_globus_client", lambda **kwargs: globus)
    (tmp_path / "10").mkdir()
    (tmp_path / "10" / "a.txt").write_text("cached", encoding="utf-8")
    text = await m._get_olcf_job_outputs(
        UserConfig(frontier_s3m_token="tok"),
        tmp_path,
        "10",
        ["a.txt"],
        cluster="frontier",
    )
    assert "a.txt" in text
