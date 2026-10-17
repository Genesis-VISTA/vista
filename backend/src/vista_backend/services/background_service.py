"""
Background mode: VISTA's services start at login and keep running with no
window, so Slack can reach VISTA while the researcher is away.

The login item runs the package launcher's `--service` mode, which writes
`<state>/service.json` while it runs; the VISTA application reads that file and
opens a window onto the running service instead of starting a second one.

- macOS: a LaunchAgent, `~/Library/LaunchAgents/<LABEL>.plist`, `RunAtLoad` and
  restarted only after a failure.
- Linux: a systemd user unit, `~/.config/systemd/user/vista.service`, enabled
  for `default.target` with `Restart=on-failure`.
- Windows: a scheduled task, `/SC ONLOGON`, running `vista.ps1 -Service` hidden.

Turning it on registers the item for the next login and does not start it now:
the window's own services hold the ports until it closes. Turning it off stops
a running service and removes the item. Only an installed package can do this;
a development checkout has no package launcher to register.
"""

import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings

LABEL = "org.vista.service"
UNIT = "vista.service"
TASK = "VISTA"
RESTART_DELAY = 30
""" Seconds before a failed service is started again, so a fault cannot spin. """

Run = Callable[[list[str]], None]


class BackgroundServiceError(Exception):
    """Background mode could not be changed; the message says why, for the UI."""


@dataclass
class Plan:
    """What turning background mode on or off does on one platform."""

    files: dict[Path, bytes] = field(default_factory=dict)
    """ Written on install, deleted on remove. """
    install: list[list[str]] = field(default_factory=list)
    """ Run after the files are written. """
    remove: list[list[str]] = field(default_factory=list)
    """ Run before the files are deleted; failures are ignored (not running). """
    marker: Path | None = None
    """ The file whose presence means installed; None to ask `query`. """
    query: list[str] | None = None
    """ A command that succeeds when installed (Windows). """


def launch_agent(launcher: Path, state: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [str(launcher), "--service"],
            "EnvironmentVariables": {"VISTA_HOME": str(state)},
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": RESTART_DELAY,
            "ProcessType": "Interactive",
            "StandardOutPath": str(state / "logs" / "service.log"),
            "StandardErrorPath": str(state / "logs" / "service.log"),
        }
    )


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_unit(launcher: Path, state: Path) -> bytes:
    return (
        "[Unit]\n"
        "Description=VISTA background service (Slack while you are away)\n"
        "After=network-online.target\n"
        f"StartLimitIntervalSec={RESTART_DELAY * 10}\n"
        "StartLimitBurst=5\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={_systemd_quote(str(launcher))} --service\n"
        f"Environment={_systemd_quote(f'VISTA_HOME={state}')}\n"
        "Restart=on-failure\n"
        f"RestartSec={RESTART_DELAY}\n"
        "KillMode=mixed\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    ).encode("utf-8")


def windows_command(launcher: Path) -> str:
    # powershell.exe on the script, not vista.cmd: the .cmd pauses on failure
    # for someone to read, and no one is looking at a login task's console.
    return (
        "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass "
        f'-File "{launcher}" -Service'
    )


def plan(platform: str, *, package: Path, state: Path, home: Path, uid: int) -> Plan:
    """What background mode is on `platform` for the package at `package`."""
    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        return Plan(
            files={plist: launch_agent(package / "vista", state)},
            remove=[["launchctl", "bootout", f"gui/{uid}/{LABEL}"]],
            marker=plist,
        )
    if platform.startswith("linux"):
        unit = home / ".config" / "systemd" / "user" / UNIT
        return Plan(
            files={unit: systemd_unit(package / "vista", state)},
            install=[
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", UNIT],
            ],
            remove=[
                ["systemctl", "--user", "disable", "--now", UNIT],
                ["systemctl", "--user", "daemon-reload"],
            ],
            marker=unit,
        )
    if platform == "win32":
        launcher = package / "vista.ps1"
        return Plan(
            install=[
                [
                    "schtasks",
                    "/Create",
                    "/F",
                    "/TN",
                    TASK,
                    "/SC",
                    "ONLOGON",
                    "/RL",
                    "LIMITED",
                    "/TR",
                    windows_command(launcher),
                ]
            ],
            remove=[
                ["schtasks", "/End", "/TN", TASK],
                ["schtasks", "/Delete", "/F", "/TN", TASK],
            ],
            query=["schtasks", "/Query", "/TN", TASK],
        )
    raise BackgroundServiceError(f"Background mode isn't available on {platform}.")


def _run(argv: list[str]) -> None:
    try:
        subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise BackgroundServiceError(f"{argv[0]} isn't available here.") from exc
    except subprocess.CalledProcessError as exc:
        why = (exc.stderr or exc.stdout or "").strip().splitlines()
        raise BackgroundServiceError(
            f"{' '.join(argv[:3])} failed: {why[-1] if why else exc.returncode}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackgroundServiceError(f"{argv[0]} did not answer.") from exc


@dataclass
class BackgroundService:
    platform: str
    package: Path | None
    state: Path
    home: Path
    uid: int = 0
    run: Run = _run

    @classmethod
    def here(cls) -> "BackgroundService":
        package = os.environ.get("VISTA_PACKAGE_DIR")
        return cls(
            platform=sys.platform,
            package=Path(package) if package else None,
            state=settings.data_dir,
            home=Path.home(),
            uid=os.getuid() if hasattr(os, "getuid") else 0,
        )

    def why_unavailable(self) -> str | None:
        if self.package is None:
            return "Background mode is only available in an installed VISTA package."
        if self.platform.startswith("linux") and shutil.which("systemctl") is None:
            return "Background mode needs systemd, which this machine doesn't have."
        try:
            self._plan()
        except BackgroundServiceError as exc:
            return str(exc)
        return None

    def _plan(self) -> Plan:
        if self.package is None:
            raise BackgroundServiceError(
                "Background mode is only available in an installed VISTA package."
            )
        return plan(
            self.platform,
            package=self.package,
            state=self.state,
            home=self.home,
            uid=self.uid,
        )

    def installed(self) -> bool:
        if self.package is None:
            return False
        try:
            p = self._plan()
        except BackgroundServiceError:
            return False
        if p.marker is not None:
            return p.marker.exists()
        if p.query is not None:
            try:
                self.run(p.query)
            except BackgroundServiceError:
                return False
            return True
        return False

    def install(self) -> None:
        """Register the login item; it starts at the next login."""
        p = self._plan()
        for path, content in p.files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(content)
            tmp.replace(path)
        for argv in p.install:
            self.run(argv)

    def remove(self) -> None:
        """Stop the service if it is running and remove the login item."""
        p = self._plan()
        for argv in p.remove:
            try:
                self.run(argv)
            except BackgroundServiceError:
                pass  # Not loaded, not running, or already gone.
        for path in p.files:
            path.unlink(missing_ok=True)


current: Callable[[], BackgroundService] = BackgroundService.here
""" The background service of this machine; tests replace it. """
