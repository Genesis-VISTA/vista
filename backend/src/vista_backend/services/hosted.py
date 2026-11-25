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


ALL_CLUSTERS = ("odo", "frontier", "lux", "perlmutter")


def allowed_clusters() -> list[str]:
    """The clusters this deployment supports: the setting, else Odo alone when hosted, else all."""
    if settings.allowed_clusters is not None:
        return [c for c in ALL_CLUSTERS if c in settings.allowed_clusters]
    return ["odo"] if is_hosted() else list(ALL_CLUSTERS)


def restricted_clusters() -> list[str] | None:
    """`allowed_clusters()` when it limits anything (hosted, or a configured list), else None.

    What the MCP server is told: its presence also turns off the shared Globus pair.
    """
    if is_hosted() or settings.allowed_clusters is not None:
        return allowed_clusters()
    return None


def refuse_cluster(cluster: str) -> None:
    """Raise `403` when `cluster` is not one this deployment supports."""
    allowed = allowed_clusters()
    if cluster not in allowed:
        only = ", ".join(c.title() for c in allowed) or "no cluster"
        raise HTTPException(
            403, f"{cluster.title()} is not available here: only {only} available."
        )
