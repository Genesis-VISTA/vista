"""
Per-user HPC configuration plumbed in via MCP request metadata.

TODO: Temporary workaround, we are passing the user config in via MCP metadata.
"""
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field


class UserConfig(BaseModel):
    s3m_token: str | None = None
    nersc_iri_token: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    remote_hpc_jobs_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    globus_token: str | None = None

    def require_s3m_token(self) -> str:
        if not self.s3m_token:
            raise ToolError(
                "No S3M token configured for this user. Add an S3M token in the Vista "
                "user settings page before submitting jobs to Odo."
            )
        return self.s3m_token

    def require_nersc_iri_token(self) -> str:
        if not self.nersc_iri_token:
            raise ToolError(
                "No NERSC IRI token configured for this user. Add one in the Vista "
                "user settings page before submitting jobs to Perlmutter."
            )
        return self.nersc_iri_token

    def require_remote_hpc_jobs_dir(self) -> str:
        if not self.remote_hpc_jobs_dir:
            raise ToolError(
                "No remote HPC jobs dir configured for this user. Set it in the Vista "
                "user settings page."
            )
        return self.remote_hpc_jobs_dir

    def require_frontier_account(self) -> str:
        if not self.frontier_account:
            raise ToolError(
                "No Frontier account configured for this user. Set 'Frontier account' "
                "in the Vista user settings page before submitting jobs to Frontier "
                "(your OLCF project name, e.g. 'chm243'; must match your S3M token's "
                "project claim)."
            )
        return self.frontier_account

    def require_frontier_remote_dir(self) -> str:
        if not self.frontier_remote_dir:
            raise ToolError(
                "No Frontier remote dir configured for this user. Set 'Frontier remote "
                "directory' in the Vista user settings page before submitting jobs to "
                "Frontier (typically /lustre/orion/<project>/proj-shared/vista)."
            )
        return self.frontier_remote_dir

    def require_globus_token(self) -> str:
        """
        Returns the user's Globus refresh token, used for Frontier file ops on
        the OLCF DTN collection. The MCP server mints short-lived access tokens
        from it on each submission via `globus_sdk.RefreshTokenAuthorizer`.
        """
        if not self.globus_token:
            raise ToolError(
                "No Globus token configured for this user. Mint with: "
                "python OLCF-Globus-Transfer/get_olcf_token.py --force-login "
                "--session-domain sso.ccs.ornl.gov, then paste the refresh_token "
                "value from ~/.globus/olcf_tokens.json into the Vista user "
                "settings page before submitting jobs to Frontier."
            )
        return self.globus_token


class ProjectPaths(BaseModel):
    """
    Per-`ProjectAgent` filesystem layout, plumbed in from the backend via MCP metadata.
    Lets the MCP server resolve sandbox-side mount points (`/mnt/skills`, `/mnt/data/output`,
    `/mnt/data/uploads`) back to the calling agent's host volume.
    """
    skills_dir: str | None = None
    output_dir: str | None = None
    uploads_dir: str | None = None

    def require_output_dir(self) -> str:
        if not self.output_dir:
            raise ToolError(
                "No per-agent output_dir provided by the backend. This tool must be called "
                "via the Vista backend so it can supply the project's host output path."
            )
        return self.output_dir


class VistaMeta(BaseModel):
    """ The `vista` metadata object sent by the backend on every MCP tool call. """
    user: UserConfig = Field(default_factory=UserConfig)
    project_paths: ProjectPaths = Field(default_factory=ProjectPaths)


def get_vista_meta(ctx: Context) -> VistaMeta:
    """ Read the `vista` metadata blob from the current MCP request. """
    rc = ctx.request_context
    meta = rc.meta if rc is not None else None
    raw: Any = getattr(meta, "vista", None) if meta is not None else None
    if not isinstance(raw, dict):
        return VistaMeta()
    return VistaMeta.model_validate(raw)
