"""
Background mode's login item, per OS: the files it writes and the commands it
runs. Every command is recorded, never run, and files go to a temporary home.
"""

import configparser
import plistlib

import pytest

from vista_backend.api.slack import SlackConfigUpdate, update_slack_config
from vista_backend.services.background_service import (
    LABEL,
    TASK,
    UNIT,
    BackgroundService,
    BackgroundServiceError,
)


def _service(tmp_path, platform: str, *, package="pkg", fail=()) -> BackgroundService:
    ran: list[list[str]] = []

    def run(argv):
        ran.append(argv)
        if argv[:2] in [list(f) for f in fail] or argv[:3] in [list(f) for f in fail]:
            raise BackgroundServiceError(f"{argv[0]} failed")

    svc = BackgroundService(
        platform=platform,
        package=tmp_path / package if package else None,
        state=tmp_path / "state with space",
        home=tmp_path / "home",
        uid=501,
        run=run,
    )
    svc.ran = ran  # type: ignore[attr-defined]
    return svc


def test_macos_launch_agent(tmp_path):
    svc = _service(tmp_path, "darwin")
    assert not svc.installed()

    svc.install()

    path = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    agent = plistlib.loads(path.read_bytes())
    assert agent["Label"] == LABEL
    assert agent["ProgramArguments"] == [str(tmp_path / "pkg" / "vista"), "--service"]
    assert agent["EnvironmentVariables"] == {"VISTA_HOME": str(svc.state)}
    assert agent["RunAtLoad"] is True
    assert agent["KeepAlive"] == {"SuccessfulExit": False}, "restart on failure only"
    assert agent["StandardErrorPath"].endswith("logs/service.log")
    assert svc.ran == [], "registered for the next login, not started now"  # type: ignore[attr-defined]
    assert svc.installed()

    svc.remove()

    assert svc.ran == [["launchctl", "bootout", f"gui/501/{LABEL}"]]  # type: ignore[attr-defined]
    assert not path.exists() and not svc.installed()


def test_linux_systemd_user_unit(tmp_path):
    svc = _service(tmp_path, "linux")

    svc.install()

    path = tmp_path / "home" / ".config" / "systemd" / "user" / UNIT
    unit = configparser.ConfigParser(interpolation=None)
    unit.read_string(path.read_text(encoding="utf-8"))
    launcher = tmp_path / "pkg" / "vista"
    assert unit["Service"]["ExecStart"] == f'"{launcher}" --service'
    assert unit["Service"]["Environment"] == f'"VISTA_HOME={svc.state}"'
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Install"]["WantedBy"] == "default.target"
    assert svc.ran == [  # type: ignore[attr-defined]
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", UNIT],
    ]
    assert svc.installed()

    svc.ran.clear()  # type: ignore[attr-defined]
    svc.remove()

    assert svc.ran == [  # type: ignore[attr-defined]
        ["systemctl", "--user", "disable", "--now", UNIT],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert not path.exists()


def test_windows_scheduled_task(tmp_path):
    svc = _service(tmp_path, "win32", fail=[("schtasks", "/Query")])
    assert not svc.installed(), "a failed query means no task"

    svc.install()

    (create,) = [a for a in svc.ran if a[1] == "/Create"]  # type: ignore[attr-defined]
    assert create[create.index("/TN") + 1] == TASK
    assert create[create.index("/SC") + 1] == "ONLOGON"
    command = create[create.index("/TR") + 1]
    assert command.startswith("powershell.exe -NoProfile -WindowStyle Hidden")
    assert command.endswith(f'-File "{tmp_path / "pkg" / "vista.ps1"}" -Service')

    svc.ran.clear()  # type: ignore[attr-defined]
    svc.remove()
    assert svc.ran == [  # type: ignore[attr-defined]
        ["schtasks", "/End", "/TN", TASK],
        ["schtasks", "/Delete", "/F", "/TN", TASK],
    ]


def test_remove_when_nothing_is_running_still_removes(tmp_path):
    svc = _service(tmp_path, "darwin", fail=[("launchctl", "bootout")])
    svc.install()

    svc.remove()

    assert not svc.installed()


def test_a_development_checkout_has_no_background_mode(tmp_path):
    svc = _service(tmp_path, "darwin", package=None)

    assert "installed VISTA package" in (svc.why_unavailable() or "")
    assert not svc.installed()
    with pytest.raises(BackgroundServiceError):
        svc.install()


def test_an_unknown_platform_is_unavailable(tmp_path):
    assert "isn't available" in (_service(tmp_path, "aix").why_unavailable() or "")


@pytest.mark.anyio
async def test_the_settings_switch_registers_and_removes(
    session, alice, no_real_login_item
):
    view = await update_slack_config(
        SlackConfigUpdate(background_mode=True), session, alice
    )
    assert view.background_mode is True and view.background_problem is None

    view = await update_slack_config(
        SlackConfigUpdate(background_mode=False), session, alice
    )
    assert view.background_mode is False
    assert no_real_login_item.ran == [["launchctl", "bootout", f"gui/501/{LABEL}"]]


@pytest.mark.anyio
async def test_the_switch_says_why_it_cannot(session, alice, tmp_path, monkeypatch):
    from fastapi import HTTPException

    from vista_backend.services import background_service

    dev = _service(tmp_path, "darwin", package=None)
    monkeypatch.setattr(background_service, "current", lambda: dev)

    with pytest.raises(HTTPException) as refused:
        await update_slack_config(
            SlackConfigUpdate(background_mode=True), session, alice
        )
    assert refused.value.status_code == 400
    assert "installed VISTA package" in refused.value.detail
