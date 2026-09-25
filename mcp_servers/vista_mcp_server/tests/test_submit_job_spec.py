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
    _get_olcf_job_status,
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
    monkeypatch.setattr(submit_job_mcp, "_require_olcf_access", _noop_access)
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "fake-odo-refresh")
    monkeypatch.setattr(
        settings, "frontier_globus_refresh_token", "fake-frontier-refresh"
    )
    monkeypatch.setattr(settings, "odo_globus_https_refresh_token", "fake-odo-https")
    monkeypatch.setattr(
        settings, "frontier_globus_https_refresh_token", "fake-frontier-https"
    )


async def test_submit_odo_job_inlines_slurm_and_vista_out(monkeypatch, user_cfg):
    assert "example" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="odo-123")
    globus = FakeGlobusClient()
    globus.seed_odo_out_dir("/fake/odo/vista")
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, err_path, out_dir, nodes, duration = await _submit_odo_job(
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
    # The stderr path is rendered beside stdout and was thrown away, which is why
    # a job that failed reached its caller with nothing to explain it.
    assert err_path.endswith("log-odo-123.err")
    assert out_dir.endswith("/out/odo-123")

    assert len(iri.submitted) == 1
    spec, name = iri.submitted[0]
    assert name == "vista-example"
    assert spec["executable"] == "bash"
    job_cmd = spec["arguments"][2]
    assert 'export VISTA_OUT="/fake/odo/vista/out/$SLURM_JOB_ID"' in job_cmd
    # Slurm script body is inlined
    slurm = (HPC_JOBS_DIR / "example" / "job.odo.slurm").read_text(encoding="utf-8")
    assert slurm in job_cmd
    assert "set -- a b" in job_cmd
    env = spec["attributes"]["environment"]
    assert env["RUN_DIR_Odo"] == "/fake/odo/vista/example/src"
    assert spec["attributes"]["account"] == "gen150-vista"
    assert globus.uploads, "expected Globus source upload"


async def test_submit_perlmutter_job_inlines_slurm_and_uploads(monkeypatch, user_cfg):
    assert "forge-tune" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="pm-99")
    globus = FakeGlobusClient()
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, err_path, out_dir, nodes, duration = await _submit_perlmutter_job(
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
    assert err_path.endswith("log-pm-99.err")

    spec, name = iri.submitted[0]
    assert name == "vista-forge-tune"
    job_cmd = spec["arguments"][2]
    assert "export VISTA_OUT=" in job_cmd
    slurm = (HPC_JOBS_DIR / "forge-tune" / "job.perlmutter.slurm").read_text(
        encoding="utf-8"
    )
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

    job_id, log_path, err_path, out_dir, nodes, duration = await _submit_frontier_job(
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args=None,
    )

    assert job_id == "fr-7"
    assert nodes == 1
    assert duration == 600
    assert err_path.endswith("log-fr-7.err")
    spec, _ = iri.submitted[0]
    job_cmd = spec["arguments"][2]
    assert "VISTA_OUT=" in job_cmd
    assert (HPC_JOBS_DIR / "example" / "job.frontier.slurm").read_text(
        encoding="utf-8"
    ) in job_cmd
    run_dir = spec["attributes"]["environment"].get("RUN_DIR_Frontier")
    assert run_dir is not None and run_dir.endswith("/example/src")
    assert globus.mkdir_p_calls
    assert globus.uploads


async def test_odo_status_fetches_both_streams_and_shows_stderr(
    monkeypatch, user_cfg, tmp_path
):
    """
    The path every odo job actually takes, and the one that was untested.

    Job 44039 exited with status 2 having written only the setup script's echoes
    to stdout; the reason — argparse rejecting invented flags — was in
    `log-44039.err`, a file this function had never heard of. The agent that
    commissioned it was told "no outputs recorded" and reasonably concluded its
    test had produced nothing.
    """
    import json

    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    globus = FakeGlobusClient()
    globus.files["/gpfs/out/44039/log-44039.out"] = (
        b"[setup_odo] OK: run_state_point.py present\n"
    )
    globus.files["/gpfs/out/44039/log-44039.err"] = (
        b"run_state_point.py: error: unrecognized arguments: --salt flibe_90Li6\n"
    )
    _patch_clients(monkeypatch, iri=iri, globus=globus)
    _record_submitted_job(
        "44039",
        SubmittedJob(
            cluster="odo",
            log_path="/gpfs/out/44039/log-44039.out",
            err_path="/gpfs/out/44039/log-44039.err",
            output_dir="/gpfs/out/44039",
        ),
    )

    text = await _get_olcf_job_status(user_cfg, tmp_path, "44039", cluster="odo")

    assert "--- LOGS ---" in text
    assert "run_state_point.py present" in text
    assert "--- STDERR ---" in text
    assert "unrecognized arguments: --salt flibe_90Li6" in text
    # Both streams are tailed the same incremental way, so stderr costs one more
    # HEAD and one more ranged GET — not a second transfer task.
    assert {path for path, _start, _end in globus.range_reads} == {
        "/gpfs/out/44039/log-44039.out",
        "/gpfs/out/44039/log-44039.err",
    }


async def test_odo_status_distinguishes_empty_stderr_from_no_stderr(
    monkeypatch, user_cfg, tmp_path
):
    """
    "nothing on stderr" is a fact about the job; "no stderr path" is one about us.

    Slurm creates the stderr file only when something writes to it, so a job that
    succeeded leaves none — the fake raises `GlobusFileNotFound` for it, exactly
    as a real collection does. A reader must not take our own missing bookkeeping
    for the job having reported no error.
    """
    import json

    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    globus = FakeGlobusClient()
    globus.files["/gpfs/out/1/log-1.out"] = b"TBR = 1.14\n"
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    _record_submitted_job(
        "1",
        SubmittedJob(
            cluster="odo",
            log_path="/gpfs/out/1/log-1.out",
            err_path="/gpfs/out/1/log-1.err",
            output_dir="/gpfs/out/1",
        ),
    )
    ran = await _get_olcf_job_status(user_cfg, tmp_path, "1", cluster="odo")
    assert "TBR = 1.14" in ran
    assert "(nothing on stderr)" in ran

    # A job recorded by the previous build, with no stderr path at all.
    _record_submitted_job(
        "2",
        SubmittedJob(
            cluster="odo",
            log_path="/gpfs/out/1/log-1.out",
            output_dir="/gpfs/out/1",
        ),
    )
    legacy = await _get_olcf_job_status(user_cfg, tmp_path, "2", cluster="odo")
    assert "no stderr path cached" in legacy


async def test_odo_status_says_when_stderr_could_not_be_fetched(
    monkeypatch, user_cfg, tmp_path
):
    """
    "We could not look" must never render as "there was nothing there".

    A file that does not exist and a collection that would not answer both leave
    us with nothing locally, and collapsing them would let an outage be read as a
    job that printed no error — the exact mistake this line of work exists to
    stop. Only the first is `(nothing on stderr)`.
    """
    import json

    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    globus = FakeGlobusClient()
    globus.files["/gpfs/out/3/log-3.out"] = b"setup ok\n"

    real_stat = globus.stat

    async def _refuse(*, collection_id: str, remote_path: str) -> int:
        if remote_path.endswith(".err"):
            raise RuntimeError("endpoint activation expired")
        return await real_stat(collection_id=collection_id, remote_path=remote_path)

    globus.stat = _refuse  # type: ignore[method-assign]
    _patch_clients(monkeypatch, iri=iri, globus=globus)
    _record_submitted_job(
        "3",
        SubmittedJob(
            cluster="odo",
            log_path="/gpfs/out/3/log-3.out",
            err_path="/gpfs/out/3/log-3.err",
            output_dir="/gpfs/out/3",
        ),
    )

    text = await _get_olcf_job_status(user_cfg, tmp_path, "3", cluster="odo")
    assert "endpoint activation expired" in text
    assert "(nothing on stderr)" not in text, "silence we did not verify is not silence"


async def test_perlmutter_status_formats_golden_fixture(
    monkeypatch, user_cfg, tmp_path
):
    """Status text matches the KEY=VALUE shape campaign parsers expect."""
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    status = json.loads(fixture.read_text(encoding="utf-8"))
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


async def test_perlmutter_status_shows_stderr(monkeypatch, user_cfg):
    """
    Where a failed job explains itself.

    Reading only stdout is how an argparse usage message — exit status 2, nothing
    on stdout past the setup echoes — reached a debating agent as "no outputs
    recorded", twice.
    """
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    iri.head_content["/remote/log.out"] = "setup ok\n"
    iri.head_content["/remote/log.err"] = (
        "run_state_point.py: error: unrecognized arguments: --salt flibe_90Li6\n"
    )

    async def _nersc(*, iri_token: str):
        return iri

    monkeypatch.setattr(submit_job_mcp, "create_iri_client", _nersc)
    _record_submitted_job(
        "pm-2",
        SubmittedJob(
            cluster="perlmutter",
            log_path="/remote/log.out",
            err_path="/remote/log.err",
            output_dir="/remote/out",
        ),
    )

    text = await _get_perlmutter_job_status(user_cfg, "pm-2")
    assert "--- STDERR ---" in text
    assert "unrecognized arguments: --salt flibe_90Li6" in text


async def test_perlmutter_status_says_when_there_is_no_stderr(monkeypatch, user_cfg):
    """
    A job registered before this existed has no stderr path, and stays pollable.

    "(no stderr path cached)" and "(nothing on stderr)" are different facts, and
    neither may read as "the job printed no error".
    """
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    iri.head_content["/remote/log.out"] = "setup ok\n"

    async def _nersc(*, iri_token: str):
        return iri

    monkeypatch.setattr(submit_job_mcp, "create_iri_client", _nersc)
    _record_submitted_job(
        "pm-3", SubmittedJob(cluster="perlmutter", log_path="/remote/log.out")
    )

    text = await _get_perlmutter_job_status(user_cfg, "pm-3")
    assert "no stderr path cached" in text
