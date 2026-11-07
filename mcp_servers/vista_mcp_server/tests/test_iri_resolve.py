"""
Which IRI compute resource a client submits to (lib/iri.py).

The moderate enclave lists Lux before Frontier, and the Frontier client used to
fall back to the first listed resource, sending Frontier jobs to Lux. These
tests pin that down without amscrot installed: discovery is faked, and the
client is built without its amscrot constructor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from vista_mcp_server.config import settings
from vista_mcp_server.lib import iri

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

FRONTIER_ID = "5173cdb4-d82f-5c81-8ef0-598997c12813"
LUX_ID = "7fd317bd-c856-5162-89df-455146909084"


@dataclass
class _Res:
    """The two attributes of an amscrot discovery entry that matching reads."""

    name: str
    data: dict = field(default_factory=dict)


@dataclass
class _Discovery:
    compute: list
    storage: list = field(default_factory=list)


class _ServiceClient:
    def __init__(self, discovery: _Discovery):
        self._discovery = discovery

    def discover(self) -> _Discovery:
        return self._discovery


def _client(
    machine: str, discovery: _Discovery, *, pinned: str | None = None
) -> iri.IriClient:
    """An IriClient with faked discovery, skipping the amscrot constructor."""
    c = iri.IriClient.__new__(iri.IriClient)
    c.machine = machine
    c.profile = "test-profile"
    c._service_client = _ServiceClient(discovery)
    c._compute_resource_id = pinned
    c._storage_resource_id = None
    return c


# The moderate enclave as it lists itself since Lux arrived: Lux first.
MODERATE = _Discovery(
    compute=[
        _Res("Lux", {"id": LUX_ID, "group": "olcf"}),
        _Res("Frontier", {"id": FRONTIER_ID, "group": "olcf"}),
    ]
)


async def test_frontier_client_is_pinned_to_frontier(monkeypatch):
    """create_olcf_iri_client passes the configured Frontier id, so discovery never runs."""
    seen: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def init_resources(self):
            pass

    monkeypatch.setattr(iri, "IriClient", _Recorder)
    await iri.create_olcf_iri_client(iri_token="frontier-token")

    assert (
        seen["compute_resource_id"]
        == settings.frontier_compute_resource_id
        == FRONTIER_ID
    )
    assert seen["api_endpoint"] == settings.frontier_iri_url


async def test_pinned_id_skips_discovery():
    c = _client("frontier", _Discovery(compute=[]), pinned=FRONTIER_ID)
    await c.init_resources()
    assert c.compute_resource_id == FRONTIER_ID


async def test_frontier_matches_by_name_ignoring_case_even_when_lux_is_first():
    c = _client("frontier", MODERATE)
    await c.init_resources()
    assert c.compute_resource_id == FRONTIER_ID


async def test_no_match_is_refused_not_replaced_by_the_first_resource():
    c = _client("frontier", _Discovery(compute=[MODERATE.compute[0]]))  # Lux only
    with pytest.raises(
        RuntimeError, match=r"no compute resource for 'frontier' \(found 'Lux'\)"
    ):
        await c.init_resources()


async def test_perlmutter_matches_its_compute_group_not_login():
    nersc = _Discovery(
        compute=[
            _Res("login", {"id": "login-id", "group": "perlmutter"}),
            _Res("compute", {"id": "compute-id", "group": "perlmutter"}),
        ],
        storage=[_Res("homes", {"id": "homes-id"})],
    )
    c = _client("perlmutter", nersc)
    await c.init_resources()
    assert c.compute_resource_id == "compute-id"
    assert c.storage_resource_id == "homes-id"
