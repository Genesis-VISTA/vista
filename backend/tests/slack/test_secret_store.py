"""
The secret store: the OS store with no plaintext fallback and entries scoped to
the data directory, or a private directory for a hosted VISTA.
"""

import stat
import sys

import keyring
import pytest
from keyring.backends import fail

from vista_backend.utils import secret_store

pytestmark = pytest.mark.unit


def test_a_secret_round_trips(memory_keyring):
    secret_store.put(secret_store.SLACK_BOT_TOKEN, "xoxb-1")
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) == "xoxb-1"
    assert secret_store.is_set(secret_store.SLACK_BOT_TOKEN)
    secret_store.delete(secret_store.SLACK_BOT_TOKEN)
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) is None
    secret_store.delete(secret_store.SLACK_BOT_TOKEN)  # deleting twice is fine


def test_entries_are_named_for_the_data_directory(
    memory_keyring, monkeypatch, tmp_path
):
    secret_store.put(secret_store.SLACK_BOT_TOKEN, "xoxb-here")
    monkeypatch.setattr(secret_store.settings, "data_dir", tmp_path / "elsewhere")
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) is None


def test_no_store_refuses_rather_than_falls_back():
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    try:
        ok, why = secret_store.available()
        assert not ok and "secret store" in (why or "")
        with pytest.raises(secret_store.SecretStoreUnavailable):
            secret_store.put(secret_store.SLACK_APP_TOKEN, "xapp-1")
        assert secret_store.get(secret_store.SLACK_APP_TOKEN) is None
    finally:
        keyring.set_keyring(previous)


# The directory backend, for a hosted VISTA with no OS store.

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")


@pytest.fixture
def secrets_dir(tmp_path, monkeypatch):
    path = tmp_path / "secrets"
    monkeypatch.setattr(secret_store.settings.secrets, "backend", "directory")
    monkeypatch.setattr(secret_store.settings.secrets, "directory", path)
    return path


def test_a_secret_round_trips_through_a_directory(secrets_dir):
    assert secret_store.available() == (True, None)
    secret_store.put(secret_store.SLACK_BOT_TOKEN, "xoxb-dir")

    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) == "xoxb-dir"
    assert (secrets_dir / secret_store.SLACK_BOT_TOKEN).read_text(
        encoding="utf-8"
    ) == "xoxb-dir"
    assert secret_store.is_set(secret_store.SLACK_BOT_TOKEN)
    secret_store.delete(secret_store.SLACK_BOT_TOKEN)
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) is None
    secret_store.delete(secret_store.SLACK_BOT_TOKEN)  # deleting twice is fine


def test_a_mounted_file_is_read_without_its_newline(secrets_dir):
    secrets_dir.mkdir(mode=0o700)
    (secrets_dir / secret_store.SLACK_APP_TOKEN).write_text(
        "xapp-mounted\n", encoding="utf-8"
    )
    assert secret_store.get(secret_store.SLACK_APP_TOKEN) == "xapp-mounted"


@posix_only
def test_secret_files_are_created_for_this_user_only(secrets_dir):
    secret_store.put(secret_store.SLACK_BOT_TOKEN, "xoxb-dir")

    assert (
        stat.S_IMODE((secrets_dir / secret_store.SLACK_BOT_TOKEN).stat().st_mode)
        == 0o600
    )
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700


@posix_only
def test_a_directory_open_to_others_is_refused(secrets_dir):
    secrets_dir.mkdir()
    secrets_dir.chmod(0o750)
    (secrets_dir / secret_store.SLACK_BOT_TOKEN).write_text("xoxb-x", encoding="utf-8")

    ok, why = secret_store.available()
    assert not ok and "open to other users" in (why or "")
    with pytest.raises(secret_store.SecretStoreUnavailable):
        secret_store.put(secret_store.SLACK_BOT_TOKEN, "xoxb-new")
    assert secret_store.get(secret_store.SLACK_BOT_TOKEN) is None


def test_an_unknown_name_is_not_a_file(tmp_path):
    with pytest.raises(ValueError):
        secret_store.DirectoryBackend(tmp_path).put("../escape", "x")


def test_auto_is_a_directory_when_hosted_and_the_os_store_otherwise(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(secret_store.settings.secrets, "backend", "auto")
    monkeypatch.setattr(secret_store.settings.secrets, "directory", None)
    monkeypatch.setattr(secret_store.settings, "data_dir", tmp_path)

    monkeypatch.setattr(secret_store.settings, "deployment", "hosted")
    hosted = secret_store.backend()
    assert isinstance(hosted, secret_store.DirectoryBackend)
    assert hosted.directory == tmp_path / "secrets"

    monkeypatch.setattr(secret_store.settings, "deployment", "desktop")
    assert isinstance(secret_store.backend(), secret_store.OsBackend)
