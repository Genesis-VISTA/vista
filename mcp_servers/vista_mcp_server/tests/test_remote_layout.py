"""
One folder layout on every cluster, found again from the job id alone. Two
folders beside the researcher's remote folder `<remote_dir>`:

    <remote_dir>.jobs/<job>/src/     sources
    <remote_dir>.out/log-<id>.out    Slurm stdout, and .err beside it
    <remote_dir>.out/<id>/           VISTA_OUT
    <remote_dir>.out/<job>/          VISTA_JOB_DIR, state a job's runs share

Nothing is recorded at submit time: no registry, and no per-run folder whose
name only the submitting process knew. That is what lets a status call after a
restart, or from another install sharing the folder, find a job's files.

On Odo and Frontier the job runs as the project's IRI automation user, which
creates `.out` itself (through Slurm), while `.jobs` belongs to the researcher.
So the folder holding them must be writable by the project's group, and VISTA
checks that before submitting rather than letting the job die at log creation.
Temporary: until S3M tokens can use the IRI filesystem API.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server import dry_run
from vista_mcp_server.config import AppSettings, settings
from vista_mcp_server.lib.globus import GlobusSessionExpired
from fakes import FakeGlobusClient

pytestmark = pytest.mark.unit

PROJ = "/lustre/orion/abc123/proj-shared"
BASE = f"{PROJ}/foo"


def test_the_layout():
    layout = m.RemoteLayout(BASE)
    assert layout.parent == PROJ
    assert layout.jobs == f"{BASE}.jobs"
    assert layout.out == f"{BASE}.out"
    assert layout.src("example") == f"{BASE}.jobs/example/src"
    assert layout.job_dir("example") == f"{BASE}.out/example"
    assert layout.stdout_template == f"{BASE}.out/log-%j.out"
    assert layout.stderr_template == f"{BASE}.out/log-%j.err"
    assert layout.log_path("42") == f"{BASE}.out/log-42.out"
    assert layout.err_path("42") == f"{BASE}.out/log-42.err"
    assert layout.output_dir("42") == f"{BASE}.out/42"


def test_nothing_is_kept_between_calls():
    assert "session_id" not in AppSettings.model_fields
    for name in ("_submitted_jobs", "SubmittedJob", "_registry_path"):
        assert not hasattr(m, name), name


@pytest.mark.anyio
async def test_there_is_no_job_listing_tool():
    """IRI cannot list a researcher's jobs, so the registry was the only
    source; with no registry there is nothing honest for it to return."""
    names = {tool.name for tool in await m.mcp.list_tools()}
    assert "list_hpc_jobs" not in names
    assert {"submit_hpc_job", "get_hpc_job_status", "cancel_hpc_job"} <= names


# ------------------------------------------------------------------ folder check


async def check(globus: FakeGlobusClient, base: str = BASE) -> None:
    await m._require_writable_out(
        globus, collection_id="coll", layout=m.RemoteLayout(base), cluster="frontier"
    )


@pytest.mark.anyio
async def test_a_new_folder_in_proj_shared_needs_no_setup():
    """The case that used to be refused: a fresh `proj-shared/foo`, nothing
    created yet. The automation user can make `foo.out` in a 770 parent."""
    globus = FakeGlobusClient()
    globus.seed_remote_dir(BASE, parent_permissions="2770")
    await check(globus)
    assert globus.ls_calls == [("coll", PROJ), ("coll", "/lustre/orion/abc123")]


@pytest.mark.anyio
async def test_an_existing_output_folder_is_accepted_whatever_its_mode():
    """Slurm leaves `.out` 755 but owned by the automation user, so its mode
    says nothing about whether that user can write there."""
    globus = FakeGlobusClient()
    globus.seed_remote_dir(BASE, parent_permissions="0755")
    globus.seed_out_dir(BASE, permissions="0755")
    await check(globus)
    assert globus.ls_calls == [("coll", PROJ)]  # no need to look further up


@pytest.mark.anyio
async def test_a_parent_its_group_cannot_write_is_refused_with_the_fix():
    globus = FakeGlobusClient()
    globus.seed_remote_dir(BASE, parent_permissions="0755")
    with pytest.raises(ToolError) as refusal:
        await check(globus)
    message = str(refusal.value)
    assert f"mkdir -p -m 2775 {BASE}.out" in message
    assert "0755" in message
    assert "automation user" in message


@pytest.mark.anyio
async def test_a_missing_parent_is_refused_with_the_fix():
    globus = FakeGlobusClient()  # nothing seeded: listing PROJ is not found
    with pytest.raises(ToolError, match=f"mkdir -p -m 2775 {BASE}.out"):
        await check(globus)


@pytest.mark.anyio
async def test_a_file_where_the_output_folder_should_be_is_refused():
    globus = FakeGlobusClient()
    globus.ls_entries[PROJ] = [{"name": "foo.out", "type": "file"}]
    with pytest.raises(ToolError, match="not a folder"):
        await check(globus)


@pytest.mark.anyio
async def test_a_grandparent_that_cannot_be_listed_does_not_block_submission(
    monkeypatch,
):
    """Above a project's own directories, a listing is often refused. Not
    knowing is not a reason to refuse."""
    globus = FakeGlobusClient()
    globus.ls_entries[PROJ] = []
    real_ls = globus.operation_ls

    async def forbidden_above(**kwargs):
        if kwargs["path"] != PROJ:
            raise RuntimeError("403 PermissionDenied")
        return await real_ls(**kwargs)

    monkeypatch.setattr(globus, "operation_ls", forbidden_above)
    await check(globus)


@pytest.mark.anyio
async def test_a_parent_that_cannot_be_listed_does_not_block_submission(monkeypatch):
    globus = FakeGlobusClient()

    async def forbidden(**kwargs):
        raise RuntimeError("403 PermissionDenied")

    monkeypatch.setattr(globus, "operation_ls", forbidden)
    await check(globus)


@pytest.mark.anyio
async def test_an_expired_session_is_not_mistaken_for_a_missing_folder(monkeypatch):
    globus = FakeGlobusClient()

    async def expired(**kwargs):
        raise GlobusSessionExpired(
            "Reconnect Globus for Frontier in the VISTA user settings."
        )

    monkeypatch.setattr(globus, "operation_ls", expired)
    with pytest.raises(GlobusSessionExpired):
        await check(globus)


# ------------------------------------------------------------------ dry-run


class _Ctx:
    """Just enough of an MCP context: no vista metadata, so no credentials."""

    class request_context:
        meta = None


@pytest.mark.anyio
async def test_a_dry_run_job_needs_no_cluster_and_no_credentials(monkeypatch):
    monkeypatch.setattr(settings, "hpc_dry_run", True)
    dry_run.reset()
    job_id = dry_run.record_submit("frontier", "example", 1, 60)
    try:
        text = await m.get_hpc_job_status(_Ctx(), job_id)
    finally:
        dry_run.reset()
    assert f"JOB_ID={job_id}" in text
    assert "CLUSTER=frontier" in text
