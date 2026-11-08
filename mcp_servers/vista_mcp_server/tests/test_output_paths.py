"""
Job-output downloads must land inside the job's output directory on every host, and the
paths reported to the agent are sandbox paths, so POSIX whatever the host is.
"""

from pathlib import Path, PurePosixPath

import pytest

from fakes import FakeGlobusClient
from vista_mcp_server.lib.globus import GlobusFileNotFound, GlobusSessionExpired
from vista_mcp_server.submit_job_mcp import (
    SANDBOX_OUTPUT_DIR,
    _fetch_once,
    _job_output_paths,
)

pytestmark = pytest.mark.unit

SANDBOX_JOB_DIR = SANDBOX_OUTPUT_DIR / "12345"


@pytest.mark.parametrize(
    "name",
    [
        "/etc/x",
        "C:\\x",
        "C:x",
        "\\\\host\\share\\x",
        "\\x",
        "../x",
        "..\\x",
        "results/../../x",
        "results\\plot.png",
        "",
    ],
)
def test_absolute_or_escaping_names_are_rejected(tmp_path: Path, name: str):
    with pytest.raises(ValueError, match="Invalid path"):
        _job_output_paths(tmp_path / "12345", SANDBOX_JOB_DIR, name)


def test_a_symlink_out_of_the_output_dir_is_rejected(tmp_path: Path):
    out_dir = tmp_path / "12345"
    out_dir.mkdir()
    (out_dir / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="Invalid path"):
        _job_output_paths(out_dir, SANDBOX_JOB_DIR, "escape/secret.txt")


def test_relative_names_map_inside_both_dirs(tmp_path: Path):
    out_dir = tmp_path / "12345"
    local, sandbox = _job_output_paths(out_dir, SANDBOX_JOB_DIR, "results/summary.csv")
    assert local == out_dir / "results" / "summary.csv"
    assert sandbox == PurePosixPath("/mnt/data/output/12345/results/summary.csv")


def test_reported_sandbox_paths_use_forward_slashes(tmp_path: Path):
    _, sandbox = _job_output_paths(tmp_path, SANDBOX_JOB_DIR, "a/b/c.txt")
    assert str(sandbox) == "/mnt/data/output/12345/a/b/c.txt"
    assert "\\" not in str(sandbox)


# `_fetch_once` is the one download step shared by job outputs and the transfer tools.


def _client(fake: FakeGlobusClient):
    """A client getter that records whether it was ever asked for a client."""
    built: list[FakeGlobusClient] = []

    def get() -> FakeGlobusClient:
        built.append(fake)
        return fake

    return get, built


@pytest.mark.anyio
async def test_fetch_once_downloads_a_missing_file(tmp_path: Path):
    fake = FakeGlobusClient()
    fake.files["/r.out/1/a.txt"] = b"hello"
    get, _ = _client(fake)
    local = tmp_path / "1" / "a.txt"

    outcome = await _fetch_once(
        get, collection_id="c", remote_path="/r.out/1/a.txt", local_path=local
    )

    assert outcome == "downloaded"
    assert local.read_bytes() == b"hello"
    assert fake.downloads == [("c", "/r.out/1/a.txt")]


@pytest.mark.anyio
async def test_fetch_once_serves_a_local_copy_without_building_a_client(tmp_path: Path):
    local = tmp_path / "a.txt"
    local.write_bytes(b"cached")
    get, built = _client(FakeGlobusClient())

    outcome = await _fetch_once(
        get, collection_id="c", remote_path="/r/a.txt", local_path=local
    )

    assert outcome == "cached"
    assert built == []


@pytest.mark.anyio
async def test_fetch_once_fetches_over_an_empty_local_file(tmp_path: Path):
    fake = FakeGlobusClient()
    fake.files["/r/a.txt"] = b"full"
    local = tmp_path / "a.txt"
    local.write_bytes(b"")
    get, _ = _client(fake)

    assert (
        await _fetch_once(
            get, collection_id="c", remote_path="/r/a.txt", local_path=local
        )
        == "downloaded"
    )
    assert local.read_bytes() == b"full"


@pytest.mark.anyio
async def test_fetch_once_lets_a_missing_remote_file_propagate(tmp_path: Path):
    get, _ = _client(FakeGlobusClient())
    with pytest.raises(GlobusFileNotFound):
        await _fetch_once(
            get, collection_id="c", remote_path="/r/none", local_path=tmp_path / "none"
        )


@pytest.mark.anyio
async def test_fetch_once_lets_an_expired_session_propagate(tmp_path: Path):
    class Expired(FakeGlobusClient):
        async def download_file(self, **_):
            raise GlobusSessionExpired("expired")

    get, _ = _client(Expired())
    with pytest.raises(GlobusSessionExpired):
        await _fetch_once(
            get, collection_id="c", remote_path="/r/a", local_path=tmp_path / "a"
        )
