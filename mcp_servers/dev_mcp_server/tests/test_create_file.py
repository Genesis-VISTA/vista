"""`create_file` must report a failed write, not claim success."""

import shutil
from pathlib import Path

import pytest

import dev_mcp_server.server as server
from dev_mcp_server.lib.sandbox import UnSandbox

pytestmark = [
    pytest.mark.unit,
    pytest.mark.anyio,
    pytest.mark.skipif(shutil.which("tee") is None, reason="needs tee on the host"),
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def host_sandbox(monkeypatch):
    monkeypatch.setattr(server, "sandbox", UnSandbox(), raising=False)


async def test_writes_the_file(tmp_path: Path):
    target = tmp_path / "out.txt"
    assert (
        await server.create_file(str(target), "hello\n")
        == f"Successfully created {target}"
    )
    assert target.read_text(encoding="utf-8") == "hello\n"


async def test_failure_raises_with_the_reason(tmp_path: Path):
    with pytest.raises(ValueError, match="No such file or directory"):
        await server.create_file(str(tmp_path / "missing" / "out.txt"), "hello\n")
