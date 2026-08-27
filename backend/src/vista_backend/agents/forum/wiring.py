"""
Live wiring for the debate forum: the seam between the API and the real world.

Everything the orchestrator needs is injected, so this module is the only place
that knows about MCP, the skills service and the h5i binary. It is also the only
place that decides what a debate is allowed to look at, which keeps that decision
readable in one file rather than spread across the tools.
"""

from __future__ import annotations

import logging
import uuid

from sqlmodel.ext.asyncio.session import AsyncSession

from ...config import settings
from ...db.db import get_engine
from ...services import debate as debate_service
from ...services import skills as skills_service
from ...services.h5i_forum import ForumClient
from .debate import DebateOrchestrator
from .grounding import Grounding, WebReader
from .roles import RoleAgents


logger = logging.getLogger(__name__)


def build_client() -> ForumClient:
    return ForumClient(settings.forum)


async def _read_skill_body(name: str) -> str:
    """
    A domain skill's text, read through the skills service.

    Opens its own session: tools run inside an agent turn, which may outlive the
    request session that started the debate.
    """
    async with AsyncSession(get_engine()) as session:
        try:
            _, body = await skills_service.get_skill_detail(session, name)
        except Exception:  # noqa: BLE001 — a missing skill is an answer, not a crash
            return f"No skill named {name!r} is installed."
        return body


def build_grounding(client: ForumClient) -> Grounding:
    """
    What a debate may look at.

    The knowledge-base search is left unwired here: `rag_search` needs a live MCP
    connection and per-user metadata, which the API supplies per run. Web reads
    are constructed but refuse themselves unless the configured tier actually
    enforces the egress allowlist — see `WebReader`.
    """
    return Grounding(
        skills=_read_skill_body,
        forum=client,
        browser=WebReader(client),
    )


def build_orchestrator(*, on_post=None) -> DebateOrchestrator:
    client = build_client()
    grounding = build_grounding(client)
    return DebateOrchestrator(
        client=client,
        roles=RoleAgents(toolsets=_toolsets(grounding)),
        on_post=on_post,
    )


def _toolsets(grounding: Grounding):
    from .grounding import build_toolsets

    return build_toolsets(grounding)


async def run_debate_task(run_id: uuid.UUID) -> None:
    """
    Run one debate to completion in the background.

    Owns its own session: the request that started the debate returns as soon as
    the thread exists, and the argument then takes minutes. A failure is recorded
    on the run rather than raised into a task nobody is awaiting — otherwise a
    debate that died would sit at `debating` forever with no explanation.
    """
    async with AsyncSession(get_engine()) as session:
        try:
            run = await debate_service.require_debate(session, run_id)
            await build_orchestrator().run(session, run)
            await session.commit()
        except Exception:
            logger.exception("debate %s failed", run_id)
            await session.rollback()
            try:
                await debate_service.set_status(session, run_id=run_id, status="failed")
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("debate %s: could not record the failure", run_id)
