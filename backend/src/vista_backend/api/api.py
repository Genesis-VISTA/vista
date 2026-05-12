import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from mcp import types as mcp_types

from ..config import settings
from ..db.db import init_db
from ..agents.agents import get_mcp_server
from .ui_chat import SseLogHandler, make_elicitation_callback, router as ui_chat_router
from .agent import router as agent_router
from .mcp import router as mcp_router
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

    await init_db()

    # Just check that the MCP server is up so we fail early if there's an issue
    async with get_mcp_server() as mcp_server:
        await mcp_server.list_tools()

    app.state.elicitations = {}
    app.state.elicitation_callback = make_elicitation_callback(app)

    try:
        yield
    finally:
        for entry in list(app.state.elicitations.values()):
            entry.timer.cancel()
            if not entry.future.done():
                entry.future.set_result(mcp_types.ElicitResult(action="cancel"))
        app.state.elicitations.clear()


app = FastAPI(title="vista-backend", lifespan=lifespan)
app.include_router(ui_chat_router)
app.include_router(agent_router)
app.include_router(mcp_router)
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
