import ipaddress
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import API_VERSION
from ..config import CollabSettings
from ..db.db import init_db, make_engine
from ..db.schemas import ServerVersion, UserPublic
from ..services.identity import TokenVerifier, UserDep
from ..services.items import ItemExists, NotFound, VersionConflict
from .items import router as items_router
from .sign_in import router as sign_in_router

log = logging.getLogger("vista.collaboration")


def _server_version() -> str:
    try:
        return version("vista-collaboration-server")
    except PackageNotFoundError:
        return "dev"


def create_app(
    settings: CollabSettings | None = None, *, verifier: TokenVerifier | None = None
) -> FastAPI:
    """
    The collaboration server as an ASGI app, on `settings` (read from the
    environment when not given). `verifier` checks tokens in `oidc` mode; by
    default one that fetches the realm's keys, and in tests one given them.

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
    app.state.verifier = verifier or TokenVerifier(settings)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ItemExists)
    async def _exists(request: Request, exc: ItemExists) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

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

    @app.get("/me", tags=["meta"])
    async def get_me(user: UserDep) -> UserPublic:
        """Who this server takes the caller to be: how a backend checks it is signed in."""
        return UserPublic(sub=user.sub, email=user.email, name=user.name)

    app.include_router(items_router)
    app.include_router(sign_in_router)
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
    if settings.auth_mode == "dev-headers" and not _is_loopback(settings.host):
        log.critical(
            "Refusing to listen on %s with auth_mode=dev-headers: anyone who "
            "could reach this server could read and write everything on it. "
            "Use auth_mode=oidc, or listen on loopback.",
            settings.host,
        )
        sys.exit(2)
    if (settings.ssl_certfile is None) != (settings.ssl_keyfile is None):
        log.critical("Set both ssl_certfile and ssl_keyfile, or neither.")
        sys.exit(2)
    log.info("Data directory: %s", settings.data_dir)
    log.info("Authentication: %s", settings.auth_mode)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        ssl_certfile=settings.ssl_certfile,
        ssl_keyfile=settings.ssl_keyfile,
        # The proxy strips the prefix; uvicorn puts it back as `root_path`, so
        # routes match without it and the docs link under it.
        root_path=settings.root_path,
    )


if __name__ == "__main__":
    main()
