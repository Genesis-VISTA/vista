"""In-memory stand-in for an ``asyncssh.SSHClientConnection`` to a Slurm login node.

Commands are answered by ``handlers``: ``(substring, responder)`` pairs tried in
order, where the responder takes ``(command, input)`` and returns
``(exit_status, stdout, stderr)``. SFTP is served from a local directory that
stands in for the remote filesystem root, so ``/lustre/x`` lives at
``<root>/lustre/x``.
"""

from __future__ import annotations

import shlex
import types
from pathlib import Path
from typing import Callable

import asyncssh

Responder = Callable[[str, str | None], tuple[int, str, str]]


class _FakeFile:
    def __init__(self, path: Path):
        self._path = path

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self, size: int = -1, offset: int | None = None) -> bytes:
        data = self._path.read_bytes()
        start = offset or 0
        return data[start:] if size < 0 else data[start : start + size]


class _FakeOpen:
    """Awaitable-free async context manager, like asyncssh's ``sftp.open``."""

    def __init__(self, path: Path):
        self._path = path

    async def __aenter__(self):
        if not self._path.exists():
            raise asyncssh.SFTPNoSuchFile(str(self._path))
        return _FakeFile(self._path)

    async def __aexit__(self, *exc):
        return False


class FakeSftp:
    def __init__(self, conn: "FakeSshConn"):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _local(self, remote: str) -> Path:
        return self._conn.local(remote)

    async def stat(self, path: str):
        p = self._local(path)
        if not p.exists():
            raise asyncssh.SFTPNoSuchFile(path)
        return types.SimpleNamespace(size=p.stat().st_size)

    def open(self, path: str, mode: str = "r", **kwargs):
        return _FakeOpen(self._local(path))

    async def readdir(self, path: str):
        p = self._local(path)
        if not p.is_dir():
            raise asyncssh.SFTPNoSuchFile(path)
        return [
            types.SimpleNamespace(
                filename=c.name,
                attrs=types.SimpleNamespace(
                    size=c.stat().st_size,
                    type=asyncssh.FILEXFER_TYPE_REGULAR
                    if c.is_file()
                    else asyncssh.FILEXFER_TYPE_DIRECTORY,
                    permissions=None,
                ),
            )
            for c in sorted(p.iterdir())
        ]

    async def put(self, local: str, remote: str):
        dest = self._local(remote)
        if not dest.parent.is_dir():
            raise asyncssh.SFTPNoSuchFile(remote)
        dest.write_bytes(Path(local).read_bytes())
        self._conn.puts.append(remote)

    async def get(self, remote: str, local: str):
        src = self._local(remote)
        if not src.exists():
            raise asyncssh.SFTPNoSuchFile(remote)
        Path(local).write_bytes(src.read_bytes())
        self._conn.gets.append(remote)


class FakeSshConn:
    username = "researcher"
    """ The login at the far end of the hop chain. """

    def get_extra_info(self, name: str, default=None):
        return {"username": self.username}.get(name, default)

    def __init__(self, root: Path, handlers: list[tuple[str, Responder]] | None = None):
        self.root = root
        self.handlers = list(handlers or [])
        self.commands: list[tuple[str, str | None]] = []
        self.puts: list[str] = []
        self.gets: list[str] = []

    def local(self, remote: str) -> Path:
        return self.root / remote.lstrip("/")

    def on(self, substring: str, responder: Responder | tuple[int, str, str]):
        if isinstance(responder, tuple):
            fixed = responder
            responder = lambda cmd, inp: fixed  # noqa: E731
        self.handlers.append((substring, responder))

    def is_closed(self) -> bool:
        return False

    async def run(self, command: str, *, input: str | None = None, check: bool = False):
        # Unwrap `bash -c '<cmd>'` / `bash -lc '<cmd>'` back to the inner command.
        parts = shlex.split(command)
        inner = parts[-1] if parts[:1] == ["bash"] else command
        self.commands.append((inner, input))
        if inner.startswith("mkdir -p "):
            self.local(shlex.split(inner)[-1]).mkdir(parents=True, exist_ok=True)
            return types.SimpleNamespace(exit_status=0, stdout="", stderr="")
        for substring, responder in self.handlers:
            if substring in inner:
                code, out, err = responder(inner, input)
                return types.SimpleNamespace(exit_status=code, stdout=out, stderr=err)
        return types.SimpleNamespace(exit_status=0, stdout="", stderr="")

    async def start_sftp_client(self):
        return FakeSftp(self)

    def ran(self, substring: str) -> list[tuple[str, str | None]]:
        return [(c, i) for c, i in self.commands if substring in c]
