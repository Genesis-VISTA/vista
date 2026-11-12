"""Live checks of the dev MCP server's tools against a real microsandbox microVM.

Covers the scenarios in openspec/changes/windows-support/specs/code-execution-sandbox, and the
background-task cases of openspec/changes/background-tasks, which also run against the container
backend. Opt-in: `VISTA_RUN_SANDBOX=1 MSB_HOME=~/.msb-live uv run pytest -m sandbox`. The first run
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
from dev_mcp_server.lib.container_sandbox import (
    ContainerSandbox,
    resolve_container_runtime,
)
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


async def test_create_file_larger_than_one_protocol_frame(sandbox):
    content = "0123456789abcdef\n" * (5 * 1024 * 1024 // 17)
    await asyncio.wait_for(server.create_file("/mnt/big.txt", content), 60)
    out = await asyncio.wait_for(server.run_bash("wc -c < /mnt/big.txt", _Ctx()), 30)
    assert int(out) == len(content)


async def test_create_file_reports_failure(sandbox):
    with pytest.raises(ValueError, match="No such file or directory"):
        await asyncio.wait_for(server.create_file("/nonexistent/x.txt", "hi\n"), 30)


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


# ─── Commands that outlive their call (openspec/changes/background-tasks, task 1.5) ──────────
#
# Run against both backends. The container one needs docker or podman, nothing else.


async def _spawn(backend: str, volume: Path):
    if backend == "microsandbox":
        if "MSB_HOME" not in os.environ:
            pytest.skip("set MSB_HOME so the live tests don't migrate ~/.microsandbox")
        return await MicrosandboxSandbox.spawn(
            volumes=[(volume, "/mnt", "w")],
            dockerfile=settings.dockerfile,
            image=settings.image,
        )
    try:
        resolve_container_runtime()
    except RuntimeError as e:
        pytest.skip(str(e))
    return await ContainerSandbox.spawn(
        volumes=[(volume, "/mnt", "w")],
        dockerfile=settings.dockerfile,
        image=settings.image,
    )


@pytest.fixture(scope="module", params=["microsandbox", "container"])
async def task_backend(request, tmp_path_factory):
    volume = tmp_path_factory.mktemp(f"tasks-{request.param}")
    sb = await _spawn(request.param, volume)
    yield sb, volume
    await sb.close()


@pytest.fixture
def task_sandbox(task_backend, monkeypatch):
    sb, volume = task_backend
    monkeypatch.setattr(server, "sandbox", sb, raising=False)
    return sb, volume


async def _guest(sb, script: str) -> str:
    proc = await sb.exec("bash", args=["-c", script])
    stdout, _ = await asyncio.wait_for(proc.communicate(), 30)
    return stdout.decode().strip()


async def _guest_alive(sb, pids: list[int]) -> list[int]:
    out = await _guest(
        sb, " ".join(f"kill -0 {p} 2>/dev/null && echo {p};" for p in pids)
    )
    return [int(p) for p in out.split()]


async def test_task_outlives_its_call(task_sandbox):
    sb, volume = task_sandbox
    t0 = time.monotonic()
    out = await asyncio.wait_for(
        server.run_bash(
            "echo started; sleep 3; echo done",
            _Ctx(),
            background=True,
            task_id="live-1",
        ),
        30,
    )
    assert time.monotonic() - t0 < 10, "start must not wait for the command"
    assert "/mnt/data/tasks/live-1.log" in out
    log = volume / "data" / "tasks" / "live-1.log"
    assert await server.wait_task("live-1", max_wait=0) == {"state": "running"}
    assert log.read_text(encoding="utf-8").split() == ["started"]
    assert await asyncio.wait_for(server.wait_task("live-1", max_wait=30), 40) == {
        "state": "exited",
        "exit_status": 0,
    }
    assert log.read_text(encoding="utf-8").split() == ["started", "done"]


async def test_task_exit_status(task_sandbox):
    await server.run_bash("exit 7", _Ctx(), background=True, task_id="live-2")
    assert await asyncio.wait_for(server.wait_task("live-2", max_wait=30), 40) == {
        "state": "exited",
        "exit_status": 7,
    }


async def test_stop_ends_descendants_and_spares_other_tasks(task_sandbox):
    sb, volume = task_sandbox
    pids = "/mnt/live-3.pids"
    await server.run_bash(
        f"sleep 300 & echo $! >> {pids}; "
        f"bash -c 'sleep 301 & echo $! >> {pids}; wait' & echo $! >> {pids}; wait",
        _Ctx(),
        background=True,
        task_id="live-3",
    )
    await server.run_bash("sleep 300", _Ctx(), background=True, task_id="live-4")
    pid_file = volume / "live-3.pids"
    deadline = time.monotonic() + 15
    while (
        len(pid_file.read_text(encoding="utf-8").split() if pid_file.exists() else [])
        < 3
    ):
        assert time.monotonic() < deadline, "descendants did not start"
        await asyncio.sleep(0.2)
    descendants = [int(p) for p in pid_file.read_text(encoding="utf-8").split()]
    assert await _guest_alive(sb, descendants) == descendants

    assert await asyncio.wait_for(server.signal_task("live-3"), 30) == {
        "state": "ended"
    }

    assert await _guest_alive(sb, descendants) == []
    assert await server.wait_task("live-3", max_wait=5) == {"state": "gone"}
    assert await server.wait_task("live-4", max_wait=0) == {"state": "running"}
    await server.signal_task("live-4")


@pytest.mark.parametrize("backend", ["microsandbox", "container"])
async def test_closing_the_sandbox_ends_its_tasks(backend, tmp_path, monkeypatch):
    sb = await _spawn(backend, tmp_path)
    monkeypatch.setattr(server, "sandbox", sb, raising=False)
    try:
        await server.run_bash(
            "while :; do date +%s%N >> /mnt/beat; sleep 0.2; done",
            _Ctx(),
            background=True,
            task_id="live-5",
        )
        await asyncio.sleep(1)
    finally:
        await sb.close()
    beat = tmp_path / "beat"
    await asyncio.sleep(2)  # the container backend is removed asynchronously
    size = beat.stat().st_size
    await asyncio.sleep(2)
    assert beat.stat().st_size == size, "the task kept running after close"
