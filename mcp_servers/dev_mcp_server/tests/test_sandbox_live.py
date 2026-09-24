"""Live checks of the dev MCP server's tools against a real microsandbox microVM.

Covers the scenarios in openspec/changes/windows-support/specs/code-execution-sandbox.
Opt-in: `VISTA_RUN_SANDBOX=1 MSB_HOME=~/.msb-live uv run pytest -m sandbox`. The first run
builds the sandbox image with docker or podman and loads it into that store.

`MSB_HOME` must be set so these tests never open (and migrate) the shared `~/.microsandbox`
that other checkouts may still be using.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest

import dev_mcp_server.server as server
from dev_mcp_server.config import settings
from dev_mcp_server.lib.microsandbox_sandbox import MicrosandboxSandbox, MsbSandbox

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("VISTA_RUN_SANDBOX") != "1",
        reason="set VISTA_RUN_SANDBOX=1 to run tests against a live microVM",
    ),
]


class _Ctx:
    """Records when each streamed line reached the tool's caller."""

    def __init__(self):
        self.stamps: list[tuple[float, str]] = []

    async def info(self, msg: str) -> None:
        self.stamps.append((time.monotonic(), msg))


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def volume_dir(tmp_path_factory) -> Path:
    vol = tmp_path_factory.mktemp("vol")
    (vol / "from_host.txt").write_text("hello from host\n", encoding="utf-8")
    return vol


@pytest.fixture(scope="module")
async def sandbox(volume_dir):
    if "MSB_HOME" not in os.environ:
        pytest.skip("set MSB_HOME so the live tests don't migrate ~/.microsandbox")
    sb = await MicrosandboxSandbox.spawn(
        volumes=[(volume_dir, "/mnt", "w")],
        dockerfile=settings.dockerfile,
        image=settings.image,
    )
    server.sandbox = sb
    yield sb
    await sb.close()


async def test_output_streams_incrementally(sandbox):
    ctx = _Ctx()
    out = await asyncio.wait_for(
        server.run_bash("for i in 1 2 3; do echo line$i; sleep 1; done", ctx), 30
    )
    assert out.split() == ["line1", "line2", "line3"]
    gaps = [b[0] - a[0] for a, b in zip(ctx.stamps, ctx.stamps[1:])]
    assert len(gaps) == 2 and all(g > 0.7 for g in gaps), gaps


async def test_python_output_streams_incrementally(sandbox):
    ctx = _Ctx()
    script = "import time\nfor i in range(3):\n    print(i)\n    time.sleep(1)\n"
    await asyncio.wait_for(server.run_bash(f"python3 -c '{script}'", ctx), 30)
    gaps = [b[0] - a[0] for a, b in zip(ctx.stamps, ctx.stamps[1:])]
    assert len(gaps) == 2 and all(g > 0.7 for g in gaps), gaps


async def test_combined_streams(sandbox):
    out = await asyncio.wait_for(
        server.run_bash("echo out; echo err >&2; exit 3", _Ctx()), 30
    )
    assert "out" in out and "err" in out


async def test_separate_streams_are_byte_exact(sandbox):
    proc = await sandbox.exec(
        "bash", args=["-c", r"printf 'a\nb\r\nc'; printf 'e\n' >&2"]
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 30)
    assert stdout == b"a\nb\r\nc"
    assert stderr == b"e\n"


async def test_exit_status(sandbox):
    proc = await sandbox.exec("bash", args=["-c", "exit 7"])
    await asyncio.wait_for(proc.communicate(), 30)
    assert proc.returncode == 7


async def test_missing_program_fails_promptly(sandbox):
    proc = await sandbox.exec("definitely-not-a-program")
    await asyncio.wait_for(proc.communicate(), 30)
    assert proc.returncode not in (0, None)


async def test_cwd_and_env(sandbox):
    proc = await sandbox.exec(
        "bash", args=["-c", 'echo "$PWD $GREETING"'], cwd="/tmp", env={"GREETING": "hi"}
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), 30)
    assert stdout == b"/tmp hi\n"


async def test_create_file_round_trips(sandbox):
    await asyncio.wait_for(
        server.create_file("/mnt/created.txt", "written by create_file\n"), 30
    )
    out = await asyncio.wait_for(server.run_bash("cat /mnt/created.txt", _Ctx()), 30)
    assert "written by create_file" in out


async def test_no_input_supplied_sees_eof(sandbox):
    proc = await sandbox.exec("cat")
    stdout, _ = await asyncio.wait_for(proc.communicate(), 30)
    assert stdout == b"" and proc.returncode == 0


async def test_view_file_and_directory(sandbox):
    out = await asyncio.wait_for(server.view("/mnt/from_host.txt", None), 30)
    assert "hello from host" in out
    out = await asyncio.wait_for(server.view("/mnt", None), 30)
    assert "from_host.txt" in out


async def test_writable_mount_round_trip(sandbox, volume_dir):
    out = await asyncio.wait_for(server.run_bash("cat /mnt/from_host.txt", _Ctx()), 30)
    assert "hello from host" in out
    await asyncio.wait_for(
        server.run_bash("echo from guest > /mnt/from_guest.txt", _Ctx()), 30
    )
    assert (volume_dir / "from_guest.txt").read_text(encoding="utf-8") == "from guest\n"


async def test_dns_resolves(sandbox):
    out = await asyncio.wait_for(
        server.run_bash("getent hosts pypi.org && echo DNS_OK", _Ctx()), 60
    )
    assert "DNS_OK" in out


async def test_public_https_egress(sandbox):
    out = await asyncio.wait_for(
        server.run_bash(
            "curl -s -o /dev/null -w '%{http_code}' https://pypi.org/simple/; echo",
            _Ctx(),
        ),
        60,
    )
    assert out.strip().endswith("200")


async def test_close_removes_sandbox(volume_dir):
    if "MSB_HOME" not in os.environ:
        pytest.skip("set MSB_HOME so the live tests don't migrate ~/.microsandbox")
    sb = await MicrosandboxSandbox.spawn(
        volumes=[(volume_dir, "/mnt", "r")],
        dockerfile=settings.dockerfile,
        image=settings.image,
    )
    name = await sb._sandbox.name
    await sb.close()
    with pytest.raises(Exception):
        await MsbSandbox.get(name)
