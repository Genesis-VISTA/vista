import pytest

from dev_mcp_server.lib.microsandbox_sandbox import MicrosandboxSandbox


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
