"""The collection identifier is read when asked for, not once at startup.

Transfer setup can complete after this server is running -- the packaged
launcher performs it during startup, and moving the login into the interface
would make it later still. While this was a `functools.cached_property`, a
researcher who completed the Globus login was told the collection did not exist
until they restarted VISTA, because the absence had been cached.
"""

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.config import settings

pytestmark = pytest.mark.unit


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def write_collection(data_dir, collection):
    client_id_file = data_dir / "globusonline" / "lta" / "client-id.txt"
    client_id_file.parent.mkdir(parents=True, exist_ok=True)
    client_id_file.write_text(f"{collection}\n")


def test_absent_before_setup(data_dir):
    assert settings.vista_globus_collection_id is None


def test_seen_once_setup_writes_it(data_dir):
    """The whole point: read after the absence has already been observed."""
    assert settings.vista_globus_collection_id is None

    write_collection(data_dir, "collection-from-setup")

    assert settings.vista_globus_collection_id == "collection-from-setup"


def test_an_empty_file_is_not_a_collection(data_dir):
    """Globus Connect Personal creates the directory before it has an answer."""
    write_collection(data_dir, "   ")

    assert settings.vista_globus_collection_id is None


def test_the_remedy_is_one_a_packaged_installation_offers(data_dir):
    with pytest.raises(ToolError) as refusal:
        settings.require_globus_collection("odo")

    message = str(refusal.value)
    # `scripts/` is not installed by the package, so naming anything under it
    # is an instruction to run a file the researcher does not have.
    assert "scripts/" not in message
    assert "Odo" in message


def test_it_returns_the_collection_once_there_is_one(data_dir):
    write_collection(data_dir, "collection-from-setup")

    assert settings.require_globus_collection("frontier") == "collection-from-setup"
