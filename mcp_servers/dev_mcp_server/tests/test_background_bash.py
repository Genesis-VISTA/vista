"""Background `run_bash`, `wait_task` and `signal_task` against the host (`UnSandbox`).

Covers openspec/changes/background-tasks tasks 1.2-1.4. The same cases against real sandboxes
are in `test_sandbox_live.py`.
"""

import asyncio
import os
import shutil
import stat
import sys
import time
from pathlib import Path, PurePosixPath

import pytest

import dev_mcp_server.server as server
from dev_mcp_server.config import settings
from dev_mcp_server.lib.sandbox import UnSandbox

pytestmark = [
    pytest.mark.unit,
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform == "win32" or shutil.which("perl") is None,
        reason="needs a POSIX host with bash and perl, like the sandbox image",
    ),
]

# util-linux `setsid -f`: fork, start a new session in the child, exec. macOS has no setsid.
_SETSID_SHIM = """\
import os, sys
args = sys.argv[1:]
if args[:1] == ["-f"]:
    args = args[1:]
if os.fork():
    os._exit(0)
os.setsid()
os.execvp(args[0], args)
"""


class _Ctx:
    async def info(self, msg: str) -> None:
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def host_sandbox(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "sandbox", UnSandbox(), raising=False)
    monkeypatch.setattr(settings, "tasks_dir", PurePosixPath(tmp_path / "tasks"))
    monkeypatch.setattr(settings, "task_kill_grace", 2)
    if shutil.which("setsid") is None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "setsid"
        shim.write_text(f"#!{sys.executable}\n{_SETSID_SHIM}", encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
async def started():
    """Start background tasks, and signal any still running when the test ends."""
    ids: list[str] = []

    async def start(task_id: str, command: str) -> str:
        ids.append(task_id)
        return await server.run_bash(command, _Ctx(), background=True, task_id=task_id)

    yield start
    for task_id in ids:
        await server.signal_task(task_id)


def _tasks_dir() -> Path:
    return Path(settings.tasks_dir)


async def _until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_start_returns_at_once_and_log_grows(started):
    t0 = time.monotonic()
    out = await started(
        "bg-1", "for i in 1 2 3; do echo line$i; sleep 0.3; done; sleep 30"
    )
    assert time.monotonic() - t0 < 5
    log = _tasks_dir() / "bg-1.log"
    assert str(log) in out
    await _until(
        lambda: log.read_text(encoding="utf-8").split() == ["line1", "line2", "line3"]
    )
    assert (await server.wait_task("bg-1", max_wait=0)) == {"state": "running"}


async def test_exit_status_lands_in_exit_file(started):
    await started("bg-2", "echo out; echo err >&2; exit 7")
    assert await server.wait_task("bg-2", max_wait=10) == {
        "state": "exited",
        "exit_status": 7,
    }
    assert (_tasks_dir() / "bg-2.exit").read_text(encoding="utf-8").strip() == "7"
    assert (_tasks_dir() / "bg-2.log").read_text(encoding="utf-8").split() == [
        "out",
        "err",
    ]


async def test_custom_log_path(started, tmp_path: Path):
    log = tmp_path / "logs" / "mine.log"
    await server.run_bash(
        "echo hi", _Ctx(), background=True, task_id="bg-3", log_path=str(log)
    )
    assert (await server.wait_task("bg-3", max_wait=10))["state"] == "exited"
    assert log.read_text(encoding="utf-8") == "hi\n"


async def test_command_does_not_read_the_callers_stdin(started):
    await started("bg-4", "cat; echo after")
    assert await server.wait_task("bg-4", max_wait=10) == {
        "state": "exited",
        "exit_status": 0,
    }


async def test_background_needs_a_task_id():
    with pytest.raises(ValueError, match="task_id"):
        await server.run_bash("true", _Ctx(), background=True)


@pytest.mark.parametrize("task_id", ["../x", "a/b", "", "-x", "a b"])
async def test_unsafe_task_ids_are_refused(task_id):
    with pytest.raises(ValueError):
        await server.run_bash("true", _Ctx(), background=True, task_id=task_id)


async def test_task_id_cannot_be_reused(started):
    await started("bg-5", "true")
    await server.wait_task("bg-5", max_wait=10)
    with pytest.raises(RuntimeError, match="already exists"):
        await started("bg-5", "true")


async def test_foreground_call_is_unchanged():
    assert await server.run_bash("echo hello; exit 3", _Ctx()) == "hello\n"
    assert not _tasks_dir().exists()


async def test_log_is_capped_while_the_command_runs_on(started, monkeypatch):
    monkeypatch.setattr(settings, "task_log_cap", 1000)
    await started("bg-6", "yes | head -c 50000; echo finished > /dev/stderr; exit 4")
    assert await server.wait_task("bg-6", max_wait=10) == {
        "state": "exited",
        "exit_status": 4,
    }
    data = (_tasks_dir() / "bg-6.log").read_bytes()
    assert data == b"y\n" * 500 + b"\n[output cut off at 1000 bytes]\n"


async def test_output_at_exactly_the_cap_is_not_cut(started, monkeypatch):
    monkeypatch.setattr(settings, "task_log_cap", 6)
    await started("bg-7", "printf 'abcdef'")
    await server.wait_task("bg-7", max_wait=10)
    assert (_tasks_dir() / "bg-7.log").read_bytes() == b"abcdef"


def test_cut_off_line_names_megabytes():
    from dev_mcp_server.lib.tasks import cut_off_line

    assert cut_off_line(10 * 1024 * 1024) == "[output cut off at 10 MB]"


async def test_wait_returns_after_max_wait(started):
    await started("bg-8", "sleep 30")
    t0 = time.monotonic()
    assert await server.wait_task("bg-8", max_wait=1) == {"state": "running"}
    assert 0.9 < time.monotonic() - t0 < 5


async def test_wait_for_an_unknown_task():
    assert await server.wait_task("never-started", max_wait=5) == {"state": "unknown"}


async def test_signal_ends_children_and_grandchildren_only(started, tmp_path: Path):
    pids = tmp_path / "pids"
    await started(
        "bg-9",
        f"sleep 300 & echo $! >> {pids}; "
        f"bash -c 'sleep 301 & echo $! >> {pids}; wait' & echo $! >> {pids}; wait",
    )
    await started("bg-10", "sleep 300")
    await _until(
        lambda: pids.exists() and len(pids.read_text(encoding="utf-8").split()) == 3
    )
    descendants = [int(p) for p in pids.read_text(encoding="utf-8").split()]
    assert all(_alive(p) for p in descendants)

    assert await server.signal_task("bg-9") == {"state": "ended"}

    await _until(lambda: not any(_alive(p) for p in descendants))
    assert await server.wait_task("bg-9", max_wait=5) == {"state": "gone"}
    assert not (_tasks_dir() / "bg-9.exit").exists()
    assert await server.wait_task("bg-10", max_wait=0) == {"state": "running"}


async def test_wait_held_while_the_task_is_signalled(started):
    await started("bg-11", "sleep 300")
    waiting = asyncio.ensure_future(server.wait_task("bg-11", max_wait=30))
    await asyncio.sleep(0.5)
    await server.signal_task("bg-11")
    assert await asyncio.wait_for(waiting, 10) == {"state": "gone"}


async def test_signal_kills_a_command_that_ignores_term(started):
    await started("bg-12", "trap '' TERM; sleep 300 & wait; sleep 300")
    await asyncio.sleep(0.5)
    assert await server.signal_task("bg-12") == {"state": "killed"}
    assert await server.wait_task("bg-12", max_wait=5) == {"state": "gone"}


async def test_signal_an_ended_or_unknown_task(started):
    await started("bg-13", "true")
    await server.wait_task("bg-13", max_wait=10)
    assert await server.signal_task("bg-13") == {"state": "not-running"}
    assert await server.signal_task("nope") == {"state": "unknown"}
