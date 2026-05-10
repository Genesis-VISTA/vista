import contextlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from mcp import types as mcp_types

from ..agents.agents import get_mcp_server
from ..config import settings
from ..db.db import get_engine
from .chat import SseLogHandler, make_elicitation_callback, router as chat_router
from .projects import router as projects_router
from .skills import router as skills_router
from .uploads import router as uploads_router

logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    handler = SseLogHandler()
    handler.setLevel(settings.log_level)
    logging.getLogger("vista").addHandler(handler)
    logging.getLogger("vista").setLevel(settings.log_level)

    # initialize get_engine() so tables and connection is created on start
    get_engine()

    app.state.elicitations = {}
    app.state.elicitation_callback = make_elicitation_callback(app)

    # Hold a long-lived MCP connection so we fail fast if it's unreachable.
    # Per-request agents create their own server instance with the
    # request-scoped elicitation/tool callbacks.
    mcp_server = get_mcp_server()
    startup_log = logging.getLogger("vista.startup")
    mcp_entered = False
    try:
        await mcp_server.__aenter__()
        mcp_entered = True
        try:
            await mcp_server.list_tools()
        except Exception as exc:  # pragma: no cover
            startup_log.warning("MCP list_tools failed at startup: %s", exc)
    except Exception as exc:
        startup_log.warning(
            "Could not connect to MCP server at %s: %s. Will retry on demand.",
            settings.mcp_url,
            exc,
        )

    try:
        yield
    finally:
        for entry in list(app.state.elicitations.values()):
            entry.timer.cancel()
            if not entry.future.done():
                entry.future.set_result(mcp_types.ElicitResult(action="cancel"))
        app.state.elicitations.clear()
        if mcp_entered:
            with contextlib.suppress(Exception):
                await mcp_server.__aexit__(None, None, None)


app = FastAPI(title="vista-backend", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(projects_router)
app.include_router(skills_router)
app.include_router(uploads_router)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "vista_backend.api.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
