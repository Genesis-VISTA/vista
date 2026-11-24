"""
Hosted deployments offer only the clusters in the metadata's `allowed_clusters`
(openspec change multi-user-hosted, D9): the job and transfer tools refuse the
others before doing anything, and the shared Globus pair is not a fallback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as jobs
import vista_mcp_server.transfer_mcp as transfer
from vista_mcp_server.lib.user_config import UserConfig

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

USER = UserConfig(
    odo_s3m_token="o",
    frontier_s3m_token="f",
    globus_token="shared",
    globus_https_token="shared-https",
)


def _ctx(allowed=("odo",)):
    vista = {"user": USER.model_dump(exclude={"allowed_clusters"}), "project_paths": {}}
    if allowed is not None:
        vista["allowed_clusters"] = list(allowed)
    return SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista))
    )


@pytest.mark.parametrize("cluster", ["frontier", "lux", "perlmutter"])
async def test_every_cluster_tool_refuses_a_cluster_outside_the_list(cluster):
    calls = [
        jobs.submit_hpc_job(_ctx(), job="x", cluster=cluster),
        jobs.preview_hpc_job(_ctx(), job="x", cluster=cluster),
        jobs.get_hpc_job_status(_ctx(), job_id="1", cluster=cluster),
        jobs.read_hpc_job_state(_ctx(), job_id="1", cluster=cluster),
        jobs.get_hpc_job_outputs(_ctx(), job_id="1", files=["a"], cluster=cluster),
        jobs.get_hpc_queue_status(_ctx(), cluster=cluster),
        jobs.cancel_hpc_job(_ctx(), job_id="1", cluster=cluster),
        transfer.hpc_list_files(_ctx(), cluster=cluster, path="/x"),
        transfer.hpc_download_files(_ctx(), cluster=cluster, remote_paths=["/x"]),
        transfer.hpc_upload_files(
            _ctx(), cluster=cluster, local_paths=["a"], remote_dir="/x"
        ),
    ]
    for call in calls:
        with pytest.raises(ToolError, match="only odo available here"):
            await call


async def test_no_key_means_no_restriction():
    from vista_mcp_server.lib.user_config import get_vista_meta

    user = get_vista_meta(_ctx(None)).user
    user.require_cluster_allowed("frontier")


async def test_the_shared_globus_pair_is_not_a_fallback_when_restricted():
    from vista_mcp_server.lib.user_config import MissingCredential, get_vista_meta

    restricted = get_vista_meta(_ctx()).user
    with pytest.raises(MissingCredential):
        restricted.require_globus_token("odo")
    open_ = get_vista_meta(_ctx(None)).user
    assert open_.require_globus_token("odo").transfer == "shared"
