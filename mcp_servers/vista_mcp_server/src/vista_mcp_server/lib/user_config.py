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
