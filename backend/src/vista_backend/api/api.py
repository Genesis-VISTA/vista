import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import get_vista_mcp_server
from ..agents.campaign.wiring import build_default_monitor
from ..agents.inference import MissingInferenceCredential
from ..config import settings
from ..db.db import get_engine, init_db
from ..services.auth import get_user
from ..services.project_agent import project_agent_pool
from .agent import router as agent_router
from .campaign import router as campaign_router
from .chat_sessions import router as chat_sessions_router
from .files import router as files_router
from .knowledge_bases import router as knowledge_bases_router
from .mcp import router as mcp_router
from .models import router as models_router
from .palisade import router as palisade_router
from .projects import router as projects_router
from .skills import router as skills_router
from .users import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()

    # Check that the vista MCP server is up so we fail early if there's an issue.
    async with get_vista_mcp_server() as mcp_server:
        await mcp_server.list_tools()

    # Background campaign monitor (poll → notify → resume open HPC jobs). Opt-in; it
    # resumes any in-flight jobs left by a prior process on startup. See CampaignSettings.
    monitor_stop: asyncio.Event | None = None
    monitor_task: asyncio.Task | None = None
    if settings.campaigns.monitor_enabled:
        monitor_stop = asyncio.Event()
        monitor = build_default_monitor()
        monitor_task = asyncio.create_task(
            monitor.run_forever(
                session_factory=lambda: AsyncSession(get_engine()),
                interval=settings.campaigns.monitor_interval,
                stop_event=monitor_stop,
            )
        )
        logging.getLogger("vista.campaign_monitor").info("Campaign monitor started.")

    try:
        yield
    finally:
        if monitor_stop is not None:
            monitor_stop.set()
        if monitor_task is not None:
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task
        # `project_agent_pool.clear()` runs `_cleanup_project_agent` for every entry,
        # which cancels pending elicitations and drops their tracker entries.
        await project_agent_pool.clear()


app = FastAPI(
    title="vista-backend",
    version=settings.version,
    lifespan=lifespan,
    dependencies=[Depends(get_user)],  # Require login for all routes
)


@app.exception_handler(MissingInferenceCredential)
async def _missing_inference_credential(
    request: Request, exc: MissingInferenceCredential
) -> JSONResponse:
    """
    Report a missing inference credential as a named condition.

    409 rather than a 5xx: nothing has failed. On a fresh install this is the
    expected state, and the request cannot be satisfied until the researcher
    supplies a value the server has no way to obtain. `detail` carries the
    message naming the setting and where to enter it, which is the shape
    `ui/lib/user.ts:extractError` already surfaces.
    """
    return JSONResponse(status_code=409, content={"detail": exc.detail})


app.include_router(agent_router)
app.include_router(campaign_router)
app.include_router(chat_sessions_router)
app.include_router(mcp_router)
app.include_router(knowledge_bases_router)
app.include_router(models_router)
app.include_router(projects_router)
app.include_router(skills_router)
app.include_router(files_router)
app.include_router(users_router)

# PALISADE trust-state + re-auth endpoints: mounted only when the master
# flag is on, so they are absent from the API (and OpenAPI schema) when
# PALISADE is disabled.
if settings.palisade.enabled:
    app.include_router(palisade_router)


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
