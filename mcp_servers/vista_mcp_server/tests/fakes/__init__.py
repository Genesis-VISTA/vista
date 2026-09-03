"""Test doubles for IRI / S3 boundaries (no network)."""

from .iri import FakeIriClient
from .s3 import FakeS3Client

__all__ = ["FakeIriClient", "FakeS3Client"]
