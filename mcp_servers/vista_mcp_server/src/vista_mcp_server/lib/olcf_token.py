"""
S3M token introspection: which OLCF project a researcher's token belongs to.

S3M tokens are group-scoped: each carries exactly one `project` claim, and IRI
runs the job as that project's automation user. So the project is the only
Slurm account the token can charge, and VISTA reads it from the token rather
than asking for it. Any project is accepted -- the facility decides what a
token may do, and file operations go through the researcher's own Globus
identity, so there is nothing for VISTA to gate.
"""
import httpx
from cachetools import TTLCache
from fastmcp.exceptions import ToolError

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

