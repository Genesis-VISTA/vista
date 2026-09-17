"""Whose Globus credential authorizes a transfer.

Three sources, and the order between them is the whole point: a researcher on a
shared deployment uses their own identity, and a deployment that configures one
keeps working for everyone who has not connected. Odo's permissions model
assumes a single shared identity -- Globus-created directories are not
group-writable and Odo has no `setfacl` -- so which token is chosen is a
behaviour change, not a detail.
"""

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def no_deployment_token(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", None)
    monkeypatch.setattr(settings, "frontier_globus_refresh_token", None)


@pytest.fixture
def deployment_token(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "deployment-odo")
    monkeypatch.setattr(
        settings, "frontier_globus_refresh_token", "deployment-frontier"
    )


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_researchers_own_cluster_token_wins(cluster, deployment_token):
    cfg = UserConfig(
        odo_globus_token="mine-odo",
        frontier_globus_token="mine-frontier",
        globus_token="mine-shared",
    )

    assert cfg.require_globus_token(cluster) == f"mine-{cluster}"


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_shared_field_is_the_second_choice(cluster, deployment_token):
    """Kept because it is the field that already exists, and a researcher with
    one identity for both enclaves should not have to connect twice."""
    cfg = UserConfig(globus_token="mine-shared")

    assert cfg.require_globus_token(cluster) == "mine-shared"


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_deployment_is_the_last_resort(cluster, deployment_token):
    """A hosted deployment where nobody has connected behaves as it always has."""
    assert UserConfig().require_globus_token(cluster) == f"deployment-{cluster}"


def test_one_cluster_connected_does_not_authorize_the_other(
    deployment_token, monkeypatch
):
    """The two enclaves pin different SSO domains and can be different
    identities, so a token for one is not evidence about the other."""
    monkeypatch.setattr(settings, "frontier_globus_refresh_token", None)
    cfg = UserConfig(odo_globus_token="mine-odo")

    assert cfg.require_globus_token("odo") == "mine-odo"
    with pytest.raises(ToolError):
        cfg.require_globus_token("frontier")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_nothing_configured_names_where_to_connect(cluster, no_deployment_token):
    """A packaged researcher has no `.env` to edit and no shell that outlives
    the launch, so naming the environment variable would name nothing they can
    do."""
    with pytest.raises(ToolError) as refusal:
        UserConfig().require_globus_token(cluster)

    message = str(refusal.value)
    assert cluster.title() in message
    assert "settings" in message.lower()
    assert "VISTA_MCP_" not in message
