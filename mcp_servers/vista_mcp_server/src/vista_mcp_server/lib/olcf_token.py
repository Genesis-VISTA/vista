"""
S3M token introspection — the per-user authorization gate for OLCF file ops.

Globus transfers run under a single Vista-held identity (the backend's
deployment-wide refresh token), so possession of an S3M token in the cluster's
OLCF project is what authorizes a user to move files through Vista. S3M tokens
are group-scoped: each token carries exactly one `project` claim, so one token
enables exactly one of Odo / Frontier.

Ported from the introspect check in the deleted `lib/s3m.py`
(`S3mClient._validate_token`, removed with the SSH/SCP path).
"""
import httpx
from cachetools import TTLCache
from fastmcp.exceptions import ToolError
from typing import Any

_introspect_cache = TTLCache[tuple[str, str], str](maxsize=256, ttl=600.0)
""" (introspect_url, token) -> project. """


async def get_s3m_token_project(s3m_token: str, *, introspect_url: str) -> str:
    """
    Return the OLCF `project` claim of an S3M token, verifying it against the
    S3M introspection endpoint. Results are cached for a bit so we don't re-introspect.
    """
    key = (introspect_url, s3m_token)
    cached = _introspect_cache.get(key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                introspect_url,
                headers={"Authorization": f"Bearer {s3m_token}"},
                timeout=60,
            )

            resp.raise_for_status()
            token_info = resp.json()
    except httpx.HTTPStatusError as e:
        raise ToolError(
            f"S3M token introspection failed ({e.response.status_code}). The token "
            "may be expired or invalid — mint a new one and update it in the Vista "
            "user settings page."
        )
    except httpx.HTTPError as e:
        raise ToolError(f"Could not reach the S3M introspection endpoint {introspect_url}: {e}")

    project = token_info.get("token", {}).get("project")
    if not project:
        raise ToolError(
            "S3M token introspection returned no project claim; cannot verify "
            "cluster access for this token."
        )
    _introspect_cache[key] = project
    return project


async def require_s3m_project(
    s3m_token: str, expected_project: str, *, cluster: str, introspect_url: str,
) -> None:
    """
    Raise ToolError unless the S3M token belongs to `expected_project`.

    Called before every OLCF file op: Globus runs as Vista's own identity
    against project-shared directories, so project membership on the S3M token
    is the only thing standing between a user and another project's files.
    """
    project = await get_s3m_token_project(s3m_token, introspect_url=introspect_url)
    if project != expected_project:
        raise ToolError(
            f"Your S3M token belongs to project {project!r}, but {cluster} access "
            f"requires {expected_project!r}. Mint an S3M token for "
            f"{expected_project!r} and update it in the Vista user settings page."
        )
