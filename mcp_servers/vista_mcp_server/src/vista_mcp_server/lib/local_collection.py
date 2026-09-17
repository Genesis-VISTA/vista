"""This machine's Globus collection, brought up when a transfer first needs it.

Every Odo and Frontier file operation is brokered between two collections, and
one of them is this machine. Until this module existed, becoming one was the
launcher's job: it gated on a refresh token being in the environment, ran the
setup, and held the endpoint for the life of the process.

That stopped working once the credential started arriving from the interface.
The launcher runs before anyone has logged in, so it would have to poll for a
credential owned by a process it started -- which is a supervisor, and this
design deliberately has none. So the endpoint moved to the only component that
actually uses it. The credential already reaches this server on every HPC tool
call, in the metadata blob, so the first transfer that needs a collection has
everything required to create one and start it.

What that costs is a slow first transfer: a microVM boot, and Globus agreeing
the collection is online. It is reported as it happens rather than passed off
as a pause, and it is paid once per server lifetime instead of at every start
including the ones where nobody transfers anything.
"""

from __future__ import annotations

import asyncio
import logging
import platform

from fastmcp.exceptions import ToolError

from ..config import settings
from . import gcp_vm
from .globus import GlobusClient

log = logging.getLogger(__name__)

ONLINE_TIMEOUT_SECONDS = 120
"""How long to wait for Globus to report the collection connected.

Generous: it covers a microVM boot, Globus Connect Personal's first run, and
the round trip to Globus, on a laptop that may be doing other things. A wait
that gives up too early looks exactly like a broken installation.
"""

ONLINE_POLL_SECONDS = 2

_lock = asyncio.Lock()
"""One endpoint, however many tool calls arrive at once.

Two concurrent transfers both finding nothing running would otherwise both
create a microVM, and the second `msb create --replace` would take the first
one's endpoint out from under a transfer already using it.
"""

_endpoint: gcp_vm.Endpoint | None = None


def the_endpoint() -> gcp_vm.Endpoint:
    """The one endpoint this process owns.

    A single instance, because it holds the process handle: rebuilding it on
    each call would hand back an object that believes nothing is running and
    start a second one.

    Built from `settings` rather than from `gcp_vm.endpoint_from_environment`,
    which exists for the command-line entry point and reads the two environment
    variables directly. Inside this server that is the wrong source: unset, it
    falls back to `./data` relative to the working directory, while
    `settings.data_dir` has its own default -- and when those disagree the
    endpoint looks for the collection somewhere `vista_globus_collection_id`
    never reads, finds none, and registers a second one with Globus. Observed,
    not theorised: it cost a stray collection on a real account.
    """
    global _endpoint
    if _endpoint is None:
        _endpoint = gcp_vm.Endpoint(
            data_dir=settings.data_dir,
            hpc_jobs_dir=settings.local_hpc_jobs_dir,
        )
    return _endpoint


async def ensure_ready(globus: GlobusClient, cluster: str) -> str:
    """This machine's collection id, with a running endpoint behind it.

    Creates the collection if this installation has never had one, starts the
    endpoint if it is not running, and waits for Globus to agree it is online.
    Idempotent, and safe to call from concurrent tool calls.
    """
    endpoint = the_endpoint()

    # The common case by far, and it must stay cheap: every file operation
    # passes through here, and after the first one there is nothing to do.
    collection = settings.vista_globus_collection_id
    if collection and endpoint.running_here:
        return collection

    async with _lock:
        # Re-checked inside the lock. Callers that queued behind the one doing
        # the work are asking about a world that changed while they waited --
        # which is the whole point of them waiting rather than repeating it.
        collection = settings.vista_globus_collection_id
        if collection and endpoint.running_here:
            return collection

        if not endpoint.is_set_up:
            await _create_collection(globus, endpoint, cluster)
            collection = settings.vista_globus_collection_id

        if not collection:
            raise ToolError(
                f"VISTA's Globus collection could not be read after being "
                f"created, so {cluster.title()} file operations cannot be "
                f"brokered. Connecting Globus again in the VISTA user settings "
                f"is the thing to try."
            )

        if not endpoint.running_here:
            await _start(endpoint, cluster)
            await _wait_until_online(globus, collection, cluster)

        return collection


async def _create_collection(
    globus: GlobusClient, endpoint: gcp_vm.Endpoint, cluster: str
) -> None:
    """Register a collection for this machine and set it up without a terminal.

    The setup key is what removes the terminal. Globus Connect Personal's own
    interactive setup exists to obtain one by sending the researcher through a
    browser login, and they have already done that login -- it is where the
    credential in hand came from.
    """
    await _say("Setting up this machine as a Globus collection, a one-time step.")
    name = f"VISTA ({platform.node().split('.')[0] or 'desktop'})"
    try:
        key = await globus.create_gcp_endpoint(display_name=name)
        await asyncio.to_thread(endpoint.setup, key, interactive=False)
    except gcp_vm.EndpointError as error:
        raise ToolError(
            f"VISTA's Globus collection was registered but could not be set up "
            f"on this machine, so {cluster.title()} file operations cannot run: "
            f"{error}"
        ) from error
    except Exception as error:
        raise ToolError(
            f"VISTA could not create a Globus collection for this machine, so "
            f"{cluster.title()} file operations cannot run: {error}"
        ) from error
    log.info("Created the Globus collection %s", name)


async def _start(endpoint: gcp_vm.Endpoint, cluster: str) -> None:
    await _say("Starting VISTA's Globus endpoint. This takes a moment the first time.")
    # From the event loop's own thread, before the work moves off it:
    # `signal.signal` only works on the main thread. See the function for why
    # this is here rather than at startup, and why the lifespan is not enough.
    gcp_vm.install_stop_on_termination(endpoint.stop)
    try:
        await asyncio.to_thread(endpoint.start)
    except gcp_vm.EndpointError as error:
        raise ToolError(
            f"VISTA's Globus endpoint would not start, so {cluster.title()} "
            f"file operations cannot run: {error}"
        ) from error


async def _wait_until_online(
    globus: GlobusClient, collection: str, cluster: str
) -> None:
    """Wait for Globus to report the collection connected.

    Asking Globus rather than asking the microVM. A started process is not the
    same fact as a usable collection, and it is the second one a transfer needs
    -- submitting against a collection Globus does not yet see fails in a way
    that says nothing about why.
    """
    deadline = asyncio.get_running_loop().time() + ONLINE_TIMEOUT_SECONDS
    said = False
    while True:
        if await globus.gcp_connected(collection):
            return
        if not the_endpoint().running_here:
            raise ToolError(
                f"VISTA's Globus endpoint stopped while coming up, so "
                f"{cluster.title()} file operations cannot run: "
                f"{the_endpoint().status().detail}"
            )
        if asyncio.get_running_loop().time() >= deadline:
            raise ToolError(
                f"VISTA's Globus endpoint started but Globus has not reported it "
                f"online within {ONLINE_TIMEOUT_SECONDS} seconds, so "
                f"{cluster.title()} file operations cannot run yet. It may still "
                f"come up; trying again shortly is worthwhile."
            )
        if not said:
            await _say("Waiting for Globus to see the endpoint.")
            said = True
        await asyncio.sleep(ONLINE_POLL_SECONDS)


async def shutdown() -> None:
    """Stop the endpoint and take its microVM with it.

    Called from the server's lifespan. `Endpoint.start` also registers an
    `atexit` hook, which covers an ordinary interpreter exit; this covers the
    shutdowns that unwind rather than exit, and does it before the event loop
    is gone.
    """
    global _endpoint
    endpoint, _endpoint = _endpoint, None
    if endpoint is None:
        return
    await asyncio.to_thread(endpoint.stop)
    log.info("Stopped the Globus endpoint.")


async def _say(message: str) -> None:
    """Tell the researcher what is taking the time.

    Best effort. The context is only there inside a tool call, and this module
    is also reachable from tests and from the lifespan, where there is nothing
    to report to and nothing wrong with that.
    """
    try:
        from fastmcp.server.dependencies import get_context

        await get_context().info(message)
    except Exception:
        log.info("%s", message)
