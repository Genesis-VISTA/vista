"""The OS secret store wrapper: no plaintext fallback, and entries scoped to the data directory."""

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
