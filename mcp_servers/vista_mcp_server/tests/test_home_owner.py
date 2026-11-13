"""
The researcher's POSIX username, which names their OLCF sources folder
(`<remote_dir>.<user>.jobs`). Odo and Frontier take it from Globus: one
Transfer `stat` of `/~/`, whose `user` is the account the researcher's identity
maps to -- which differs between the two enclaves. Lux takes it from the SSH
login.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.lib import globus as g
from vista_mcp_server.lib.types import GlobusTokens

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _Stat:
    def __init__(self, entry):
        self.entry = entry
        self.calls: list[tuple[str, str]] = []

    def operation_stat(self, endpoint, path):
        self.calls.append((endpoint, path))
        return type("R", (), {"data": self.entry})()


@pytest.fixture(autouse=True)
def _fresh_cache():
    g._home_owners.clear()
    yield
    g._home_owners.clear()


def _client(monkeypatch, entry, transfer="t1"):
    c = g.GlobusClient(tokens=GlobusTokens(transfer=transfer, https="h"), cluster="odo")
    fake = _Stat(entry)
    monkeypatch.setattr(c, "_transfer", lambda: fake)
    return c, fake


async def test_the_owner_of_home_is_the_username(monkeypatch):
    c, fake = _client(monkeypatch, {"name": "~", "type": "dir", "user": "jhi"})
    assert await c.home_owner(collection_id="odo-coll") == "jhi"
    assert fake.calls == [("odo-coll", "/~/")]


async def test_it_is_asked_once_per_credential(monkeypatch):
    c, fake = _client(monkeypatch, {"user": "jhi"})
    await c.home_owner(collection_id="odo-coll")
    await c.home_owner(collection_id="odo-coll")
    assert len(fake.calls) == 1
    other, other_fake = _client(monkeypatch, {"user": "hinesjr"}, transfer="t2")
    assert await other.home_owner(collection_id="odo-coll") == "hinesjr"


async def test_no_owner_reported_is_an_error_not_a_guess(monkeypatch):
    c, _ = _client(monkeypatch, {"name": "~", "type": "dir", "user": None})
    with pytest.raises(ToolError, match="sources folder"):
        await c.home_owner(collection_id="odo-coll")
