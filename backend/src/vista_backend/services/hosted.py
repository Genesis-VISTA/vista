"""
What a hosted deployment turns off (openspec change `multi-user-hosted`, spec
`hosted-mode`).

One backend serves two deployments: `desktop` (a researcher's own computer) and
`hosted` (a shared service behind a gateway). A control the hosted deployment
lacks is refused here, in the backend, first; the UI then hides it
(`ui/lib/deployment.ts`), so a hidden control called directly still fails.
"""

from fastapi import HTTPException

from ..config import settings


def is_hosted() -> bool:
    return settings.deployment == "hosted"


def refuse_when_hosted(what: str) -> None:
    """Raise `403` naming `what` when this is a hosted deployment; otherwise do nothing."""
    if is_hosted():
        raise HTTPException(403, f"{what} is not available on a hosted VISTA.")
