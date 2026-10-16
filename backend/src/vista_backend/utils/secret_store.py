"""
Secrets kept in the operating system's secret store, not in a file or the database.

macOS Keychain, Windows Credential Manager, or the Secret Service on Linux, through
`keyring`. Each entry is named for VISTA's data directory, so a development
checkout, a packaged install and a test run never read each other's secrets.

There is deliberately no plaintext fallback: where no real store exists (a
headless Linux box without a Secret Service, say), `available()` says why and
the feature that needed it stays off.
"""

import hashlib
import logging

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

from ..config import settings

log = logging.getLogger(__name__)

SERVICE = "VISTA"

SLACK_BOT_TOKEN = "slack-bot-token"
SLACK_APP_TOKEN = "slack-app-token"
DB_ENCRYPTION_KEY = "db-encryption-key"


class SecretStoreUnavailable(RuntimeError):
    """There is no usable OS secret store on this machine."""


def _account(name: str) -> str:
    digest = hashlib.sha256(
        str(settings.data_dir.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    return f"{name}@{digest}"


def _backend_problem(backend: KeyringBackend) -> str | None:
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


def available() -> tuple[bool, str | None]:
    """Whether secrets can be stored here, and if not, why."""
    problem = _backend_problem(keyring.get_keyring())
    return problem is None, problem


def _require() -> None:
    ok, problem = available()
    if not ok:
        raise SecretStoreUnavailable(problem)


def get(name: str) -> str | None:
    """The secret, or None when it is not set or there is no store."""
    if not available()[0]:
        return None
    try:
        return keyring.get_password(SERVICE, _account(name))
    except keyring.errors.KeyringError as exc:
        log.warning("Could not read %s from the OS secret store: %s", name, exc)
        return None


def put(name: str, value: str) -> None:
    """Store a secret. Raises `SecretStoreUnavailable` when there is no store."""
    _require()
    try:
        keyring.set_password(SERVICE, _account(name), value)
    except keyring.errors.KeyringError as exc:
        raise SecretStoreUnavailable(str(exc)) from exc


def delete(name: str) -> None:
    """Remove a secret; nothing happens when it is not set."""
    if not available()[0]:
        return
    try:
        keyring.delete_password(SERVICE, _account(name))
    except keyring.errors.PasswordDeleteError:
        pass


def is_set(name: str) -> bool:
    return bool(get(name))
