"""
A real collaboration server on loopback, and VISTA installs to use it as.

Hosted forums are git over HTTP, which git cannot do through the in-process
`httpx.ASGITransport` the `collab_server` fixture uses. So the server runs under
`uvicorn` on a free loopback port in `dev-headers` mode (it believes the install
id and display name a client sends, which is what the backend's sign-in headers
carry without a token), with a temporary data directory and no realm, gateway
or network. The pattern is `collaboration_server/tests/test_forums.py`'s `live`.

`Install` is one researcher's VISTA: a database of its own, its own settings row
(so its own install id, which is its identity on the server) and its own
`data_dir`, which holds its forum repositories and its host id. Two of them in
one test process are two installs on one forum; `Install.active` makes one of
them the install the process is, for as long as a block runs.
"""

import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import ForumSettings, settings
from vista_backend.db.schemas import LocalSettingsTable, LocalSettingsUpdate
from vista_backend.services import local_settings

DISPLAY_NAME_HEADER = "X-Vista-Display-Name"
INSTALL_ID_HEADER = "X-Vista-Install-Id"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LoopbackServer:
    """The collaboration server on `url`, and a plain client for each account."""

    def __init__(self, collab_settings, port: int):
        self.settings = collab_settings
        self.port = port
        self.url = f"http://127.0.0.1:{port}"

    def api(self, install_id: uuid.UUID, name: str | None = None) -> httpx.Client:
        headers = {INSTALL_ID_HEADER: str(install_id)}
        if name:
            headers[DISPLAY_NAME_HEADER] = name
        return httpx.Client(base_url=self.url, headers=headers)

    def forum_exists(self, install_id: uuid.UUID, forum_id: str) -> bool:
        """Whether the account can see the forum (its push records answer)."""
        with self.api(install_id) as api:
            return api.get(f"/forums/{forum_id}/pushes").status_code == 200

    def wait_for_records(self, forum_url: str) -> None:
        """
        git tells a client its push is accepted before the server's
        `post-receive` has written the records: wait for the hook to finish.
        """
        forum_id = forum_url.rsplit("/", 1)[-1].removesuffix(".git")
        pending = self.settings.forums_dir / f"{forum_id}.git" / "vista-pending"
        deadline = time.monotonic() + 30
        while pending.is_dir() and any(pending.iterdir()):
            assert time.monotonic() < deadline, "post-receive did not finish"
            time.sleep(0.02)


def run_server(tmp_path: Path):
    """A context manager running the server until it exits. Yields a `LoopbackServer`."""
    from contextlib import contextmanager

    from vista_collaboration_server.api.app import create_app
    from vista_collaboration_server.config import CollabSettings

    @contextmanager
    def running():
        data_dir = tmp_path / "collaboration-server"
        # The database URL too: one in the environment would point the test at a
        # developer's real database.
        collab = CollabSettings(
            data_dir=data_dir,
            database_url=f"sqlite+aiosqlite:///{data_dir / 'collaboration.db'}",
            auth_mode="dev-headers",
            forum_run_expiry_seconds=2700,
        )
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(collab), host="127.0.0.1", port=port, log_level="warning"
            )
        )
        server.install_signal_handlers = lambda: None  # not the main thread
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, (
                "the server did not start"
            )
            time.sleep(0.02)
        try:
            yield LoopbackServer(collab, port)
        finally:
            server.should_exit = True
            thread.join(timeout=30)

    return running()


@dataclass
class Install:
    """One researcher's VISTA: its database, settings row and data directory."""

    name: str
    data_dir: Path
    engine: object
    session: AsyncSession
    install_id: uuid.UUID

    @property
    def sign_in(self) -> dict[str, str]:
        return {INSTALL_ID_HEADER: str(self.install_id), DISPLAY_NAME_HEADER: self.name}

    async def close(self) -> None:
        await self.session.close()
        await self.engine.dispose()  # type: ignore[attr-defined]

    @asynccontextmanager
    async def active(self) -> AsyncIterator[AsyncSession]:
        """This install is the one the backend's settings and files are, for the block."""
        before = settings.data_dir
        settings.data_dir = self.data_dir
        try:
            yield self.session
        finally:
            settings.data_dir = before


async def make_install(tmp_path: Path, name: str, server_url: str | None) -> Install:
    """A new install on `server_url`, named `name`; dispose with `await install.close()`."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / name.lower()}.db",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session = AsyncSession(engine)
    row = LocalSettingsTable()
    install_id = row.install_id  # before the commit expires the row
    session.add(row)
    await session.commit()
    install = Install(
        name=name,
        data_dir=tmp_path / f"{name.lower()}-data",
        engine=engine,
        session=session,
        install_id=install_id,
    )
    await local_settings.update(
        session,
        LocalSettingsUpdate.model_validate(
            {"display_name": name, "collaboration_server_url": server_url}
        ),
    )
    await session.commit()
    return install


def forum_settings() -> ForumSettings:
    """What a deployment with labs on sets: enabled, with a generous git timeout."""
    return ForumSettings(enabled=True, timeout=60.0)
