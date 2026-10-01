"""Whose Globus credential authorizes a file operation.

Only the researcher's. Two sources, in order: their pair for this cluster, then
their pair shared by both clusters. There is no deployment-wide login to fall
back on, because a file operation must act as the researcher's own mapped POSIX
identity -- that is what lets the facility, not VISTA, decide what they may
read and write, and so what lets VISTA accept a token from any project.

Each source carries a *pair*: a Transfer token for listing directories and a
collection token for reading what is in them. The pair is taken whole, which is
what keeps one identity from listing a directory another identity then fails to
read.
"""

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.config import AppSettings
from vista_mcp_server.lib.types import GlobusTokens
from vista_mcp_server.lib.user_config import UserConfig

pytestmark = pytest.mark.unit

DEPLOYMENT_VARS = (
    "VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN",
    "VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN",
    "VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN",
    "VISTA_MCP_FRONTIER_GLOBUS_HTTPS_REFRESH_TOKEN",
)


@pytest.fixture
def deployment_vars(monkeypatch):
    """The variables that used to be a deployment-wide Globus login."""
    for var in DEPLOYMENT_VARS:
        monkeypatch.setenv(var, f"deployment-{var.lower()}")


def mine(cluster: str) -> GlobusTokens:
    return GlobusTokens(transfer=f"mine-{cluster}", https=f"mine-{cluster}-https")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_researchers_own_cluster_token_wins(cluster):
    cfg = UserConfig(
        odo_globus_token="mine-odo",
        odo_globus_https_token="mine-odo-https",
        frontier_globus_token="mine-frontier",
        frontier_globus_https_token="mine-frontier-https",
        globus_token="mine-shared",
        globus_https_token="mine-shared-https",
    )

    assert cfg.require_globus_token(cluster) == mine(cluster)


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_shared_field_is_the_second_choice(cluster):
    """Kept because it is the field that already exists, and a researcher with
    one identity for both enclaves should not have to connect twice."""
    cfg = UserConfig(globus_token="mine-shared", globus_https_token="mine-shared-https")

    assert cfg.require_globus_token(cluster) == mine("shared")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_a_deployment_login_is_not_a_source(cluster, deployment_vars):
    """The old deployment-wide variables are set, and the researcher has
    connected nothing: the operation is refused, as if they were not set."""
    with pytest.raises(ToolError, match="not connected"):
        UserConfig().require_globus_token(cluster)


def test_the_settings_no_longer_read_a_deployment_login(deployment_vars):
    fields = AppSettings.model_fields
    assert not [name for name in fields if "globus_refresh_token" in name]
    assert not [name for name in fields if "globus_https_refresh_token" in name]


def test_one_cluster_connected_does_not_authorize_the_other():
    """The two enclaves pin different SSO domains and can be different
    identities, so a token for one is not evidence about the other."""
    cfg = UserConfig(
        odo_globus_token="mine-odo", odo_globus_https_token="mine-odo-https"
    )

    assert cfg.require_globus_token("odo") == mine("odo")
    with pytest.raises(ToolError):
        cfg.require_globus_token("frontier")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_half_a_credential_is_not_one(cluster):
    """A connection made before VISTA moved to the HTTPS interface has a
    Transfer token and nothing to read files with. Accepting it would list an
    output directory and then fail on every file in it -- "no outputs yet" all
    over again, one layer down."""
    cfg = UserConfig(
        odo_globus_token="stale-odo", frontier_globus_token="stale-frontier"
    )

    with pytest.raises(ToolError) as refusal:
        cfg.require_globus_token(cluster)
    assert "connect again" in str(refusal.value).lower()


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_a_half_credential_does_not_shadow_a_whole_one(cluster):
    """A stale cluster pair is skipped, and the researcher's complete shared
    pair is used instead."""
    cfg = UserConfig(
        odo_globus_token="stale-odo",
        frontier_globus_token="stale-frontier",
        globus_token="mine-shared",
        globus_https_token="mine-shared-https",
    )

    assert cfg.require_globus_token(cluster) == mine("shared")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_nothing_configured_names_where_to_connect(cluster):
    """A packaged researcher has no `.env` to edit and no shell that outlives
    the launch, so naming the environment variable would name nothing they can
    do."""
    with pytest.raises(ToolError) as refusal:
        UserConfig().require_globus_token(cluster)

    message = str(refusal.value)
    assert cluster.title() in message
    assert "settings" in message.lower()
