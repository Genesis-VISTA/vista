"""
JobSpec construction tests for Odo / Perlmutter / Frontier submit paths.

IRI and Globus are faked — no network. Asserts Slurm inlining, VISTA_OUT,
setup/pre_launch, and source sync.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vista_mcp_server.submit_job_mcp as submit_job_mcp
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from vista_mcp_server.submit_job_mcp import (
    AVAILABLE_JOBS,
    SubmittedJob,
    _get_perlmutter_job_status,
    _record_submitted_job,
    _submit_frontier_job,
    _submit_odo_job,
    _submit_perlmutter_job,
    _submitted_jobs,
)
from fakes import FakeGlobusClient, FakeIriClient

REPO_ROOT = Path(__file__).resolve().parents[3]
HPC_JOBS_DIR = REPO_ROOT / "hpc_jobs"

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _hpc_jobs_and_registry(monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", HPC_JOBS_DIR)
    # Patched on the class: `vista_globus_collection_id` is a plain property
    # reading a file, so there is no instance attribute to assign to.
    monkeypatch.setattr(
        type(settings),
        "vista_globus_collection_id",
        property(lambda self: "vista-gcs-id"),
    )
    monkeypatch.setattr(settings, "odo_globus_collection_id", "odo-collection")
    monkeypatch.setattr(
        settings, "frontier_globus_collection_id", "frontier-collection"
    )
    monkeypatch.setattr(settings, "odo_remote_dir", "/fake/odo/vista")
    monkeypatch.setattr(settings, "frontier_remote_dir", "/fake/frontier/vista")
    monkeypatch.setattr(settings, "odo_account", "gen150-vista")
    monkeypatch.setattr(settings, "frontier_account", "chm243")
    monkeypatch.setattr(settings, "session_id", "test-session")
    _submitted_jobs.clear()
    yield
    _submitted_jobs.clear()


@pytest.fixture
def user_cfg() -> UserConfig:
    return UserConfig(
        odo_s3m_token="odo-token",
        frontier_s3m_token="frontier-token",
        nersc_iri_token="nersc-token",
        nersc_account="m1234",
        nersc_remote_dir="/fake/nersc/home/user/vista",
    )


def _patch_clients(monkeypatch, *, iri: FakeIriClient, globus: FakeGlobusClient):
    async def _odo(*, iri_token: str):
        return iri

    async def _olcf(*, iri_token: str):
        return iri

    async def _nersc(*, iri_token: str):
        return iri

    async def _noop_access(cfg, cluster):
        return None

    monkeypatch.setattr(submit_job_mcp, "create_odo_iri_client", _odo)
    monkeypatch.setattr(submit_job_mcp, "create_olcf_iri_client", _olcf)
    monkeypatch.setattr(submit_job_mcp, "create_iri_client", _nersc)
    monkeypatch.setattr(submit_job_mcp, "create_globus_client", lambda **kwargs: globus)

    # This machine's collection is a given here; bringing one up is
    # `test_local_collection.py`'s subject. Without this the dispatchers would
    # try to start a real endpoint on the way to building a job spec.
    async def _collection(_globus, _cluster):
        return settings.vista_globus_collection_id

    monkeypatch.setattr(submit_job_mcp, "ensure_local_collection", _collection)
    monkeypatch.setattr(submit_job_mcp, "_require_olcf_access", _noop_access)
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "fake-odo-refresh")
    monkeypatch.setattr(
        settings, "frontier_globus_refresh_token", "fake-frontier-refresh"
    )


async def test_submit_odo_job_inlines_slurm_and_vista_out(monkeypatch, user_cfg):
    assert "example" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="odo-123")
    globus = FakeGlobusClient()
    globus.seed_odo_out_dir("/fake/odo/vista")
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, out_dir, nodes, duration = await _submit_odo_job(
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args="a b",
    )

    assert job_id == "odo-123"
    assert nodes == 1
    assert duration == 120  # example cluster_defaults
    assert log_path.endswith("log-odo-123.out")
    assert out_dir.endswith("/out/odo-123")

    assert len(iri.submitted) == 1
    spec, name = iri.submitted[0]
    assert name == "vista-example"
    assert spec["executable"] == "bash"
    job_cmd = spec["arguments"][2]
    assert 'export VISTA_OUT="/fake/odo/vista/out/$SLURM_JOB_ID"' in job_cmd
    # Slurm script body is inlined
    slurm = (HPC_JOBS_DIR / "example" / "job.odo.slurm").read_text()
    assert slurm in job_cmd
    assert "set -- a b" in job_cmd
    env = spec["attributes"]["environment"]
    assert env["RUN_DIR_Odo"] == "/fake/odo/vista/example/src"
    assert spec["attributes"]["account"] == "gen150-vista"
    assert globus.transfers, "expected Globus source upload"


async def test_submit_perlmutter_job_inlines_slurm_and_uploads(monkeypatch, user_cfg):
    assert "forge-tune" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="pm-99")
    globus = FakeGlobusClient()
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, out_dir, nodes, duration = await _submit_perlmutter_job(
        user_cfg,
        "forge-tune",
        node_count=2,
        duration_int=900,
        script_args=None,
    )

    assert job_id == "pm-99"
    assert nodes == 2
    assert duration == 900
    assert "test-session/out" in log_path

    spec, name = iri.submitted[0]
    assert name == "vista-forge-tune"
    job_cmd = spec["arguments"][2]
    assert "export VISTA_OUT=" in job_cmd
    slurm = (HPC_JOBS_DIR / "forge-tune" / "job.perlmutter.slurm").read_text()
    assert slurm in job_cmd
    # setup_perlmutter.sh exists → pre_launch set
    assert "pre_launch" in spec["attributes"]
    assert "bash -lc" in spec["attributes"]["pre_launch"]
    env = spec["attributes"]["environment"]
    assert env["RUN_DIR_Perlmutter"].endswith("/forge-tune/src")
    assert "VISTA_PM_IMAGE" in env
    assert iri.mkdirs  # out dir
    assert iri.uploads  # source files


async def test_submit_frontier_job_syncs_and_inlines(monkeypatch, user_cfg):
    iri = FakeIriClient(job_id="fr-7")
    globus = FakeGlobusClient()
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, out_dir, nodes, duration = await _submit_frontier_job(
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args=None,
    )

    assert job_id == "fr-7"
    assert nodes == 1
    assert duration == 600
    spec, _ = iri.submitted[0]
    job_cmd = spec["arguments"][2]
    assert "VISTA_OUT=" in job_cmd
    assert (HPC_JOBS_DIR / "example" / "job.frontier.slurm").read_text() in job_cmd
    run_dir = spec["attributes"]["environment"].get("RUN_DIR_Frontier")
    assert run_dir is not None and run_dir.endswith("/example/src")
    assert globus.mkdir_p_calls
    assert globus.transfers


async def test_perlmutter_status_formats_golden_fixture(
    monkeypatch, user_cfg, tmp_path
):
    """Status text matches the KEY=VALUE shape campaign parsers expect."""
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    status = json.loads(fixture.read_text())
    iri = FakeIriClient(status=status)
    iri.head_content["/remote/log.out"] = "line1\nline2\n"

    async def _nersc(*, iri_token: str):
        return iri

    monkeypatch.setattr(submit_job_mcp, "create_iri_client", _nersc)
    _record_submitted_job(
        "pm-1",
        SubmittedJob(
            cluster="perlmutter", log_path="/remote/log.out", output_dir="/remote/out"
        ),
    )

    text = await _get_perlmutter_job_status(user_cfg, "pm-1")
    assert "JOB_ID=pm-1" in text
    assert "STATE=COMPLETED" in text
    assert "EXIT_CODE=0" in text
    assert "--- LOGS ---" in text
    assert "line1" in text
