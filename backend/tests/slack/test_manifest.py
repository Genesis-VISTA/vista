"""The Slack app manifest parses and asks for what the transport uses."""

from pathlib import Path

import yaml

import vista_backend.slack

MANIFEST = Path(vista_backend.slack.__file__).parent / "manifest.yaml"


def load() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_is_socket_mode_with_no_request_url():
    settings = load()["settings"]
    assert settings["socket_mode_enabled"] is True
    assert settings["interactivity"]["is_enabled"] is True
    assert "request_url" not in settings["interactivity"]
    assert "request_url" not in settings["event_subscriptions"]


def test_manifest_subscribes_to_the_events_handled():
    events = set(load()["settings"]["event_subscriptions"]["bot_events"])
    assert events == {"app_home_opened", "app_mention", "message.im"}


def test_manifest_scopes_cover_the_calls_and_nothing_wider():
    scopes = set(load()["oauth_config"]["scopes"]["bot"])
    assert {
        "app_mentions:read",
        "chat:write",
        "im:history",
        "im:write",
        "files:write",
        "pins:read",
        "pins:write",
    } <= scopes
    # No reading channels VISTA wasn't mentioned in, and no user directory.
    assert not scopes & {"channels:history", "groups:history", "users:read"}


def test_manifest_has_a_home_tab_and_dms():
    home = load()["features"]["app_home"]
    assert home["home_tab_enabled"] is True
    assert home["messages_tab_enabled"] is True
    assert home["messages_tab_read_only_enabled"] is False
