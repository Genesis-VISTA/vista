"""Test doubles for IRI / Globus / SSH boundaries (no network)."""

from .iri import FakeIriClient
from .globus import FakeGlobusClient
from .ssh import FakeSshConn

__all__ = ["FakeIriClient", "FakeGlobusClient", "FakeSshConn"]
