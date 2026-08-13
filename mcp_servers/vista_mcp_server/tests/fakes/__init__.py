"""Test doubles for IRI / Globus boundaries (no network)."""

from .iri import FakeIriClient
from .globus import FakeGlobusClient

__all__ = ["FakeIriClient", "FakeGlobusClient"]
