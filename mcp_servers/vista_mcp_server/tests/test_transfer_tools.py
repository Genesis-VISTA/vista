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
from fastmcp.server.elicitation import AcceptedElicitation

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


class Approver:
    """Stands in for the researcher: records each prompt and gives a set answer."""

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.messages: list[str] = []

    async def __call__(self, message: str) -> bool:
        self.messages.append(message)
        return self.answer


@pytest.fixture
def approver() -> Approver:
    return Approver()


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


async def test_an_unwalked_folder_is_marked(globus):
    globus.ls_entries["/out"] = [
        {"name": ".cache", "type": "dir", "path": "/out/.cache", "walked": False},
        {"name": "chart.png", "type": "file", "size": 9300, "path": "/out/chart.png"},
    ]
    text = await t._list_files(CFG, "odo", "/out", False)
    assert "/out/.cache  (not walked)" in text
    assert "/out/chart.png" in text


async def test_a_plain_listing_of_a_missing_folder_is_missing(globus):
    with pytest.raises(ToolError, match="is not on"):
        await t._list_files(CFG, "odo", "/nowhere", False)


@pytest.mark.parametrize("path", ["data/runs", "~/data", "/data/../etc", "\\data"])
async def test_a_relative_or_climbing_path_is_refused_before_globus(globus, path):
    with pytest.raises(ToolError, match="not an absolute path on Odo"):
        await t._list_files(CFG, "odo", path, False)
    assert globus.clients == 0


# --- downloading ------------------------------------------------------------

REMOTE_TIME = 1792591387
""" 2026-10-21 14:03:07 UTC, the default `last_modified` of a seeded file. """


def local_copy(project, cluster: str, remote: str) -> Path:
    return Path(project.output_dir) / "transfers" / cluster / remote.lstrip("/")


async def test_a_download_mirrors_the_remote_path(globus, project, approver):
    globus.seed_file("/lustre/orion/abc/data/a.nc", b"nc")
    text = await t._download_files(
        CFG,
        Path(project.output_dir),
        "frontier",
        ["/lustre/orion/abc/data/a.nc"],
        approver,
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


async def test_a_two_file_download_is_approved_once_and_keeps_the_remote_times(
    globus, project, approver
):
    globus.seed_file("/lustre/orion/abc/proj-shared/a.nc", b"a" * 1536)
    globus.seed_file("/lustre/orion/abc/proj-shared/b.nc", b"b" * 512)
    text = await t._download_files(
        CFG,
        Path(project.output_dir),
        "odo",
        ["/lustre/orion/abc/proj-shared/a.nc", "/lustre/orion/abc/proj-shared/b.nc"],
        approver,
    )
    [message] = approver.messages
    lines = message.splitlines()
    assert lines[0] == (
        "Download 2 files (2.0 KB) from Odo into /mnt/data/output/transfers/odo/:"
    )
    assert "/lustre/orion/abc/proj-shared/a.nc  1.5 KB" in lines[1]
    assert "/lustre/orion/abc/proj-shared/b.nc   512 B" in lines[2]
    assert "changed" not in message
    assert globus.ls_calls == [("odo-c", "/lustre/orion/abc/proj-shared")]
    for name in ("a.nc", "b.nc"):
        local = local_copy(project, "odo", f"/lustre/orion/abc/proj-shared/{name}")
        assert int(local.stat().st_mtime) == REMOTE_TIME
    assert text.splitlines()[0] == "Downloaded files:"
    assert len(text.splitlines()) == 3


async def test_a_declined_download_writes_nothing(globus, project):
    globus.seed_file("/d/a.txt", b"a")
    no = Approver(False)
    with pytest.raises(t.TransferNotApproved):
        await t._download_files(CFG, Path(project.output_dir), "odo", ["/d/a.txt"], no)
    assert len(no.messages) == 1
    assert globus.downloads == []
    assert not (Path(project.output_dir) / "transfers").exists()


async def test_nothing_to_fetch_asks_nothing(globus, project, approver):
    globus.seed_file("/d/a.txt", b"a")
    first = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert len(approver.messages) == 1
    assert globus.downloads == [("odo-c", "/d/a.txt")]
    assert text == first
    assert "/mnt/data/output/transfers/odo/d/a.txt" in text


async def test_a_file_that_changed_in_size_is_marked_and_fetched_again(
    globus, project, approver
):
    globus.seed_file("/d/a.txt", b"old")
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    globus.rewrite_file("/d/a.txt", b"newer contents")
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert "changed on Odo, replaces your local copy" in approver.messages[1]
    assert "changed" not in approver.messages[0]
    assert local_copy(project, "odo", "/d/a.txt").read_bytes() == b"newer contents"


async def test_a_file_changed_only_in_time_is_fetched_again(globus, project, approver):
    globus.seed_file("/d/a.txt", b"aaaa")
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    globus.rewrite_file("/d/a.txt", b"bbbb", last_modified="2026-10-23 00:00:00+00:00")
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert "changed on Odo" in approver.messages[1]
    assert local_copy(project, "odo", "/d/a.txt").read_bytes() == b"bbbb"


async def test_a_file_edited_locally_is_replaced_by_the_remote_one(
    globus, project, approver
):
    globus.seed_file("/d/a.txt", b"aaaa")
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    local = local_copy(project, "odo", "/d/a.txt")
    local.write_bytes(b"mine")
    os.utime(local, (REMOTE_TIME + 500, REMOTE_TIME + 500))
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert "changed on Odo, replaces your local copy" in approver.messages[1]
    assert local.read_bytes() == b"aaaa"


async def test_without_a_remote_time_the_size_alone_decides(globus, project, approver):
    globus.seed_file("/d/a.txt", b"aaaa", last_modified=None)
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    local = local_copy(project, "odo", "/d/a.txt")
    os.utime(local, (1000, 1000))
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert len(approver.messages) == 1
    globus.rewrite_file("/d/a.txt", b"bbbbbb", last_modified=None)
    await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert len(approver.messages) == 2
    assert local.read_bytes() == b"bbbbbb"


async def test_a_missing_file_is_reported_and_the_rest_downloaded(
    globus, project, approver
):
    globus.seed_file("/d/b.txt", b"b")
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt", "/d/b.txt"], approver
    )
    assert text.splitlines() == [
        "Downloaded files:",
        "/mnt/data/output/transfers/odo/d/b.txt",
        "Not on Odo:",
        "/d/a.txt",
    ]
    assert "a.txt" not in approver.messages[0]


async def test_a_path_given_twice_is_listed_fetched_and_reported_once(
    globus, project, approver
):
    globus.seed_file("/d/a.txt", b"a")
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt", "/d//a.txt"], approver
    )
    [message] = approver.messages
    assert message.count("/d/a.txt") == 1
    assert globus.downloads == [("odo-c", "/d/a.txt")]
    assert text.splitlines() == [
        "Downloaded files:",
        "/mnt/data/output/transfers/odo/d/a.txt",
    ]


async def test_a_file_gone_before_its_download_is_reported_missing(
    globus, project, approver
):
    globus.seed_file("/d/a.txt", b"a")
    globus.seed_file("/d/b.txt", b"b")
    del globus.files["/d/a.txt"]
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt", "/d/b.txt"], approver
    )
    assert text.splitlines() == [
        "Downloaded files:",
        "/mnt/data/output/transfers/odo/d/b.txt",
        "Not on Odo:",
        "/d/a.txt",
    ]


async def test_a_missing_folder_is_reported_as_missing_files(globus, project, approver):
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/nowhere/a.txt"], approver
    )
    assert text.splitlines() == ["Not on Odo:", "/nowhere/a.txt"]
    assert approver.messages == []


async def test_a_folder_that_cannot_be_listed_is_read_file_by_file(
    globus, project, approver
):
    globus.seed_file("/shared/a.txt", b"a")
    globus.unlistable.add("/shared")
    text = await t._download_files(
        CFG,
        Path(project.output_dir),
        "odo",
        ["/shared/a.txt", "/shared/gone.txt"],
        approver,
    )
    assert globus.stat_calls == [
        ("odo-c", "/shared/a.txt"),
        ("odo-c", "/shared/gone.txt"),
    ]
    assert local_copy(project, "odo", "/shared/a.txt").read_bytes() == b"a"
    assert "Not on Odo:\n/shared/gone.txt" in text


@pytest.mark.parametrize("path", ["/lustre/../etc/passwd", "d/a.txt", "/"])
async def test_a_bad_download_path_is_refused_before_globus(
    globus, project, approver, path
):
    with pytest.raises(ToolError):
        await t._download_files(
            CFG, Path(project.output_dir), "odo", ["/d/fine.txt", path], approver
        )
    assert globus.clients == 0
    assert approver.messages == []
    assert not (Path(project.output_dir) / "transfers").exists()


# --- uploading --------------------------------------------------------------


async def test_an_upload_lands_in_the_transfers_folder(globus, project, approver):
    (Path(project.uploads_dir) / "salt.xyz").write_text("Li 0 0 0", encoding="utf-8")
    text = await t._upload_files(
        CFG, project, "frontier", ["/mnt/data/uploads/salt.xyz"], "inputs", approver
    )
    assert globus.files["/r.jdoe.jobs/transfers/inputs/salt.xyz"] == b"Li 0 0 0"
    # From the folder holding /r, so a researcher who never submitted a job gets `.jobs` too.
    assert globus.mkdir_p_calls == [
        ("frontier-c", "/r.jdoe.jobs/transfers/inputs", "/")
    ]
    assert text.splitlines()[-1] == "/r.jdoe.jobs/transfers/inputs/salt.xyz"


async def test_an_upload_from_the_output_folder_to_the_transfers_root(
    globus, project, approver
):
    (Path(project.output_dir) / "plot.png").write_bytes(b"png")
    await t._upload_files(
        CFG, project, "odo", ["/mnt/data/output/plot.png"], "", approver
    )
    assert globus.files["/r.jdoe.jobs/transfers/plot.png"] == b"png"


async def test_a_second_upload_replaces_the_first(globus, project, approver):
    source = Path(project.uploads_dir) / "salt.xyz"
    source.write_text("old", encoding="utf-8")
    await t._upload_files(
        CFG, project, "odo", ["/mnt/data/uploads/salt.xyz"], "inputs", approver
    )
    source.write_text("new", encoding="utf-8")
    await t._upload_files(
        CFG, project, "odo", ["/mnt/data/uploads/salt.xyz"], "inputs", approver
    )
    assert globus.files["/r.jdoe.jobs/transfers/inputs/salt.xyz"] == b"new"


@pytest.mark.parametrize(
    "remote_dir", ["/lustre/orion/abc/proj-shared", "../src", "a/../../b", "a\\b"]
)
async def test_a_destination_outside_transfers_is_refused_before_globus(
    globus, project, approver, remote_dir
):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError, match="only under your transfers folder"):
        await t._upload_files(
            CFG, project, "odo", ["/mnt/data/uploads/f"], remote_dir, approver
        )
    assert globus.clients == 0
    assert approver.messages == []


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
    globus, project, approver, path
):
    with pytest.raises(ToolError, match="Cannot upload"):
        await t._upload_files(CFG, project, "odo", [path], "", approver)
    assert globus.clients == 0
    assert approver.messages == []


async def test_two_sources_with_one_name_are_refused(globus, project, approver):
    (Path(project.uploads_dir) / "a.txt").write_text("1", encoding="utf-8")
    (Path(project.output_dir) / "a.txt").write_text("2", encoding="utf-8")
    with pytest.raises(ToolError, match="same name"):
        await t._upload_files(
            CFG,
            project,
            "odo",
            ["/mnt/data/uploads/a.txt", "/mnt/data/output/a.txt"],
            "",
            approver,
        )
    assert globus.clients == 0
    assert approver.messages == []


async def test_an_upload_needs_the_remote_folder(globus, project, approver):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    cfg = CFG.model_copy(update={"odo_remote_dir": None})
    with pytest.raises(ToolError, match="Odo remote directory"):
        await t._upload_files(
            cfg, project, "odo", ["/mnt/data/uploads/f"], "", approver
        )
    assert globus.clients == 0
    assert approver.messages == []


# --- approval ---------------------------------------------------------------


async def test_a_no_raises_the_not_approved_error():
    with pytest.raises(t.TransferNotApproved) as raised:
        await t._require_approval(Approver(False), "Upload?")
    assert str(raised.value) == t.NOT_APPROVED
    assert "Do not retry" in t.NOT_APPROVED and "approval" in t.NOT_APPROVED
    await t._require_approval(Approver(True), "Upload?")


async def test_the_prompt_names_the_destination_and_the_file_with_its_size(
    globus, project, approver
):
    (Path(project.uploads_dir) / "salt.xyz").write_bytes(b"x" * 2048)
    await t._upload_files(
        CFG, project, "frontier", ["/mnt/data/uploads/salt.xyz"], "inputs", approver
    )
    [message] = approver.messages
    assert message.splitlines()[0] == (
        "Upload 1 file (2.0 KB) to Frontier, into /r.jdoe.jobs/transfers/inputs/:"
    )
    assert "salt.xyz" in message and "2.0 KB" in message
    assert "replaces" not in message


async def test_the_prompt_marks_only_the_file_it_replaces(globus, project, approver):
    (Path(project.uploads_dir) / "salt.xyz").write_text("new", encoding="utf-8")
    (Path(project.uploads_dir) / "mesh.h5").write_text("m", encoding="utf-8")
    globus.ls_entries["/r.jdoe.jobs/transfers/inputs"] = [
        {"name": "salt.xyz", "type": "file", "size": 3},
        {"name": "mesh.h5", "type": "dir"},
    ]
    await t._upload_files(
        CFG,
        project,
        "odo",
        ["/mnt/data/uploads/salt.xyz", "/mnt/data/uploads/mesh.h5"],
        "inputs",
        approver,
    )
    [message] = approver.messages
    assert message.startswith(
        "Upload 2 files (4 B) to Odo, into /r.jdoe.jobs/transfers/inputs/:"
    )
    [salt] = [line for line in message.splitlines() if "salt.xyz" in line]
    [mesh] = [line for line in message.splitlines() if "mesh.h5" in line]
    assert "replaces the file there" in salt
    assert "replaces" not in mesh


async def test_a_declined_upload_into_a_new_folder_leaves_nothing(globus, project):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    no = Approver(False)
    with pytest.raises(t.TransferNotApproved, match="did not approve"):
        await t._upload_files(CFG, project, "odo", ["/mnt/data/uploads/f"], "fresh", no)
    assert len(no.messages) == 1
    assert globus.uploads == []
    assert globus.mkdir_p_calls == []
    assert globus.files == {}


# --- an expired session -----------------------------------------------------


@pytest.fixture
def expired(monkeypatch) -> Expired:
    fake = Expired()
    monkeypatch.setattr(t, "create_globus_client", lambda **_: fake)
    return fake


async def test_an_expired_session_on_a_listing(expired):
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._list_files(CFG, "odo", "/data", False)


async def test_an_expired_session_on_a_download_is_not_a_missing_file(
    expired, project, approver
):
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._download_files(
            CFG, Path(project.output_dir), "odo", ["/d/a.txt"], approver
        )
    assert approver.messages == []


def hold(project, remote: str) -> None:
    local = local_copy(project, "odo", remote)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"held")


async def test_an_expired_session_with_every_file_held_reports_the_copies(
    expired, project, approver
):
    hold(project, "/d/a.txt")
    hold(project, "/d/b.txt")
    text = await t._download_files(
        CFG, Path(project.output_dir), "odo", ["/d/a.txt", "/d/b.txt"], approver
    )
    lines = text.splitlines()
    assert lines[0].startswith("Could not check Odo for newer versions:")
    assert "expired" in lines[0]
    assert lines[1:] == [
        "Downloaded files:",
        "/mnt/data/output/transfers/odo/d/a.txt",
        "/mnt/data/output/transfers/odo/d/b.txt",
    ]
    assert approver.messages == []


async def test_an_expired_session_with_one_file_not_held_still_raises(
    expired, project, approver
):
    hold(project, "/d/a.txt")
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._download_files(
            CFG, Path(project.output_dir), "odo", ["/d/a.txt", "/d/b.txt"], approver
        )
    assert approver.messages == []


async def test_not_connected_with_every_file_held_reports_the_copies(
    globus, project, approver
):
    hold(project, "/d/a.txt")
    text = await t._download_files(
        UserConfig(), Path(project.output_dir), "odo", ["/d/a.txt"], approver
    )
    assert text.splitlines()[0].startswith("Could not check Odo for newer versions:")
    assert "not connected for Odo" in text.splitlines()[0]
    assert "/mnt/data/output/transfers/odo/d/a.txt" in text
    assert approver.messages == []


async def test_not_connected_with_a_file_not_held_still_raises(
    globus, project, approver
):
    with pytest.raises(ToolError, match="not connected for Odo"):
        await t._download_files(
            UserConfig(), Path(project.output_dir), "odo", ["/d/a.txt"], approver
        )


async def test_an_expired_session_on_an_upload_comes_before_the_prompt(
    expired, project, approver
):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    with pytest.raises(GlobusSessionExpired, match="expired"):
        await t._upload_files(
            CFG, project, "odo", ["/mnt/data/uploads/f"], "", approver
        )
    assert approver.messages == []


# --- through the tool, as a call sees it ------------------------------------


async def test_a_tool_call_reads_paths_and_credentials_from_the_request(
    globus, project
):
    (Path(project.uploads_dir) / "f").write_text("x", encoding="utf-8")
    vista = {"user": CFG.model_dump(), "project_paths": project.model_dump()}

    async def elicit(message, response_type=None):
        return AcceptedElicitation(data=None)

    ctx = SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista)),
        elicit=elicit,
    )
    text = await t.hpc_upload_files(ctx, "odo", ["/mnt/data/uploads/f"], "in")
    assert "/r.jdoe.jobs/transfers/in/f" in text
