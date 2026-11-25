"""
Whether the hosted server's own model endpoint answers (openspec change
`multi-user-hosted`, design D10).

A probe of `{endpoint}/models`, cached for `CACHE_SECONDS` so the picker and
every send share one answer, with a short timeout so a dead endpoint never holds
a request for long. Other providers (MAG, OLCF with the user's key) are never
affected by it.
"""

import asyncio
import time
from dataclasses import dataclass

from typing import Any

import httpx

from ..agents.inference import (
    InferenceTarget,
    ServerModelUnavailable,
    require_inference_credential,
)
from ..config import settings

__all__ = [
    "ServerModelUnavailable",
    "require_model_reachable",
    "require_server_model",
    "server_model_status",
]

CACHE_SECONDS = 45.0
TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class ServerModelStatus:
    available: bool
    reason: str | None = None


_cached: tuple[float, ServerModelStatus] | None = None
_lock = asyncio.Lock()


def reset_for_tests() -> None:
    global _cached
    _cached = None


async def _probe() -> ServerModelStatus:
    url = settings.server_inference_url.rstrip("/") + "/models"
    key = settings.server_inference_key
    headers = {"Authorization": f"Bearer {key.get_secret_value()}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return ServerModelStatus(
            False,
            "The server's model is not answering right now "
            f"({type(exc).__name__}). Choose another provider in Settings, or try again shortly.",
        )
    if response.status_code >= 400:
        return ServerModelStatus(
            False,
            f"The server's model answered {response.status_code}. "
            "Choose another provider in Settings, or try again shortly.",
        )
    return ServerModelStatus(True)


async def server_model_status() -> ServerModelStatus:
    """The probe's answer, reused for `CACHE_SECONDS`."""
    global _cached
    now = time.monotonic()
    if _cached and now - _cached[0] < CACHE_SECONDS:
        return _cached[1]
    async with _lock:
        if _cached and time.monotonic() - _cached[0] < CACHE_SECONDS:
            return _cached[1]
        status = await _probe()
        _cached = (time.monotonic(), status)
        return status


async def require_model_reachable(local: Any, model: str | None) -> InferenceTarget:
    """
    `require_inference_credential`, then `require_server_model` for its
    provider: what every place that starts a turn checks first (the app's send,
    a debate, Slack).
    """
    target = require_inference_credential(local, model)
    await require_server_model(target.provider)
    return target


async def require_server_model(provider: str) -> None:
    """Refuse a conversation on the `server` provider while its endpoint is down."""
    if provider != "server":
        return
    status = await server_model_status()
    if not status.available:
        raise ServerModelUnavailable(
            status.reason or "The server's model is unavailable."
        )
