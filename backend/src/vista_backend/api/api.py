from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
import uvicorn

from ..config import settings
from ..db.db import init_db
from ..agents.agents import get_vista_mcp_server
from .agent import router as agent_router
from .vistaguard import router as vistaguard_router
from ..services.project_agent import project_agent_pool
from ..services.auth import get_user
from .mcp import router as mcp_router
from .knowledge_bases import router as knowledge_bases_router
from .projects import router as projects_router
from .skills import router as skills_router
from .uploads import router as uploads_router
from .users import router as users_router

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()

    # Check that the vista MCP server is up so we fail early if there's an issue.
    async with get_vista_mcp_server() as mcp_server:
        await mcp_server.list_tools()

    try:
        yield
    finally:
        # `project_agent_pool.clear()` runs `_cleanup_project_agent` for every entry,
        # which cancels pending elicitations and drops their tracker entries.
        await project_agent_pool.clear()


app = FastAPI(
    title="vista-backend",
    lifespan=lifespan,
    dependencies=[Depends(get_user)], # Require login for all routes
)
app.include_router(agent_router)
app.include_router(mcp_router)
app.include_router(knowledge_bases_router)
app.include_router(projects_router)
app.include_router(skills_router)
app.include_router(uploads_router)
app.include_router(users_router)

# VISTAGuard trust-state + re-auth endpoints: mounted only when the master
# flag is on, so they are absent from the API (and OpenAPI schema) when
# VISTAGuard is disabled.
if settings.vistaguard.enabled:
    app.include_router(vistaguard_router)


def main() -> None:
    uvicorn.run(
        "vista_backend.api.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
