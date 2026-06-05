"""
Per-user HPC configuration plumbed in via MCP request metadata.

TODO: Temporary workaround, we are passing the user config in via MCP metadata.
"""
from __future__ import annotations
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import BaseModel


class UserConfig(BaseModel):
    s3m_token: str | None = None
    nersc_iri_token: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    remote_hpc_jobs_dir: str | None = None
    frontier_remote_dir: str | None = None
    globus_token: str | None = None


def get_user_config(ctx: Context) -> UserConfig:
    """
    Read the user config blob from the current MCP request's metadata.
    """
    rc = ctx.request_context
    meta = rc.meta if rc is not None else None
    raw: Any = getattr(meta, "vista_user_config", None) if meta is not None else None
    if not isinstance(raw, dict):
        return UserConfig()
    return UserConfig.model_validate(raw)


def require_s3m_token(cfg: UserConfig) -> str:
    if not cfg.s3m_token:
        raise ToolError(
            "No S3M token configured for this user. Add an S3M token in the Vista "
            "user settings page before submitting jobs to Odo."
        )
    return cfg.s3m_token


def require_nersc_iri_token(cfg: UserConfig) -> str:
    if not cfg.nersc_iri_token:
        raise ToolError(
            "No NERSC IRI token configured for this user. Add one in the Vista "
            "user settings page before submitting jobs to Perlmutter."
        )
    return cfg.nersc_iri_token


def require_remote_hpc_jobs_dir(cfg: UserConfig) -> str:
    if not cfg.remote_hpc_jobs_dir:
        raise ToolError(
            "No remote HPC jobs dir configured for this user. Set it in the Vista "
            "user settings page."
        )
    return cfg.remote_hpc_jobs_dir


def require_frontier_remote_dir(cfg: UserConfig) -> str:
    if not cfg.frontier_remote_dir:
        raise ToolError(
            "No Frontier remote dir configured for this user. Set 'Frontier remote "
            "directory' in the Vista user settings page before submitting jobs to "
            "Frontier (typically /lustre/orion/<project>/proj-shared/vista)."
        )
    return cfg.frontier_remote_dir


def require_globus_token(cfg: UserConfig) -> str:
    """
    Returns the user's Globus refresh token, used for Frontier file ops on
    the OLCF DTN collection. The MCP server mints short-lived access tokens
    from it on each submission via `globus_sdk.RefreshTokenAuthorizer`.
    """
    if not cfg.globus_token:
        raise ToolError(
            "No Globus token configured for this user. Mint with: "
            "python OLCF-Globus-Transfer/get_olcf_token.py --force-login "
            "--session-domain sso.ccs.ornl.gov, then paste the refresh_token "
            "value from ~/.globus/olcf_tokens.json into the Vista user "
            "settings page before submitting jobs to Frontier."
        )
    return cfg.globus_token
