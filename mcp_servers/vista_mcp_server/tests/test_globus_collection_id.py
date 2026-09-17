"""The collection identifier is read when asked for, not once at startup.

The collection is created part way through this server's life, by whichever
file operation first needs one -- see `lib/local_collection.py`. While this was
a `functools.cached_property`, the absence observed before that happened was
cached for the life of the process, so a researcher who had just connected
Globus was still told there was no collection until they restarted VISTA.

What to do about an absent collection is `test_local_collection.py`'s subject.
This file is only about the reading being live.
"""

import pytest

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
