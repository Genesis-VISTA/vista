"""
The secret the VISTA MCP server shares with the backend.

The server listens on loopback, but anything else on the machine (another user,
a sandboxed command) can reach loopback too, and the server acts on credentials
it is handed. So the HTTP app refuses a request that does not carry the secret
in `X-Vista-MCP-Secret`. With no secret configured it accepts every request,
as a server started by hand always has, and warns once.
"""

import hmac
import logging

from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings

log = logging.getLogger(__name__)

HEADER = "x-vista-mcp-secret"


class SharedSecretMiddleware:
    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self._secret = secret.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        sent = dict(scope["headers"]).get(HEADER.encode("ascii"), b"")
        if hmac.compare_digest(sent, self._secret):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})


def http_middleware() -> list[Middleware]:
    """The middleware for the HTTP app: empty (with a warning) when no secret is set."""
    secret = settings.secret.get_secret_value() if settings.secret else ""
    if not secret:
        log.warning(
            "VISTA_MCP_SECRET is not set: the MCP server accepts requests from any "
            "local process. The launchers set it; set it when starting by hand "
            "beside a backend that has it."
        )
        return []
    return [Middleware(SharedSecretMiddleware, secret=secret)]
