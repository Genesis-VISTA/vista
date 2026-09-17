"""
Job tools must name an incomplete Globus setup, not fail obscurely.

`scripts/launch.sh` used to run `launch_globus.py --setup` unconditionally, so
transfer setup either succeeded or the launch died. It is now gated on a
configured refresh token and non-fatal, which means a running VISTA can
legitimately have no Globus collection — including on a host that simply has
no container runtime. The dispatchers for the two clusters whose file
operations depend on it have to say so.

Marked `hpc`: excluded from PR CI by `ci-local.sh`'s hermetic filter, since
these reach the real dispatchers rather than a fake.
"""

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server import submit_job_mcp
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig

pytestmark = [pytest.mark.hpc, pytest.mark.anyio]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def no_globus_collection(monkeypatch):
    """
    No Globus collection, as after a skipped or failed setup.

    `vista_globus_collection_id` is a property reading
    `<data_dir>/globusonline/lta/client-id.txt`, so it is patched on the class
    rather than the instance: a property with no setter cannot be assigned to.
    """
    monkeypatch.setattr(
        type(settings),
        "vista_globus_collection_id",
        property(lambda self: None),
        raising=False,
    )


@pytest.mark.parametrize(
    "dispatch,cluster",
    [
        (submit_job_mcp._submit_odo_job, "Odo"),
        (submit_job_mcp._submit_frontier_job, "Frontier"),
    ],
    ids=["odo", "frontier"],
)
async def test_dispatch_names_the_incomplete_setup(
    no_globus_collection, dispatch, cluster
):
    cfg = UserConfig()
    with pytest.raises(ToolError) as excinfo:
        await dispatch(cfg, "example", None, None, None)

    message = str(excinfo.value)
    assert "Globus collection is not set up" in message, message
    # Names a remedy the researcher can perform. A packaged installation has no
    # `scripts/` directory, so anything under it is an instruction to run a file
    # that is not there.
    assert "scripts/" not in message, message
    assert "Restart VISTA" in message, message


async def test_perlmutter_is_unaffected(no_globus_collection):
    """
    The cluster that must keep working. Perlmutter never touches Globus
    Transfer — every file operation goes through the NERSC IRI filesystem API
    — so an absent collection must not appear anywhere on its path.
    """
    import inspect

    source = inspect.getsource(submit_job_mcp._submit_perlmutter_job)
    assert "vista_globus_collection_id" not in source
    assert "create_globus_client" not in source
