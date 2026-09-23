import asyncio
import types

import pytest
from microsandbox import ExecEventType

from dev_mcp_server.lib.microsandbox_sandbox import MicrosandboxSandbox, _ExecProcess


class _AwaitableValue:
    def __init__(self, value: str) -> None:
        self._value = value

    def __await__(self):
        async def _resolve():
            return self._value

        return _resolve().__await__()


class _FakeSandbox:
    def __init__(self) -> None:
        self.name = _AwaitableValue("vista-sandbox-test")
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class TestMicrosandboxClose:
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.mark.anyio
    async def test_close_stops_and_removes_sandbox(self, monkeypatch):
        removed: list[str] = []

        async def fake_remove(name: str) -> None:
            removed.append(name)

        monkeypatch.setattr(
            "dev_mcp_server.lib.microsandbox_sandbox.MsbSandbox.remove",
            fake_remove,
        )

        sandbox_impl = _FakeSandbox()
        sandbox = MicrosandboxSandbox(sandbox_impl)

        await sandbox.close()

        assert sandbox_impl.stop_calls == 1
        assert removed == ["vista-sandbox-test"]


class _Event:
    def __init__(self, event_type, data: bytes | None = None, code: int | None = None):
        self.event_type = event_type
        self.data = data
        self.code = code


class _FakeSink:
    def __init__(self) -> None:
        self.ops: list[object] = []

    async def write(self, data: bytes) -> None:
        # Yield so a later write could overtake this one if writes weren't chained.
        await asyncio.sleep(0.01 if len(self.ops) == 0 else 0)
        self.ops.append(data)

    async def close(self) -> None:
        self.ops.append("close")


class _FakeHandle:
    """An `ExecHandle` stand-in that replays a fixed list of events."""

    def __init__(self, events: list[_Event], wait_code: int = 0, stdin: bool = True):
        self._events = events
        self._wait_code = wait_code
        self.sink = _FakeSink() if stdin else None
        self.wait_calls = 0

    def take_stdin(self):
        return self.sink

    def __aiter__(self):
        async def gen():
            for event in self._events:
                await asyncio.sleep(0)
                yield event

        return gen()

    async def wait(self):
        self.wait_calls += 1
        return self._wait_code, True

    async def kill(self) -> None:
        pass


@pytest.mark.anyio
class TestExecProcess:
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    async def test_separate_streams(self):
        handle = _FakeHandle(
            [
                _Event(ExecEventType.STDOUT, b"out\n"),
                _Event(ExecEventType.STDERR, b"err\n"),
                _Event(ExecEventType.EXITED, code=3),
            ]
        )
        proc = _ExecProcess(handle, combine_streams=False)
        stdout, stderr = await proc.communicate()
        assert (stdout, stderr) == (b"out\n", b"err\n")
        assert proc.returncode == 3
        assert handle.wait_calls == 0

    async def test_combined_streams(self):
        handle = _FakeHandle(
            [
                _Event(ExecEventType.STDOUT, b"out\n"),
                _Event(ExecEventType.STDERR, b"err\n"),
                _Event(ExecEventType.EXITED, code=0),
            ]
        )
        proc = _ExecProcess(handle, combine_streams=True)
        assert proc.stderr is None
        stdout, stderr = await proc.communicate()
        assert stdout == b"out\nerr\n"
        assert stderr is None

    async def test_communicate_writes_input_in_order_then_closes(self):
        handle = _FakeHandle([_Event(ExecEventType.EXITED, code=0)])
        proc = _ExecProcess(handle, combine_streams=False)
        proc.stdin.write(b"first ")
        proc.stdin.write(b"second")
        await proc.communicate(b" third")
        assert handle.sink.ops == [b"first ", b"second", b" third", "close"]
        assert proc.stdin.is_closing()

    async def test_communicate_without_input_still_closes_stdin(self):
        handle = _FakeHandle([_Event(ExecEventType.EXITED, code=0)])
        proc = _ExecProcess(handle, combine_streams=False)
        await proc.communicate()
        assert handle.sink.ops == ["close"]

    async def test_failed_event_sets_returncode_and_reports_message(self):
        handle = _FakeHandle([_Event(ExecEventType.FAILED, b"no such program\n")])
        proc = _ExecProcess(handle, combine_streams=False)
        _, stderr = await proc.communicate()
        assert proc.returncode == -1
        assert stderr == b"no such program\n"

    async def test_wait_falls_back_to_handle_without_exit_event(self):
        handle = _FakeHandle([_Event(ExecEventType.STDOUT, b"x")], wait_code=5)
        proc = _ExecProcess(handle, combine_streams=False)
        assert await proc.wait() == 5
        assert handle.wait_calls == 1

    async def test_no_stdin(self):
        handle = _FakeHandle([_Event(ExecEventType.EXITED, code=0)], stdin=False)
        proc = _ExecProcess(handle, combine_streams=False)
        assert proc.stdin is None
        assert await proc.communicate() == (b"", b"")


@pytest.mark.anyio
class TestMicrosandboxBuild:
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    async def test_image_only_pull_uses_resolved_msb(self, monkeypatch):
        calls: list[tuple[str, ...]] = []

        async def fake_check_output(*args):
            calls.append(args)

        async def no_digest(image):
            return None

        mod = "dev_mcp_server.lib.microsandbox_sandbox"
        monkeypatch.setattr(f"{mod}.check_output", fake_check_output)
        monkeypatch.setattr(f"{mod}._msb_image_digest", no_digest)
        monkeypatch.setattr(
            f"{mod}.resolve_runtime",
            lambda: types.SimpleNamespace(msb_path="/opt/msb/msb.exe"),
        )

        await MicrosandboxSandbox.build(image="example:latest")
        assert calls == [("/opt/msb/msb.exe", "pull", "example:latest")]
