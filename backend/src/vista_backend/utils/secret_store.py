"""
Secrets kept out of the database: Slack's tokens and the database's own key.

Where they live is a backend, chosen by `settings.secrets.backend`:

- `os`: the operating system's secret store through `keyring` (macOS
  Keychain, Windows Credential Manager, the Secret Service on Linux). What a
  desktop install uses. Each entry is named for VISTA's data directory, so a
  development checkout, a packaged install and a test run never read each
  other's secrets. There is deliberately no plaintext fallback: where no real
  store exists (a headless Linux box without a Secret Service, say),
  `available()` says why and the feature that needed it stays off.
- `directory`: one file per secret in a directory only this process's user
  can read, for a hosted VISTA (a container on a facility service), where
  there is no OS store and the platform protects the volume. An operator may
  provide a secret by mounting a file of that name. The directory must not be
  readable by anyone else, or the backend refuses, as `os` refuses a
  plaintext keyring.

`auto`, the default, is `os` on a desktop and `directory` when
`settings.deployment` is `hosted`.
"""

import hashlib
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Protocol

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

from ..config import settings

log = logging.getLogger(__name__)

SERVICE = "VISTA"

SLACK_BOT_TOKEN = "slack-bot-token"
SLACK_APP_TOKEN = "slack-app-token"
DB_ENCRYPTION_KEY = "db-encryption-key"

NAMES = (SLACK_BOT_TOKEN, SLACK_APP_TOKEN, DB_ENCRYPTION_KEY)
""" Every secret VISTA keeps; a directory backend names its files after these. """


class SecretStoreUnavailable(RuntimeError):
    """There is no usable secret store here."""


class Backend(Protocol):
    def problem(self) -> str | None:
        """Why secrets cannot be kept here, or None when they can."""
        ...

    def get(self, name: str) -> str | None: ...

    def put(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


# --- the OS secret store ----------------------------------------------------


def _account(name: str) -> str:
    digest = hashlib.sha256(
        str(settings.data_dir.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    return f"{name}@{digest}"


def _keyring_problem(backend: KeyringBackend) -> str | None:
    module = type(backend).__module__
    if module.startswith(("keyring.backends.fail", "keyring.backends.null")):
        return "no OS secret store was found (keyring has no usable backend)"
    if module.startswith("keyring.backends.chainer") and not getattr(
        backend, "backends", None
    ):
        return "no OS secret store was found (keyring has no usable backend)"
    if module.startswith(("keyrings.alt", "keyring.backends.file")):
        return "keyring is configured to store secrets in a plain file"
    return None


class OsBackend:
    def problem(self) -> str | None:
        return _keyring_problem(keyring.get_keyring())

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(SERVICE, _account(name))
        except keyring.errors.KeyringError as exc:
            log.warning("Could not read %s from the OS secret store: %s", name, exc)
            return None

    def put(self, name: str, value: str) -> None:
        try:
            keyring.set_password(SERVICE, _account(name), value)
        except keyring.errors.KeyringError as exc:
            raise SecretStoreUnavailable(str(exc)) from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE, _account(name))
        except keyring.errors.PasswordDeleteError:
            pass


# --- a directory of files ---------------------------------------------------


class DirectoryBackend:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, name: str) -> Path:
        if name not in NAMES:
            raise ValueError(f"Unknown secret {name!r}")
        return self.directory / name

    def problem(self) -> str | None:
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = self.directory.stat().st_mode
        except OSError as exc:
            return f"the secrets directory {self.directory} cannot be used: {exc}"
        if not os.access(self.directory, os.R_OK | os.X_OK):
            return f"the secrets directory {self.directory} cannot be read"
        # POSIX only: Windows has no mode bits worth checking, and a hosted
        # VISTA is a Linux container.
        if sys.platform != "win32" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            return (
                f"the secrets directory {self.directory} is open to other users "
                "(it must be mode 0700)"
            )
        return None

    def get(self, name: str) -> str | None:
        try:
            return self._path(name).read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("Could not read %s from %s: %s", name, self.directory, exc)
            return None

    def put(self, name: str, value: str) -> None:
        path = self._path(name)
        temporary = path.with_name(f".{name}.tmp")
        try:
            # Created 0600 from the start, then moved into place, so the
            # value is never readable by others nor half-written.
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SecretStoreUnavailable(
                f"could not write {name} to {self.directory}: {exc}"
            ) from exc

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink(missing_ok=True)
        except OSError as exc:
            raise SecretStoreUnavailable(
                f"could not remove {name} from {self.directory}: {exc}"
            ) from exc


# --- the store in effect ----------------------------------------------------


def backend() -> Backend:
    """The backend `settings.secrets` selects."""
    choice = settings.secrets.backend
    if choice == "auto":
        choice = "directory" if settings.deployment == "hosted" else "os"
    if choice == "directory":
        return DirectoryBackend(
            settings.secrets.directory or settings.data_dir / "secrets"
        )
    return OsBackend()


def available() -> tuple[bool, str | None]:
    """Whether secrets can be stored here, and if not, why."""
    problem = backend().problem()
    return problem is None, problem


def get(name: str) -> str | None:
    """The secret, or None when it is not set or there is no store."""
    store = backend()
    if store.problem() is not None:
        return None
    return store.get(name)


def put(name: str, value: str) -> None:
    """Store a secret. Raises `SecretStoreUnavailable` when there is no store."""
    store = backend()
    problem = store.problem()
    if problem is not None:
        raise SecretStoreUnavailable(problem)
    store.put(name, value)


def delete(name: str) -> None:
    """Remove a secret; nothing happens when it is not set or there is no store."""
    store = backend()
    if store.problem() is not None:
        return
    store.delete(name)


def is_set(name: str) -> bool:
    return bool(get(name))
