import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import get_vista_mcp_server
from ..agents.campaign.wiring import build_default_monitor
from ..agents.inference import (
    MissingInferenceCredential,
    MissingInferenceModel,
    RejectedInferenceCredential,
)
from ..config import settings
from ..db.db import DatabaseIncompatible, get_engine, init_db
from ..services.chat_run import chat_run_registry, sweep_interrupted_runs
from ..services.collaboration import (
    CollaborationError,
    CollaborationRefused,
    CollaborationUnavailable,
    SharingConflict,
)
from ..services.project_agent import project_agent_pool
from .agent import router as agent_router
from .campaign import router as campaign_router
from .chat_runs import router as chat_runs_router
from .collaboration import router as collaboration_router
from .debate import router as debate_router
from .chat_sessions import router as chat_sessions_router
from .files import router as files_router
from .knowledge_bases import router as knowledge_bases_router
from .mcp import router as mcp_router
from .models import router as models_router
from .palisade import router as palisade_router
from .projects import router as projects_router
from .reports import router as reports_router
from .settings import router as settings_router
from .skills import router as skills_router


def _report_database_incompatible(refusal: DatabaseIncompatible) -> None:
    """
    Say why the database was refused, for the log and for the launcher.

    The launcher sets `VISTA_STARTUP_FAILURE_FILE` and reads the code back when
    this process exits before it is healthy; run on its own, nothing is written.
    The caller re-raises, which makes uvicorn exit non-zero.
    """
    logging.getLogger(__name__).error(
        "Refusing to start: %s (%s)", refusal.message, refusal.code
    )
    failure_file = os.environ.get("VISTA_STARTUP_FAILURE_FILE")
    if failure_file:
        try:
            with open(failure_file, "w", encoding="utf-8") as f:
                f.write(refusal.code + "\n")
        except OSError:
            logging.getLogger(__name__).exception(
                "Could not write the startup failure file %s", failure_file
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        await init_db()
    except DatabaseIncompatible as refusal:
        _report_database_incompatible(refusal)
        raise
    # No run survives a restart: a row still `running` died with the last process.
    if await sweep_interrupted_runs():
        logging.getLogger(__name__).info(
            "Marked chat runs from the last session as interrupted."
        )

    # The Hypothesis Lab's repository is a property of each project now, so
    # there is nothing deployment-wide to reconcile at boot — a project's forum
    # is initialised when its URL is saved, and repaired on first use. This only
    # disowns the settings that used to select one, so a stale `.env` line
    # cannot quietly keep working on the one machine that still has it.
    from ..agents.forum.project_forum import check_legacy_forum_env

    check_legacy_forum_env()

    # Likewise the deployment-wide repository tokens, which moved to Settings.
    from ..services.repo_tokens import check_legacy_token_env

    check_legacy_token_env()

    # Nothing a publish or import was working on survives a restart.
    from ..services.knowledge_base_collaboration import clear_incoming

    await asyncio.to_thread(clear_incoming)

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
        # End runs while their agents still exist, each saving its partial history.
        # Bounded so it fits inside the launcher's 10 s grace on quit.
        await chat_run_registry.stop_all(reason="interrupted", timeout=5)
        # `project_agent_pool.clear()` runs `_cleanup_project_agent` for every entry,
        # which cancels pending elicitations and drops their tracker entries.
        await project_agent_pool.clear()


app = FastAPI(
    title="vista-backend",
    version=settings.version,
    lifespan=lifespan,
)


@app.exception_handler(MissingInferenceCredential)
@app.exception_handler(MissingInferenceModel)
@app.exception_handler(RejectedInferenceCredential)
async def _missing_inference_credential(
    request: Request,
    exc: MissingInferenceCredential
    | MissingInferenceModel
    | RejectedInferenceCredential,
) -> JSONResponse:
    """
    Report a missing or rejected inference credential, or a provider with no
    model chosen and no default, as a named condition.

    409 rather than a 5xx: nothing has failed. On a fresh install this is the
    expected state, and the request cannot be satisfied until the researcher
    supplies a value the server has no way to obtain. `detail` carries the
    message naming the setting and where to enter it, which is the shape
    `ui/lib/user.ts:extractError` already surfaces. `code` tells it apart from
    the chat endpoint's other 409, a conversation that is already running a
    turn (`run_busy`).
    """
    return JSONResponse(
        status_code=409,
        content={"detail": exc.detail, "code": "inference_not_configured"},
    )


@app.exception_handler(CollaborationError)
async def _collaboration_error(
    request: Request, exc: CollaborationError
) -> JSONResponse:
    """
    A collaboration server that cannot be reached is 503, one that refused or
    answered nonsense 502; `detail` is the sentence to show.
    """
    if isinstance(exc, CollaborationUnavailable):
        status, code = 503, "collaboration_unavailable"
    elif isinstance(exc, CollaborationRefused):
        status, code = 502, "collaboration_refused"
    else:
        status, code = 502, "collaboration_error"
    return JSONResponse(status_code=status, content={"detail": str(exc), "code": code})


@app.exception_handler(SharingConflict)
async def _sharing_conflict(request: Request, exc: SharingConflict) -> JSONResponse:
    """A publish or import that needs the researcher's choice: 409 with `code`."""
    return JSONResponse(
        status_code=409,
        content={"detail": exc.detail, "code": exc.code, **exc.extra},
    )


app.include_router(agent_router)
app.include_router(campaign_router)
app.include_router(debate_router)
app.include_router(chat_sessions_router)
app.include_router(chat_runs_router)
app.include_router(collaboration_router)
app.include_router(mcp_router)
app.include_router(knowledge_bases_router)
app.include_router(models_router)
app.include_router(projects_router)
app.include_router(reports_router)
app.include_router(skills_router)
app.include_router(files_router)
app.include_router(settings_router)

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
