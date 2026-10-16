"""
Sleep: telling researchers before the machine sleeps, and optionally keeping it awake.

Both are best effort. A machine asleep receives nothing, so the offline notice
should go out first; where the OS cannot tell us in time, the only cost is that
notice. Keeping the system awake is a setting, off by default, and only while
Slack is enabled. A closed laptop lid sleeps regardless on most machines.

- macOS: IOKit's system-power notification on a thread of its own (ctypes);
  `caffeinate -i -w <pid>` keeps it awake while VISTA runs.
- Linux: logind's `PrepareForSleep` signal, read from `dbus-monitor`, with a
  delay inhibitor held so the notice has time to go; `systemd-inhibit` keeps it
  awake.
- Windows: `SetThreadExecutionState` keeps it awake; there is no sleep notice.
"""

import asyncio
import ctypes
import logging
import os
import shutil
import sys
import threading
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

WHY = "VISTA is handling Slack while you are away"
NOTICE_BUDGET = 3.0
"""Seconds sleep is held for the offline notice."""

OnSleep = Callable[[], Coroutine[Any, Any, None]]


def prevent_sleep_command(platform: str, pid: int) -> list[str] | None:
    """The command that keeps the system awake until VISTA (`pid`) exits."""
    if platform == "darwin":
        return ["caffeinate", "-i", "-w", str(pid)]
    if platform.startswith("linux"):
        return [
            "systemd-inhibit",
            "--what=idle:sleep",
            "--who=VISTA",
            f"--why={WHY}",
            "--mode=block",
            "sleep",
            "infinity",
        ]
    return None  # Windows: an API call, not a command.


def linux_monitor_command() -> list[str]:
    return [
        "dbus-monitor",
        "--system",
        "type='signal',interface='org.freedesktop.login1.Manager',"
        "member='PrepareForSleep'",
    ]


def linux_delay_command() -> list[str]:
    """An inhibitor that holds sleep back until it exits (at most logind's delay)."""
    return [
        "systemd-inhibit",
        "--what=sleep",
        "--who=VISTA",
        "--why=Telling Slack VISTA is going offline",
        "--mode=delay",
        "sleep",
        "infinity",
    ]


def parse_prepare_for_sleep(lines: list[str]) -> bool | None:
    """From dbus-monitor's lines for one signal: True going to sleep, False waking."""
    if not any("member=PrepareForSleep" in line for line in lines):
        return None
    for line in lines:
        word = line.strip()
        if word == "boolean true":
            return True
        if word == "boolean false":
            return False
    return None


# -- keeping awake -------------------------------------------------------------

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class SleepPrevention:
    def __init__(self, platform: str = sys.platform) -> None:
        self._platform = platform
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self) -> bool:
        if self._platform == "win32":
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._windows_hold, name="vista-keep-awake", daemon=True
            )
            self._thread.start()
            return True
        command = prevent_sleep_command(self._platform, os.getpid())
        if command is None or shutil.which(command[0]) is None:
            log.info("Keeping the system awake is not available here")
            return False
        self._proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return True

    def _windows_hold(self) -> None:
        # The request belongs to the calling thread, so this thread lives as
        # long as it should hold.
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        self._stop.wait()
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    async def stop(self) -> None:
        self._stop.set()
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
        self._proc = None


# -- sleep notices ---------------------------------------------------------------

kIOMessageCanSystemSleep = 0xE0000270
kIOMessageSystemWillSleep = 0xE0000280
kIOMessageSystemHasPoweredOn = 0xE0000300


class SleepWatcher:
    """Calls `on_sleep` before the system sleeps, holding sleep for a moment."""

    def __init__(self, on_sleep: OnSleep, platform: str = sys.platform) -> None:
        self._on_sleep = on_sleep
        self._platform = platform
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._runloop: ctypes.c_void_p | None = None
        self._thread: threading.Thread | None = None
        self._delay: asyncio.subprocess.Process | None = None

    async def start(self) -> bool:
        self._loop = asyncio.get_running_loop()
        try:
            if self._platform == "darwin":
                return self._start_macos()
            if self._platform.startswith("linux"):
                return await self._start_linux()
        except Exception:
            log.warning("No sleep notice: could not watch for sleep", exc_info=True)
        return False

    def _notify_from_thread(self) -> None:
        """On the IOKit thread: run the notice on the loop, a bounded wait."""
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._on_sleep(), self._loop)
        try:
            future.result(timeout=NOTICE_BUDGET)
        except Exception:
            log.warning("The pre-sleep notice did not finish in time")

    # macOS
    def _start_macos(self) -> bool:
        iokit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/IOKit.framework/IOKit"
        )
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        callback_type = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
        )
        iokit.IORegisterForSystemPower.restype = ctypes.c_uint
        iokit.IORegisterForSystemPower.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            callback_type,
            ctypes.POINTER(ctypes.c_uint),
        ]
        iokit.IONotificationPortGetRunLoopSource.restype = ctypes.c_void_p
        iokit.IONotificationPortGetRunLoopSource.argtypes = [ctypes.c_void_p]
        iokit.IOAllowPowerChange.argtypes = [ctypes.c_uint, ctypes.c_long]
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopAddSource.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
        ready = threading.Event()
        state: dict[str, object] = {}

        def run() -> None:
            port = ctypes.c_void_p()
            notifier = ctypes.c_uint()
            root = ctypes.c_uint(0)

            def on_power(refcon, service, message_type, argument) -> None:
                notification = argument or 0
                if message_type == kIOMessageCanSystemSleep:
                    iokit.IOAllowPowerChange(root.value, notification)
                elif message_type == kIOMessageSystemWillSleep:
                    self._notify_from_thread()
                    iokit.IOAllowPowerChange(root.value, notification)

            handler = callback_type(on_power)
            state["handler"] = handler  # Kept alive as long as the thread.
            root.value = iokit.IORegisterForSystemPower(
                None, ctypes.byref(port), handler, ctypes.byref(notifier)
            )
            if not root.value:
                state["ok"] = False
                ready.set()
                return
            source = iokit.IONotificationPortGetRunLoopSource(port)
            runloop = cf.CFRunLoopGetCurrent()
            cf.CFRunLoopAddSource(runloop, source, mode)
            self._runloop = ctypes.c_void_p(runloop)
            state["ok"] = True
            ready.set()
            cf.CFRunLoopRun()

        self._cf = cf
        self._thread = threading.Thread(target=run, name="vista-sleep", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        return bool(state.get("ok"))

    # Linux
    async def _start_linux(self) -> bool:
        command = linux_monitor_command()
        if shutil.which(command[0]) is None:
            log.info("No sleep notice: dbus-monitor is not installed")
            return False
        monitor = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._hold_delay()
        self._task = asyncio.create_task(self._read_linux(monitor))
        return True

    async def _hold_delay(self) -> None:
        command = linux_delay_command()
        if shutil.which(command[0]) is None:
            return
        self._delay = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _release_delay(self) -> None:
        if self._delay is not None and self._delay.returncode is None:
            self._delay.terminate()
            await self._delay.wait()
        self._delay = None

    async def _read_linux(self, monitor: asyncio.subprocess.Process) -> None:
        assert monitor.stdout is not None
        lines: list[str] = []
        try:
            async for raw in monitor.stdout:
                line = raw.decode("utf-8", "replace")
                if line.startswith("signal "):
                    lines = []  # A new signal; whatever came before is done.
                lines.append(line)
                if parse_prepare_for_sleep(lines) is not None:
                    await self._linux_signal(lines)
                    lines = []
        finally:
            if monitor.returncode is None:
                monitor.terminate()
            await self._release_delay()

    async def _linux_signal(self, lines: list[str]) -> None:
        going = parse_prepare_for_sleep(lines)
        if going is True:
            try:
                await asyncio.wait_for(self._on_sleep(), timeout=NOTICE_BUDGET)
            except Exception:
                log.warning("The pre-sleep notice did not finish in time")
            await self._release_delay()  # Now let it sleep.
        elif going is False:
            await self._hold_delay()  # Awake: hold the next sleep too.

    async def stop(self) -> None:
        if self._runloop is not None:
            self._cf.CFRunLoopStop(self._runloop)
            self._runloop = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._release_delay()
