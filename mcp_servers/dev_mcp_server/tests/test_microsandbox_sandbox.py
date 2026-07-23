import pytest

from dev_mcp_server.lib.microsandbox_sandbox import MicrosandboxSandbox


class _AwaitableValue:
    def __init__(self, value: str) -> None:
        self._value = value

    def __await__(self):
        async def _resolve():
            return self._value

        return _resolve().__await__()


class _NewLifecycleSandbox:
    def __init__(self) -> None:
        self.name = _AwaitableValue("vista-sandbox-test")
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class _OldLifecycleSandbox:
    def __init__(self) -> None:
        self.name = _AwaitableValue("vista-sandbox-test")
        self.stop_and_wait_calls = 0

    async def stop_and_wait(self) -> None:
        self.stop_and_wait_calls += 1


class TestMicrosandboxClose:
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.mark.anyio
    async def test_close_uses_stop_when_stop_and_wait_is_unavailable(self, monkeypatch):
        removed: list[str] = []

        async def fake_remove(name: str) -> None:
            removed.append(name)

        monkeypatch.setattr(
            "dev_mcp_server.lib.microsandbox_sandbox.MsbSandbox.remove",
            fake_remove,
        )

        sandbox_impl = _NewLifecycleSandbox()
        sandbox = MicrosandboxSandbox(sandbox_impl)

        await sandbox.close()

        assert sandbox_impl.stop_calls == 1
        assert removed == ["vista-sandbox-test"]

    @pytest.mark.anyio
    async def test_close_preserves_stop_and_wait_for_older_versions(self, monkeypatch):
        removed: list[str] = []

        async def fake_remove(name: str) -> None:
            removed.append(name)

        monkeypatch.setattr(
            "dev_mcp_server.lib.microsandbox_sandbox.MsbSandbox.remove",
            fake_remove,
        )

        sandbox_impl = _OldLifecycleSandbox()
        sandbox = MicrosandboxSandbox(sandbox_impl)

        await sandbox.close()

        assert sandbox_impl.stop_and_wait_calls == 1
        assert removed == ["vista-sandbox-test"]
