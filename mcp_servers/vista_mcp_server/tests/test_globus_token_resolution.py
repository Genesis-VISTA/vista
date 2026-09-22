"""Whose Globus credential authorizes a file operation.

Three sources, and the order between them is the whole point: a researcher on a
shared deployment uses their own identity, and a deployment that configures one
keeps working for everyone who has not connected. Odo's permissions model
assumes a single shared identity -- Globus-created directories are not
group-writable and Odo has no `setfacl` -- so which token is chosen is a
behaviour change, not a detail.

Each source now carries a *pair*: a Transfer token for listing directories and a
collection token for reading what is in them. The pair is taken whole, which is
what keeps one identity from listing a directory another identity then fails to
read.
"""

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.config import settings
from vista_mcp_server.lib.types import GlobusTokens
from vista_mcp_server.lib.user_config import UserConfig

pytestmark = pytest.mark.unit


@pytest.fixture
def no_deployment_token(monkeypatch):
    for field in (
        "odo_globus_refresh_token",
        "frontier_globus_refresh_token",
        "odo_globus_https_refresh_token",
        "frontier_globus_https_refresh_token",
    ):
        monkeypatch.setattr(settings, field, None)


@pytest.fixture
def deployment_token(monkeypatch):
    monkeypatch.setattr(settings, "odo_globus_refresh_token", "deployment-odo")
    monkeypatch.setattr(
        settings, "frontier_globus_refresh_token", "deployment-frontier"
    )
    monkeypatch.setattr(
        settings, "odo_globus_https_refresh_token", "deployment-odo-https"
    )
    monkeypatch.setattr(
        settings, "frontier_globus_https_refresh_token", "deployment-frontier-https"
    )


def mine(cluster: str) -> GlobusTokens:
    return GlobusTokens(transfer=f"mine-{cluster}", https=f"mine-{cluster}-https")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_researchers_own_cluster_token_wins(cluster, deployment_token):
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
def test_the_shared_field_is_the_second_choice(cluster, deployment_token):
    """Kept because it is the field that already exists, and a researcher with
    one identity for both enclaves should not have to connect twice."""
    cfg = UserConfig(globus_token="mine-shared", globus_https_token="mine-shared-https")

    assert cfg.require_globus_token(cluster) == mine("shared")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_the_deployment_is_the_last_resort(cluster, deployment_token):
    """A hosted deployment where nobody has connected behaves as it always has."""
    assert UserConfig().require_globus_token(cluster) == GlobusTokens(
        transfer=f"deployment-{cluster}", https=f"deployment-{cluster}-https"
    )


def test_one_cluster_connected_does_not_authorize_the_other(
    deployment_token, monkeypatch
):
    """The two enclaves pin different SSO domains and can be different
    identities, so a token for one is not evidence about the other."""
    monkeypatch.setattr(settings, "frontier_globus_refresh_token", None)
    monkeypatch.setattr(settings, "frontier_globus_https_refresh_token", None)
    cfg = UserConfig(
        odo_globus_token="mine-odo", odo_globus_https_token="mine-odo-https"
    )

    assert cfg.require_globus_token("odo") == mine("odo")
    with pytest.raises(ToolError):
        cfg.require_globus_token("frontier")


@pytest.mark.parametrize("cluster", ["odo", "frontier"])
def test_half_a_credential_is_not_one(cluster, no_deployment_token):
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
def test_a_half_credential_does_not_shadow_a_whole_one(cluster, deployment_token):
    """...and the deployment's complete pair is used instead, rather than the
    researcher being locked out of a server that works for everyone else."""
    cfg = UserConfig(
        odo_globus_token="stale-odo", frontier_globus_token="stale-frontier"
    )

    assert cfg.require_globus_token(cluster) == GlobusTokens(
        transfer=f"deployment-{cluster}", https=f"deployment-{cluster}-https"
    )


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
