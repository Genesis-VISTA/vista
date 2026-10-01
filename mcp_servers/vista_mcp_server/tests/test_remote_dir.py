"""
Where a researcher's jobs live on a cluster is their own setting, one per
cluster, with no default. Where a project keeps its files is specific to the
project and the filesystem, so VISTA does not guess, and an unset folder stops
a submission before anything is introspected, uploaded or submitted.
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

pytestmark = pytest.mark.unit

LABELS = {
    "odo": "Odo remote directory",
    "frontier": "Frontier remote directory",
    "perlmutter": "NERSC remote directory",
    "lux": "Lux remote directory",
}
FIELDS = {
    "odo": "odo_remote_dir",
    "frontier": "frontier_remote_dir",
    "perlmutter": "nersc_remote_dir",
    "lux": "lux_remote_dir",
}


@pytest.mark.parametrize("cluster", list(LABELS))
def test_unset_names_the_setting(cluster):
    with pytest.raises(ToolError, match=LABELS[cluster]) as refusal:
        UserConfig().require_remote_dir(cluster)
    assert "settings" in str(refusal.value).lower()


@pytest.mark.parametrize("cluster", list(LABELS))
def test_each_cluster_reads_its_own_setting(cluster):
    cfg = UserConfig(**{FIELDS[c]: f"/{c}/vista" for c in FIELDS})
    assert cfg.require_remote_dir(cluster) == f"/{cluster}/vista"


def test_trailing_slash_is_dropped():
    assert UserConfig(odo_remote_dir="/a/b/").require_remote_dir("odo") == "/a/b"


def test_relative_path_is_refused():
    with pytest.raises(ToolError, match="not an absolute path"):
        UserConfig(frontier_remote_dir="proj/vista").require_remote_dir("frontier")


@pytest.mark.parametrize(
    "folder",
    [
        "/proj/my vista",  # a space splits an #SBATCH -o path
        "/proj/$HOME",  # expanded by the job's shell
        "/proj/`id`",
        '/proj/a"b',
        "/proj/a;rm",
        "/proj/log%j",  # Slurm expands % in output paths
        "/proj/*",
    ],
)
def test_characters_a_shell_or_slurm_would_read_are_refused(folder):
    """The folder is pasted into the job's bash prefix and into Slurm's output
    patterns, and the job runs as the project's shared automation user."""
    with pytest.raises(ToolError, match="cannot pass to a job safely"):
        UserConfig(odo_remote_dir=folder).require_remote_dir("odo")


def test_plain_path_characters_are_accepted():
    folder = "/gpfs/wolf2/olcf/abc123/proj-shared/v1.2_run+a,b:c=d@e"
    assert UserConfig(odo_remote_dir=folder).require_remote_dir("odo") == folder


def test_the_root_is_refused():
    """`<dir>.jobs` and `<dir>.out` need a parent folder to sit in."""
    with pytest.raises(ToolError, match="cannot be /"):
        UserConfig(lux_remote_dir="/").require_remote_dir("lux")


def test_the_mcp_server_has_no_folder_of_its_own():
    assert not [f for f in type(settings).model_fields if f.endswith("_remote_dir")]
    assert "lux_account" not in type(settings).model_fields


@pytest.fixture
def clients(monkeypatch):
    """Fakes that record whether anything past the folder check was reached."""
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", REPO_ROOT / "hpc_jobs")
    reached: list[str] = []
    iri, globus = FakeIriClient(job_id="1"), FakeGlobusClient()

    async def introspect(token, *, introspect_url):
        reached.append("introspect")
        return "abc123"

    async def make_iri(*, iri_token):
        reached.append("iri")
        return iri

    monkeypatch.setattr(m, "get_s3m_token_project", introspect)
    monkeypatch.setattr(m, "create_odo_iri_client", make_iri)
    monkeypatch.setattr(m, "create_olcf_iri_client", make_iri)
    monkeypatch.setattr(m, "create_iri_client", make_iri)
    monkeypatch.setattr(m, "create_globus_client", lambda **kw: globus)
    return reached, iri, globus


CREDENTIALS = UserConfig(
    odo_s3m_token="o",
    frontier_s3m_token="f",
    nersc_iri_token="n",
    nersc_account="m0000",
    globus_token="gt",
    globus_https_token="gh",
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cluster", "submit", "job"),
    [
        ("odo", m._submit_odo_job, "example"),
        ("frontier", m._submit_frontier_job, "example"),
        ("perlmutter", m._submit_perlmutter_job, "forge-tune"),
    ],
)
async def test_submission_stops_at_an_unset_folder(clients, cluster, submit, job):
    reached, iri, globus = clients
    with pytest.raises(ToolError, match=LABELS[cluster]):
        await submit(
            CREDENTIALS, job, node_count=None, duration_int=None, script_args=None
        )
    assert reached == []
    assert iri.submitted == []
    assert globus.uploads == []
