"""Sleep prevention and sleep notices: the commands and parsing, nothing run."""

import os

from vista_backend.slack.power import (
    linux_delay_command,
    linux_monitor_command,
    parse_prepare_for_sleep,
    prevent_sleep_command,
)


def test_macos_keeps_awake_only_while_vista_runs():
    assert prevent_sleep_command("darwin", 4242) == ["caffeinate", "-i", "-w", "4242"]


def test_linux_blocks_idle_sleep_with_a_reason():
    command = prevent_sleep_command("linux", os.getpid())
    assert command is not None
    assert command[:2] == ["systemd-inhibit", "--what=idle:sleep"]
    assert "--mode=block" in command
    assert any(arg.startswith("--why=") for arg in command)


def test_windows_uses_the_api_not_a_command():
    assert prevent_sleep_command("win32", 1) is None


def test_linux_watches_logind_and_holds_sleep_back():
    assert linux_monitor_command()[:2] == ["dbus-monitor", "--system"]
    assert "member='PrepareForSleep'" in linux_monitor_command()[2]
    assert "--mode=delay" in linux_delay_command()
    assert "--what=sleep" in linux_delay_command()


SIGNAL = (
    "signal time=1.0 sender=:1.2 -> destination=(null) serial=9 "
    "path=/org/freedesktop/login1; interface=org.freedesktop.login1.Manager; "
    "member=PrepareForSleep"
)


def test_prepare_for_sleep_is_read_both_ways():
    assert parse_prepare_for_sleep([SIGNAL, "   boolean true"]) is True
    assert parse_prepare_for_sleep([SIGNAL, "   boolean false"]) is False
    assert parse_prepare_for_sleep([SIGNAL]) is None
    assert parse_prepare_for_sleep(["signal member=Other", "boolean true"]) is None
