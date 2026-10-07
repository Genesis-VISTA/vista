"""
HTTP-level helpers for agent API tests.

The FastAPI app is a module singleton whose lifespan pings the real
`vista_mcp_server`; `httpx.ASGITransport` does not run lifespan, so the app can
be exercised in-process without it. Requests are routed to the test's
in-memory DB session, and the agent pool is swapped for one that hands back a
pre-built agent instead of constructing one against the production engine.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.agents.agents import ProjectAgent
from vista_backend.api import agent as agent_api
from vista_backend.api.api import app
from vista_backend.db.db import _get_session
from vista_backend.services import chat_run


@dataclass
class FakeAgentPool:
    """Stands in for `project_agent_pool`, yielding a single prepared agent."""

    agent: Any
    keys: list = field(default_factory=list)

    @asynccontextmanager
    async def get(self, key) -> AsyncIterator[Any]:
        self.keys.append(key)
        yield self.agent


@contextmanager
def api_client(
    session: AsyncSession, *, agent: ProjectAgent | None = None
) -> Iterator[tuple[AsyncClient, FakeAgentPool]]:
    """
    Yield an `(AsyncClient, FakeAgentPool)` pair bound to `session`.

    Authentication runs for real: pass `headers={"X-Vista-User-Email": ...}` to
    act as a seeded user, which is what makes the 401/403/404 assertions
    meaningful.
    """
    pool = FakeAgentPool(agent=agent)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[_get_session] = override_session
    original_pool = agent_api.project_agent_pool
    agent_api.project_agent_pool = pool  # type: ignore[assignment]
    # Chat turns run in the background registry, which has its own pool and opens
    # its own sessions: point both at the test's pool and database.
    original_run_pool = chat_run.project_agent_pool
    original_factory = chat_run.session_factory
    chat_run.project_agent_pool = pool  # type: ignore[assignment]
    chat_run.session_factory = lambda: AsyncSession(session.bind)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        yield client, pool
    finally:
        agent_api.project_agent_pool = original_pool  # type: ignore[assignment]
        chat_run.project_agent_pool = original_run_pool
        chat_run.session_factory = original_factory
        app.dependency_overrides.clear()


def parse_sse(body: str) -> list[tuple[str, str]]:
    """Parse an SSE body into `(event, data)` pairs."""
    events: list[tuple[str, str]] = []
    name: str | None = None
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            events.append((name or "message", line.removeprefix("data:").strip()))
            name = None
    return events
