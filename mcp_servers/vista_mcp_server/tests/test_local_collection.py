"""Bringing this machine's Globus collection up, once, when it is first needed.

The endpoint used to belong to the launcher, which knew at startup whether it
had a credential. It does not any more: a researcher connects Globus in the
interface, long after every service has started, so the first file operation to
need a collection is what creates and starts one.

Three things that has to get right, and they are what this file checks: doing
it once however many tool calls arrive together, not doing it again on an
installation that already has a collection, and saying which of the several
distinct failures happened.

No microVM is created here. `Endpoint` is faked, because what is under test is
the decision to start one rather than the starting.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from fakes import FakeGlobusClient
from vista_mcp_server.config import settings
from vista_mcp_server.lib import gcp_vm, local_collection

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class FakeEndpoint:
    """Everything `ensure_ready` asks an endpoint, and a record of what it did.

    `start` is deliberately slow-ish in the concurrency tests: the bug worth
    catching is a second caller sailing past a start already underway, which a
    start that returns instantly would hide.
    """

    def __init__(self, *, set_up: bool = False, start_delay: float = 0.0):
        self.is_set_up = set_up
        self.running_here = False
        self.start_delay = start_delay
        self.setup_calls: list[str | None] = []
        self.starts = 0
        self.stops = 0
        self.start_error: Exception | None = None
        self.detail = "the Globus endpoint is not running"

    def setup(self, setup_key=None, *, interactive: bool) -> None:
        # The setup key is what removes the terminal, and this is where that
        # claim is enforced: a tool call has no tty, so an interactive setup
        # would hang on a prompt nobody can see.
        assert interactive is False, "a tool call has no terminal to log in from"
        self.setup_calls.append(setup_key)
        self.is_set_up = True

    def start(self):
        import time

        if self.start_delay:
            time.sleep(self.start_delay)
        self.starts += 1
        if self.start_error is not None:
            raise self.start_error
        self.running_here = True

    def stop(self) -> None:
        self.stops += 1
        self.running_here = False

    def status(self) -> gcp_vm.Status:
        return gcp_vm.Status(gcp_vm.State.STOPPED, self.detail)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """A fresh module state per test, and never the real endpoint."""
    monkeypatch.setattr(local_collection, "_endpoint", None)
    monkeypatch.setattr(local_collection, "_lock", asyncio.Lock())
    monkeypatch.setattr(local_collection, "ONLINE_POLL_SECONDS", 0)
    yield
    local_collection._endpoint = None


@pytest.fixture
def collection(monkeypatch):
    """A collection id that the fake setup writes into being."""
    state = {"id": None}
    monkeypatch.setattr(
        type(settings),
        "vista_globus_collection_id",
        property(lambda self: state["id"]),
    )
    return state


def install(monkeypatch, endpoint: FakeEndpoint) -> FakeEndpoint:
    monkeypatch.setattr(local_collection, "_endpoint", endpoint)
    return endpoint


class TestAnInstallationWithNoCollection:
    async def test_it_creates_one_and_starts_it(self, monkeypatch, collection):
        endpoint = install(monkeypatch, FakeEndpoint(set_up=False))
        globus = FakeGlobusClient()
        globus.connected = True
        # The fake setup is what makes the collection readable, exactly as the
        # real one writes client-id.txt.
        original = endpoint.setup

        def setup(key=None, *, interactive):
            original(key, interactive=interactive)
            collection["id"] = "new-collection"

        endpoint.setup = setup

        assert await local_collection.ensure_ready(globus, "odo") == "new-collection"

        assert len(globus.created_endpoints) == 1
        assert endpoint.setup_calls == [globus.setup_key]
        assert endpoint.starts == 1


class TestAnInstallationThatAlreadyHasOne:
    async def test_it_does_not_create_a_second(self, monkeypatch, collection):
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True))
        globus = FakeGlobusClient()
        globus.connected = True

        assert (
            await local_collection.ensure_ready(globus, "odo") == "existing-collection"
        )

        assert globus.created_endpoints == []
        assert endpoint.setup_calls == []
        assert endpoint.starts == 1

    async def test_a_running_endpoint_is_left_alone(self, monkeypatch, collection):
        """The path every file operation after the first one takes."""
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True))
        endpoint.running_here = True
        globus = FakeGlobusClient()

        assert (
            await local_collection.ensure_ready(globus, "odo") == "existing-collection"
        )

        assert endpoint.starts == 0
        # Not even asked. Every transfer passes through here, and a round trip
        # to Globus on each one would be a real cost for a known answer.
        assert globus.created_endpoints == []


class TestWhereItLooksForTheCollection:
    """The endpoint and `vista_globus_collection_id` must mean the same
    directory, or the endpoint finds no collection where there is one.

    This is not hypothetical. `gcp_vm.endpoint_from_environment` reads the two
    environment variables and falls back to `./data` relative to the working
    directory, while `settings.data_dir` falls back to a path relative to the
    package. Building the endpoint from the former registered a second
    collection on a real Globus account, because `is_set_up` looked somewhere
    `vista_globus_collection_id` never reads.
    """

    async def test_it_is_the_one_settings_names(self, monkeypatch, tmp_path):
        monkeypatch.setattr(local_collection, "_endpoint", None)
        monkeypatch.setattr(settings, "data_dir", tmp_path / "state")
        monkeypatch.setattr(settings, "local_hpc_jobs_dir", tmp_path / "jobs")

        endpoint = local_collection.the_endpoint()

        assert endpoint.data_dir == tmp_path / "state"
        assert endpoint.hpc_jobs_dir == tmp_path / "jobs"

    async def test_it_agrees_with_the_collection_reader(self, monkeypatch, tmp_path):
        """The two paths that must not diverge, compared directly."""
        monkeypatch.setattr(local_collection, "_endpoint", None)
        monkeypatch.setattr(settings, "data_dir", tmp_path)
        client_id_file = tmp_path / "globusonline" / "lta" / "client-id.txt"
        client_id_file.parent.mkdir(parents=True)
        client_id_file.write_text("a-collection\n")

        endpoint = local_collection.the_endpoint()

        assert endpoint.client_id_file == client_id_file
        assert endpoint.is_set_up
        assert settings.vista_globus_collection_id == "a-collection"


class TestConcurrentCallers:
    async def test_they_produce_one_endpoint(self, monkeypatch, collection):
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True, start_delay=0.05))
        globus = FakeGlobusClient()
        globus.connected = True

        results = await asyncio.gather(
            *(local_collection.ensure_ready(globus, "odo") for _ in range(8))
        )

        assert results == ["existing-collection"] * 8
        # The one that matters. `msb create --replace` would take the first
        # endpoint out from under a transfer already using it.
        assert endpoint.starts == 1

    async def test_a_start_in_progress_is_waited_on(self, monkeypatch, collection):
        """Not repeated, and not skipped past either.

        Every caller has to come back with a *running* endpoint. Returning
        early because someone else is on it would hand back a collection that
        cannot move a file yet.
        """
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True, start_delay=0.05))
        globus = FakeGlobusClient()
        globus.connected = True

        first = asyncio.create_task(local_collection.ensure_ready(globus, "odo"))
        await asyncio.sleep(0)
        second = asyncio.create_task(local_collection.ensure_ready(globus, "odo"))

        await asyncio.gather(first, second)

        assert endpoint.starts == 1
        assert endpoint.running_here

    async def test_only_one_collection_is_created(self, monkeypatch, collection):
        """The expensive version of the same mistake: eight Globus collections."""
        endpoint = install(monkeypatch, FakeEndpoint(set_up=False))
        globus = FakeGlobusClient()
        globus.connected = True
        original = endpoint.setup

        def setup(key=None, *, interactive):
            original(key, interactive=interactive)
            collection["id"] = "new-collection"

        endpoint.setup = setup

        await asyncio.gather(
            *(local_collection.ensure_ready(globus, "odo") for _ in range(8))
        )

        assert len(globus.created_endpoints) == 1


class TestSayingWhatWentWrong:
    """Three distinct causes with three distinct remedies.

    The fourth -- no credential at all -- never reaches this module: the caller
    resolves a refresh token before it can build a client, and
    `UserConfig.require_globus_token` refuses first. That separation is the
    reason these messages can be about the endpoint.
    """

    async def test_no_collection_could_be_created(self, monkeypatch, collection):
        install(monkeypatch, FakeEndpoint(set_up=False))
        globus = FakeGlobusClient()

        async def refuse(*, display_name):
            raise RuntimeError("Globus said no")

        globus.create_gcp_endpoint = refuse

        with pytest.raises(ToolError) as refusal:
            await local_collection.ensure_ready(globus, "odo")

        message = str(refusal.value)
        assert "could not create a Globus collection" in message
        assert "Globus said no" in message
        assert "Odo" in message

    async def test_the_endpoint_would_not_start(self, monkeypatch, collection):
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True))
        endpoint.start_error = gcp_vm.EndpointError("no image, and no archive")

        with pytest.raises(ToolError) as refusal:
            await local_collection.ensure_ready(FakeGlobusClient(), "frontier")

        message = str(refusal.value)
        assert "would not start" in message
        assert "no image, and no archive" in message
        assert "Frontier" in message

    async def test_the_endpoint_died_while_coming_up(self, monkeypatch, collection):
        """Started, then gone. Distinct from never having started, because the
        cause is in the endpoint's own account of itself rather than in an
        exception nobody caught."""
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True))
        endpoint.detail = "the Globus endpoint's microVM is not running"

        def start():
            endpoint.starts += 1
            endpoint.running_here = False  # exits immediately

        endpoint.start = start

        with pytest.raises(ToolError) as refusal:
            await local_collection.ensure_ready(FakeGlobusClient(), "odo")

        assert "stopped while coming up" in str(refusal.value)
        assert "microVM is not running" in str(refusal.value)

    async def test_globus_never_sees_it(self, monkeypatch, collection):
        """The endpoint is up and Globus still does not report it connected.

        Worth its own message: nothing is broken locally, so a message about
        the endpoint failing would send the researcher looking in the wrong
        place.
        """
        collection["id"] = "existing-collection"
        install(monkeypatch, FakeEndpoint(set_up=True))
        monkeypatch.setattr(local_collection, "ONLINE_TIMEOUT_SECONDS", 0)
        globus = FakeGlobusClient()
        globus.connected = False

        with pytest.raises(ToolError) as refusal:
            await local_collection.ensure_ready(globus, "odo")

        assert "has not reported it online" in str(refusal.value)


class TestShutdown:
    async def test_it_stops_an_endpoint_that_was_started(self, monkeypatch, collection):
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True))
        globus = FakeGlobusClient()
        globus.connected = True
        await local_collection.ensure_ready(globus, "odo")

        await local_collection.shutdown()

        assert endpoint.stops == 1

    async def test_it_is_cheap_when_nothing_was_started(self):
        """Called from the lifespan on every shutdown, including the many where
        no transfer ever happened."""
        assert local_collection._endpoint is None
        await local_collection.shutdown()  # no error, nothing to do

    async def test_a_second_call_does_nothing(self, monkeypatch, collection):
        collection["id"] = "existing-collection"
        endpoint = install(monkeypatch, FakeEndpoint(set_up=True))
        globus = FakeGlobusClient()
        globus.connected = True
        await local_collection.ensure_ready(globus, "odo")

        await local_collection.shutdown()
        await local_collection.shutdown()

        assert endpoint.stops == 1
