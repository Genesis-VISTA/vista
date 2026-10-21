"""
The transfer tools move files to and from Odo and Frontier outside a job.

Reads reach anything the researcher's Globus identity can read; writes reach only
`<remote_dir>.<user>.jobs/transfers/`, because no tool asks the researcher before it
runs. Every argument is checked before Globus is contacted, so a refused call costs no
round trip and leaves nothing behind. An expired session is reported as expired, never
as a missing file or an empty folder.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.transfer_mcp as t
from vista_mcp_server.config import settings
from vista_mcp_server.lib.globus import GlobusSessionExpired
from vista_mcp_server.lib.user_config import ProjectPaths, UserConfig
from fakes import FakeGlobusClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

TOOLS = {"hpc_list_files", "hpc_download_files", "hpc_upload_files"}

CFG = UserConfig(
    odo_globus_token="t",
    odo_globus_https_token="h",
    frontier_globus_token="t",
    frontier_globus_https_token="h",
    odo_remote_dir="/r",
    frontier_remote_dir="/r",
)


@pytest.fixture
def globus(monkeypatch) -> FakeGlobusClient:
    """One fake client for every call; `clients` counts how often one was built."""
    fake = FakeGlobusClient(home_user="jdoe")
    fake.clients = 0
    monkeypatch.setattr(settings, "odo_globus_collection_id", "odo-c")
    monkeypatch.setattr(settings, "frontier_globus_collection_id", "frontier-c")

    def create(*, tokens, cluster):
        fake.clients += 1
        return fake

    monkeypatch.setattr(t, "create_globus_client", create)
    return fake


@pytest.fixture
def project(tmp_path) -> ProjectPaths:
    (tmp_path / "output").mkdir()
    (tmp_path / "uploads").mkdir()
    return ProjectPaths(
        output_dir=str(tmp_path / "output"), uploads_dir=str(tmp_path / "uploads")
    )


class Expired(FakeGlobusClient):
    """Globus asking for a new session on every call, as after the 3-day OLCF timeout."""

    async def operation_ls(self, **_):
        raise GlobusSessionExpired("Your Globus session for Odo has expired.")

    async def download_file(self, **_):
        raise GlobusSessionExpired("Your Globus session for Odo has expired.")

    async def operation_mkdir_p(self, **_):
        raise GlobusSessionExpired("Your Globus session for Odo has expired.")


# --- the tools --------------------------------------------------------------


async def test_the_three_tools_are_on_the_server():
    from vista_mcp_server.server import mcp

    assert TOOLS <= {tool.name for tool in await mcp.list_tools()}


@pytest.mark.parametrize("tool", sorted(TOOLS))
async def test_only_odo_and_frontier_are_offered(tool):
    [spec] = [s for s in await t.mcp.list_tools() if s.name == tool]
    assert spec.parameters["properties"]["cluster"]["enum"] == ["odo", "frontier"]
    assert "cluster" in spec.parameters["required"]


@pytest.mark.parametrize("tool", sorted(TOOLS))
async def test_the_descriptions_say_where_files_go(tool):
    [spec] = [s for s in await t.mcp.list_tools() if s.name == tool]
    text = " ".join(spec.description.split())
    if tool == "hpc_upload_files":
        assert "transfers" in text and "replaced" in text
    else:
        assert "on the cluster" in text and "job" in text
    if tool == "hpc_download_files":
        assert "/mnt/data/output/transfers/<cluster>/" in text


def test_the_server_can_be_left_out():
    env = {**os.environ, "VISTA_MCP_DISABLE_SERVERS": "agenthpc,transfer"}
    names = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio; from vista_mcp_server.server import mcp; "
            "print(sorted(x.name for x in asyncio.run(mcp.list_tools())))",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    assert "get_hpc_job_status" in names
    assert not any(tool in names for tool in TOOLS)


async def test_no_globus_connection_names_the_setting(globus):
    with pytest.raises(ToolError, match="not connected for Frontier"):
        await t._list_files(UserConfig(), "frontier", "/data", False)


# --- listing ----------------------------------------------------------------


async def test_a_listing_names_type_size_and_path(globus):
    globus.ls_entries["/data"] = [
        {"name": "a.nc", "type": "file", "size": 2 * 1024**3, "path": "/data/a.nc"},
        {"name": "runs", "type": "dir", "size": 0, "path": "/data/runs"},
    ]
    text = await t._list_files(CFG, "frontier", "/data/", False)
    assert "file     2.0 GB  /data/a.nc" in text
    assert "dir" in text and "/data/runs" in text
    assert globus.ls_calls == [("frontier-c", "/data")]


async def test_a_recursive_listing_of_a_missing_folder_is_missing_not_empty(globus):
    with pytest.raises(ToolError, match="is not on"):
        await t._list_files(CFG, "odo", "/nowhere/here", True)


async def test_a_recursive_listing_walks_an_existing_folder(globus):
    globus.ls_entries["/data"] = [{"name": "x", "type": "dir", "permissions": "0755"}]
    globus.ls_entries["/data/x"] = [
        {"name": "a", "type": "file", "size": 1, "path": "/data/x/a"}
    ]
    text = await t._list_files(CFG, "odo", "/data/x", True)
    assert "/data/x/a" in text


async def test_a_plain_listing_of_a_missing_folder_is_missing(globus):
    with pytest.raises(ToolError, match="is not on"):
        await t._list_files(CFG, "odo", "/nowhere", False)


@pytest.mark.parametrize("path", ["data/runs", "~/data", "/data/../etc", "\\data"])
async def test_a_relative_or_climbing_path_is_refused_before_globus(globus, path):
    with pytest.raises(ToolError, match="not an absolute path on Odo"):
        await t._list_files(CFG, "odo", path, False)
    assert globus.clients == 0


# --- downloading ------------------------------------------------------------


async def test_a_download_mirrors_the_remote_path(globus, project):
    globus.files["/lustre/orion/abc/data/a.nc"] = b"nc"
    text = await t._download_files(
        CFG, Path(project.output_dir), "frontier", ["/lustre/orion/abc/data/a.nc"]
    )
    local = (
        Path(project.output_dir)
        / "transfers"
        / "frontier"
        / "lustre"
        / "orion"
        / "abc"
        / "data"
        / "a.nc"
    )
    assert local.read_bytes() == b"nc"
    assert "/mnt/data/output/transfers/frontier/lustre/orion/abc/data/a.nc" in text


async def test_a_second_download_is_served_locally(globus, project):
    globus.files["/d/a.txt"] = b"a"
    await t._download_files(CFG, Path(project.output_dir), "odo", ["/d/a.txt"])
    text = await t._download_files(CFG, Path(project.output_dir), "odo", ["/d/a.txt"])
    assert globus.downloads == [("odo-c", "/d/a.txt")]
    assert globus.clients == 1
    assert "/mnt/data/output/transfers/odo/d/a.txt" in text


async def test_a_missing_file_is_reported_and_the_rest_downloaded(globus, project):
    globus.files["/d/b.txt"] = b"b"
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt", "/d/b.txt"]
    )
    assert text.splitlines() == [
        "Downloaded files:",
        "/mnt/data/output/transfers/odo/d/b.txt",
        "Not on Odo:",
        "/d/a.txt",
    ]


@pytest.mark.parametrize("path", ["/lustre/../etc/passwd", "d/a.txt", "/"])
async def test_a_bad_download_path_is_refused_before_globus(globus, project, path):
    with pytest.raises(ToolError):
        await t._download_files(
            CFG, Path(project.output_dir), "odo", ["/d/fine.txt", path]
        )
    assert globus.clients == 0
    assert not (Path(project.output_dir) / "transfers").exists()


# --- uploading --------------------------------------------------------------


async def test_an_upload_lands_in_the_transfers_folder(globus, project):
    (Path(project.uploads_dir) / "salt.xyz").write_text("Li 0 0 0", encoding="utf-8")
    text = await t._upload_files(
        CFG, project, "frontier", ["/mnt/data/uploads/salt.xyz"], "inputs"
    )
    assert globus.files["/r.jdoe.jobs/transfers/inputs/salt.xyz"] == b"Li 0 0 0"
    # From the folder holding /r, so a researcher who never submitted a job gets `.jobs` too.
    assert globus.mkdir_p_calls == [
        ("frontier-c", "/r.jdoe.jobs/transfers/inputs", "/")
    ]
    assert text.splitlines()[-1] == "/r.jdoe.jobs/transfers/inputs/salt.xyz"


async def test_an_upload_from_the_output_folder_to_the_transfers_root(globus, project):
    (Path(project.output_dir) / "plot.png").write_bytes(b"png")
    await t._upload_files(CFG, project, "odo", ["/mnt/data/output/plot.png"], "")
    assert globus.files["/r.jdoe.jobs/transfers/plot.png"] == b"png"


async def test_a_second_upload_replaces_the_first(globus, project):
    source = Path(project.uploads_dir) / "salt.xyz"
    source.write_text("old", encoding="utf-8")
    await t._upload_files(CFG, project, "odo", ["/mnt/data/uploads/salt.xyz"], "inputs")
    source.write_text("new", encoding="utf-8")
    await t._upload_files(CFG, project, "odo", ["/mnt/data/uploads/salt.xyz"], "inputs")
    assert globus.files["/r.jdoe.jobs/transfers/inputs/salt.xyz"] == b"new"


@pytest.mark.parametrize(
    "remote_dir", ["/lustre/orion/abc/proj-shared", "../src", "a/../../b", "a\\b"]
)
async def test_a_destination_outside_transfers_is_refused_before_globus(
    globus, project, remote_dir
):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError, match="only under your transfers folder"):
        await t._upload_files(CFG, project, "odo", ["/mnt/data/uploads/f"], remote_dir)
    assert globus.clients == 0


@pytest.mark.parametrize(
    "path",
    [
        "/etc/hosts",
        "/mnt/data/output/../../etc/hosts",
        "/mnt/data/uploads",
        "/mnt/data/uploads/none",
    ],
)
async def test_a_source_outside_the_project_or_not_a_file_is_refused(
    globus, project, path
):
    with pytest.raises(ToolError, match="Cannot upload"):
        await t._upload_files(CFG, project, "odo", [path], "")
    assert globus.clients == 0


async def test_two_sources_with_one_name_are_refused(globus, project):
    (Path(project.uploads_dir) / "a.txt").write_text("1", encoding="utf-8")
    (Path(project.output_dir) / "a.txt").write_text("2", encoding="utf-8")
    with pytest.raises(ToolError, match="same name"):
        await t._upload_files(
            CFG,
            project,
            "odo",
            ["/mnt/data/uploads/a.txt", "/mnt/data/output/a.txt"],
            "",
        )
    assert globus.clients == 0


async def test_an_upload_needs_the_remote_folder(globus, project):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    cfg = CFG.model_copy(update={"odo_remote_dir": None})
    with pytest.raises(ToolError, match="Odo remote directory"):
        await t._upload_files(cfg, project, "odo", ["/mnt/data/uploads/f"], "")
    assert globus.clients == 0


# --- an expired session -----------------------------------------------------


@pytest.fixture
def expired(monkeypatch) -> Expired:
    fake = Expired()
    monkeypatch.setattr(t, "create_globus_client", lambda **_: fake)
    return fake


async def test_an_expired_session_on_a_listing(expired):
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._list_files(CFG, "odo", "/data", False)


async def test_an_expired_session_on_a_download_is_not_a_missing_file(expired, project):
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._download_files(CFG, Path(project.output_dir), "odo", ["/d/a.txt"])


async def test_an_expired_session_on_an_upload(expired, project):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._upload_files(CFG, project, "odo", ["/mnt/data/uploads/f"], "")


# --- through the tool, as a call sees it ------------------------------------


async def test_a_tool_call_reads_paths_and_credentials_from_the_request(
    globus, project
):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    vista = {"user": CFG.model_dump(), "project_paths": project.model_dump()}
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista))
    )
    text = await t.hpc_upload_files(ctx, "odo", ["/mnt/data/uploads/f"], "in")
    assert "/r.jdoe.jobs/transfers/in/f" in text
