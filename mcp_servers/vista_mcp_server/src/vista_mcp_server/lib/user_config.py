"""
Per-user HPC configuration plumbed in via MCP request metadata.

TODO: Temporary workaround, we are passing the user config in via MCP metadata.
"""
from typing import Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .types import GlobusTokens


class UserConfig(BaseModel):
    odo_s3m_token: str | None = None
    frontier_s3m_token: str | None = None
    nersc_iri_token: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    odo_globus_token: str | None = None
    frontier_globus_token: str | None = None
    globus_token: str | None = None
    odo_globus_https_token: str | None = None
    frontier_globus_https_token: str | None = None
    globus_https_token: str | None = None

    def require_s3m_token(self, cluster: Literal["odo", "frontier"]) -> str:
        token = self.odo_s3m_token if cluster == "odo" else self.frontier_s3m_token
        if not token:
            raise ToolError(
                f"No S3M token configured for {cluster!r}. Add a {cluster} S3M token in the "
                "Vista user settings page before submitting jobs."
            )
        return token

    def require_globus_token(
        self, cluster: Literal["odo", "frontier"]
    ) -> GlobusTokens:
        """The researcher's Globus credential for this cluster's file operations.

        Two sources, in order: the researcher's own tokens for this cluster,
        then their pair shared by both clusters. There is no deployment-wide
        credential: every file operation acts as the researcher's own mapped
        POSIX identity, so the facility decides what they may read and write.

        A source counts only when it has *both* tokens, and the pair is taken
        whole. Half a credential is the case that matters here: a connection
        made before VISTA moved to the HTTPS interface has a Transfer token and
        no collection token, and using it would list a directory and then fail
        to read anything in it. Skipping such a source lets the other one take
        over; when neither has both, the refusal below says to connect again.
        """
        for transfer, https in (
            (
                self.odo_globus_token if cluster == "odo" else self.frontier_globus_token,
                self.odo_globus_https_token
                if cluster == "odo"
                else self.frontier_globus_https_token,
            ),
            (self.globus_token, self.globus_https_token),
        ):
            if transfer and https:
                return GlobusTokens(transfer=transfer, https=https)

        raise ToolError(
            f"Globus file transfer is not connected for {cluster.title()}. "
            "Connect it in the VISTA user settings, which asks Globus for "
            "permission to read and write this cluster's files. If you "
            "connected before, connect again -- VISTA now needs one more "
            "permission than it did, so the older connection is incomplete."
        )

    def require_nersc_iri_token(self) -> str:
        if not self.nersc_iri_token:
            raise ToolError(
                "No NERSC IRI token configured for this user. Add one in the Vista "
                "user settings page before submitting jobs to Perlmutter."
            )
        return self.nersc_iri_token


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
    uri_map: dict[str, str] = Field(default_factory=dict)
    """
    Maps sandbox `file://` prefixes (e.g. `file:///mnt/data/output/`) to download URL templates
    """


def get_vista_meta(ctx: Context) -> VistaMeta:
    """ Read the `vista` metadata blob from the current MCP request. """
    rc = ctx.request_context
    meta = rc.meta if rc is not None else None
    raw: Any = getattr(meta, "vista", None) if meta is not None else None
    if not isinstance(raw, dict):
        return VistaMeta()
    return VistaMeta.model_validate(raw)
