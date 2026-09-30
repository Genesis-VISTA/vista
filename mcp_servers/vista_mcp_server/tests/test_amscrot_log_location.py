"""amscrot's log file must be openable before amscrot is imported (lib/iri.py)."""

import tempfile
from pathlib import Path

import pytest

from vista_mcp_server.lib import iri


@pytest.fixture
def no_log_location(monkeypatch):
    monkeypatch.delenv("AMSCROT_LOG_LOCATION", raising=False)


def _tmp_exists(monkeypatch, exists: bool):
    real_is_dir = Path.is_dir
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda self: exists if str(self) in ("/tmp", "\\tmp") else real_is_dir(self),
    )


def test_uses_the_temp_directory_when_tmp_is_missing(monkeypatch, no_log_location):
    _tmp_exists(monkeypatch, False)
    iri._default_amscrot_log_location()
    assert iri.os.environ["AMSCROT_LOG_LOCATION"] == str(
        Path(tempfile.gettempdir()) / "amscrot.log"
    )


def test_leaves_amscrots_default_where_tmp_exists(monkeypatch, no_log_location):
    _tmp_exists(monkeypatch, True)
    iri._default_amscrot_log_location()
    assert "AMSCROT_LOG_LOCATION" not in iri.os.environ


def test_an_explicit_location_wins(monkeypatch):
    monkeypatch.setenv("AMSCROT_LOG_LOCATION", "/somewhere/else.log")
    _tmp_exists(monkeypatch, False)
    iri._default_amscrot_log_location()
    assert iri.os.environ["AMSCROT_LOG_LOCATION"] == "/somewhere/else.log"
