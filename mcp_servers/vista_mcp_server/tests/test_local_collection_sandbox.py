"""The lazy start's locking, proven against a really-booted microVM.

`test_local_collection.py` proves the same property with a fake endpoint,
deliberately slow to start so a caller racing past a start already in
progress would be visible. That is a reasonable stand-in for the property,
but not a substitute for it: a fake's timing is chosen by the test, while
`msb create` and `msb exec` take real wall-clock time whatever the test
asks for, and only a real process handle can show whether `running_here`
still means something once several callers have gone through it.

Marked `sandbox`, so the hermetic filter excludes it: it needs the
`vista-globus` image loaded and hardware virtualisation. A genuine Globus
registration is not needed, and deliberately avoided -- that is `live`
territory, exercised for real elsewhere (this change's own group-5
verification, against a live account). `globusconnectpersonal -start`
refuses to run against anything short of a real registration, so the guest
command is swapped for a real, harmless, long-lived one; everything around
it -- the microVM, the process handle, the lock -- is the genuine article.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from fakes import FakeGlobusClient
from vista_mcp_server.config import settings
from vista_mcp_server.lib import gcp_vm, local_collection
from vista_mcp_server.lib.gcp_vm import Endpoint

pytestmark = [pytest.mark.sandbox, pytest.mark.anyio]


@pytest.fixture(scope="module")
def msb():
    found = gcp_vm.msb_path()
    if found is None:
        pytest.skip("no microsandbox binary; set VISTA_MSB_PATH")
    image = subprocess.run(
        [str(found), "image", "inspect", "--format=json", gcp_vm.IMAGE],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if image.returncode != 0:
        pytest.skip(
            f"the {gcp_vm.IMAGE} image is not loaded; build the package or "
            f"`msb load` it first"
        )
    return found


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """A fresh module state per test, never a leftover endpoint or lock."""
    monkeypatch.setattr(local_collection, "_endpoint", None)
    monkeypatch.setattr(local_collection, "_lock", asyncio.Lock())
    monkeypatch.setattr(local_collection, "ONLINE_POLL_SECONDS", 0)
    yield
    local_collection._endpoint = None


@pytest.fixture
def endpoint(tmp_path, msb, monkeypatch):
    """A real endpoint, already "set up" and standing in `local_collection`'s
    module slot, with the guest command swapped for `sleep` -- a real
    long-lived process, so `running_here` reflects a genuine one, without
    needing the Globus registration `-start` would otherwise refuse without.
    """
    data_dir = tmp_path / "state"
    hpc_jobs_dir = tmp_path / "hpc_jobs"
    ep = Endpoint(data_dir=data_dir, hpc_jobs_dir=hpc_jobs_dir)
    ep.ensure_directories()
    ep.client_id_file.parent.mkdir(parents=True, exist_ok=True)
    ep.client_id_file.write_text("sandbox-test-collection\n")

    monkeypatch.setattr(Endpoint, "resolve_gcp", lambda self: gcp_vm.Path("/bin/sleep"))
    monkeypatch.setattr(Endpoint, "start_command", lambda self, gcp: [str(gcp), "60"])

    monkeypatch.setattr(local_collection, "_endpoint", ep)
    try:
        yield ep
    finally:
        ep.stop()


@pytest.fixture
def collection_id(monkeypatch):
    monkeypatch.setattr(
        type(settings),
        "vista_globus_collection_id",
        property(lambda self: "sandbox-test-collection"),
    )


def _counting(monkeypatch) -> list[int]:
    """Count real `create_vm` calls while still making each one real."""
    calls = [0]
    original = Endpoint.create_vm

    def _create_vm(self):
        calls[0] += 1
        return original(self)

    monkeypatch.setattr(Endpoint, "create_vm", _create_vm)
    return calls


async def test_concurrent_callers_produce_one_real_endpoint(
    monkeypatch, endpoint, collection_id
):
    calls = _counting(monkeypatch)
    globus = FakeGlobusClient()
    globus.connected = True

    results = await asyncio.gather(
        *(local_collection.ensure_ready(globus, "odo") for _ in range(8))
    )

    assert results == ["sandbox-test-collection"] * 8
    # The one that matters. `msb create --replace` would take the first
    # microVM out from under a transfer already using it.
    assert calls == [1]
    assert endpoint.running_here


async def test_a_start_in_progress_is_waited_on_for_real(
    monkeypatch, endpoint, collection_id
):
    calls = _counting(monkeypatch)
    globus = FakeGlobusClient()
    globus.connected = True

    first = asyncio.create_task(local_collection.ensure_ready(globus, "odo"))
    await asyncio.sleep(0)
    second = asyncio.create_task(local_collection.ensure_ready(globus, "odo"))

    results = await asyncio.gather(first, second)

    assert results == ["sandbox-test-collection"] * 2
    assert calls == [1]
    assert endpoint.running_here
