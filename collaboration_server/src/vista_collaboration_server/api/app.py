import ipaddress
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import API_VERSION
from ..config import CollabSettings
from ..db.db import init_db, make_engine
from ..db.schemas import ServerVersion
from ..services.items import NotFound, VersionConflict
from .items import router as items_router

log = logging.getLogger("vista.collaboration")


def _server_version() -> str:
    try:
        return version("vista-collaboration-server")
    except PackageNotFoundError:
        return "dev"


def create_app(settings: CollabSettings | None = None) -> FastAPI:
    """
    The collaboration server as an ASGI app, on `settings` (read from the
    environment when not given).

    A factory rather than a module-level app so each app has its own data: the
    backend's tests mount one per test, in-process, through
    `httpx.ASGITransport`. That transport does not run lifespan events, so
    such a caller enters `app.router.lifespan_context(app)` itself.
    """
    settings = settings or CollabSettings.model_validate({})

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.blobs_dir.mkdir(parents=True, exist_ok=True)
        engine = make_engine(settings.database_url)
        await init_db(engine)
        app.state.engine = engine
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="vista-collaboration-server",
        version=_server_version(),
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(VersionConflict)
    async def _conflict(request: Request, exc: VersionConflict) -> JSONResponse:
        """
        The head moved: say what it is now, so the publisher can fetch it and
        choose, rather than retry blind.
        """
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "head_version": exc.head_version},
        )

    @app.get("/version", tags=["meta"])
    async def get_version() -> ServerVersion:
        """What this server is, and which API it speaks. Needs no identity."""
        return ServerVersion(server_version=_server_version(), api_version=API_VERSION)

    app.include_router(items_router)
    return app


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    settings = CollabSettings.model_validate({})
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        force=True,
    )
    if not _is_loopback(settings.host):
        log.warning(
            "Listening on %s with no authentication: anyone who can reach this "
            "server can read and write everything on it.",
            settings.host,
        )
    log.info("Data directory: %s", settings.data_dir)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
