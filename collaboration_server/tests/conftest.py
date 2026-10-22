import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from vista_collaboration_server.api.app import create_app
from vista_collaboration_server.config import CollabSettings
from vista_collaboration_server.services.identity import (
    DISPLAY_NAME_HEADER,
    INSTALL_ID_HEADER,
)

INSTALL_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_INSTALL_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> CollabSettings:
    data_dir = tmp_path / "collab"
    # Every setting a test relies on is explicit, so a `VISTA_COLLAB_*` value in
    # the environment or a `.env` (a real database URL above all) cannot leak in.
    return CollabSettings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{data_dir / 'collaboration.db'}",
        max_blob_size=1024,
    )


@pytest.fixture
async def app(settings):
    """The server on a throwaway data directory, with its lifespan entered."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


def headers(install_id: uuid.UUID = INSTALL_ID, name: str | None = "Ada") -> dict:
    out = {INSTALL_ID_HEADER: str(install_id)}
    if name is not None:
        out[DISPLAY_NAME_HEADER] = name
    return out


@pytest.fixture
async def anon(app):
    """A client that names no one."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://collab"
    ) as client:
        yield client


@pytest.fixture
async def client(app):
    """A client that names itself as `INSTALL_ID`, "Ada"."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://collab", headers=headers()
    ) as client:
        yield client
