"""
Is there a git the Hypothesis Lab can use?

The lab's forum is a git repository, and VISTA drives the user's own `git`
rather than shipping one (openspec/changes/forum-git-backend, D9). So whether
the lab can run on this machine is a question about that git, and it has to be
answered honestly and without side effects:

- **Absent** is an ordinary state on a researcher's laptop, not an error. The
  lab turns off and says so; the rest of VISTA never notices.
- **macOS lies about having git.** `/usr/bin/git` always exists, but until the
  Command Line Tools are installed it is a shim that pops an install dialog when
  run. Running it to ask its version would put that dialog in front of someone
  who only opened a page. So when `git` resolves to the shim, `xcode-select -p`
  is asked first, and a non-zero exit means absent — the shim is never run.
- **Too old** is reported with both versions, so the fix is obvious.

`reason` is the sentence the UI shows, verbatim. It is written for the person
reading it, not for a log.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_VERSION = (2, 34)
"""
Oldest git the lab accepts.

Nothing v1 does needs it — plumbing, compare-and-swap `update-ref` and refspec
fetches are all far older. It is the floor SSH commit signing needs, kept so
that adding signing later does not raise the requirement on people who already
have the lab working. Lower it if it excludes someone.
"""

CHECK_TIMEOUT = 5.0
""" Seconds for each probe. `git --version` answers in milliseconds; this only bounds a hang. """

MACOS_GIT_SHIM = Path("/usr/bin/git")
""" The macOS stub that offers to install the developer tools. A module global so tests can stand one in. """

NOT_INSTALLED = "Git is not installed."
MACOS_HINT = (
    " Install the Command Line Tools with `xcode-select --install`, then reload."
)

_VERSION = re.compile(r"git version (\d+)\.(\d+)")


@dataclass(frozen=True)
class GitCheck:
    """What the lab found when it looked for git."""

    ok: bool
    path: str | None = None
    """ The git that was found, resolved; None when there is none. """
    version: tuple[int, int] | None = None
    reason: str | None = None
    """ Why the lab is off, for the person looking at it. None when `ok`. """


def _env() -> dict[str, str]:
    # Parseable output in any locale, and never a prompt: a probe that waits on
    # a terminal nobody is watching would hang the request that asked.
    return {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}


def _is_macos_shim(path: str) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        return Path(path).resolve() == MACOS_GIT_SHIM.resolve()
    except OSError:
        return False


def _developer_tools_installed() -> bool:
    """`xcode-select -p` exits 0 only when the developer tools are installed."""
    xcode_select = shutil.which("xcode-select")
    if xcode_select is None:
        return False
    try:
        proc = subprocess.run(
            [xcode_select, "-p"],
            capture_output=True,
            timeout=CHECK_TIMEOUT,
            env=_env(),
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def check_git(binary: str = "git") -> GitCheck:
    """
    Look for a usable git, uncached. `binary` is a name on `PATH` or a path.

    Never raises: every failure is a `GitCheck` whose `reason` says what to do.
    """
    path = shutil.which(binary)
    if path is None:
        return GitCheck(ok=False, reason=NOT_INSTALLED)

    if _is_macos_shim(path) and not _developer_tools_installed():
        return GitCheck(ok=False, reason=NOT_INSTALLED + MACOS_HINT)

    try:
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CHECK_TIMEOUT,
            env=_env(),
        )
    except subprocess.TimeoutExpired:
        return GitCheck(ok=False, path=path, reason=f"Git at {path} did not answer.")
    except OSError as exc:
        return GitCheck(
            ok=False, path=path, reason=f"Git at {path} could not be run: {exc}."
        )

    out = (proc.stdout or "").strip()
    match = _VERSION.search(out)
    if proc.returncode != 0 or match is None:
        said = (proc.stderr or out or "no output").strip().splitlines()[0]
        return GitCheck(
            ok=False, path=path, reason=f"Git at {path} could not be run: {said}"
        )

    version = (int(match[1]), int(match[2]))
    if version < MIN_VERSION:
        need = ".".join(map(str, MIN_VERSION))
        return GitCheck(
            ok=False,
            path=path,
            version=version,
            reason=(
                f"Git {version[0]}.{version[1]} is too old; "
                f"the Hypothesis Lab needs {need} or later."
            ),
        )
    return GitCheck(ok=True, path=path, version=version)


_found: dict[str, GitCheck] = {}


def git_status(binary: str | None = None) -> GitCheck:
    """
    The lab's git, checked once it is found.

    Only a success is cached. A missing or too-old git is re-checked on every
    call — it costs a `PATH` lookup and, on macOS, one `xcode-select` — so
    someone who installs git while VISTA is running sees the lab turn on when
    they reload, rather than after a restart they had no reason to try.
    """
    if binary is None:
        from ..config import settings

        binary = settings.forum.git_binary
    if binary in _found:
        return _found[binary]
    result = check_git(binary)
    if result.ok:
        _found[binary] = result
        logger.info("Hypothesis Lab using git %s at %s", result.version, result.path)
    return result


def reset_git_status() -> None:
    """Forget a cached success. For tests."""
    _found.clear()
