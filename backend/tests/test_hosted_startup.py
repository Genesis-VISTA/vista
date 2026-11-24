"""A hosted backend starts only with real secrets, and says which setting is missing."""

import pytest
from pydantic import ValidationError

from vista_backend.config import DEV_ENCRYPTION_KEY, Settings

REAL_KEY = "Xb1sH8o3vV0kQ2m4Z9yJ7uTnE5rLwC6dA8fGhIjKlMo="


def _settings(monkeypatch, **env):
    for name in (
        "VISTA_BACKEND_DEPLOYMENT",
        "VISTA_BACKEND_ENCRYPTION_KEY",
        "VISTA_MCP_SECRET",
        "VISTA_ENV",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_hosted_without_a_key_refuses_and_names_the_setting(monkeypatch):
    with pytest.raises(ValidationError, match="VISTA_BACKEND_ENCRYPTION_KEY"):
        _settings(monkeypatch, VISTA_BACKEND_DEPLOYMENT="hosted", VISTA_MCP_SECRET="s")


def test_hosted_with_the_development_key_refuses(monkeypatch):
    with pytest.raises(ValidationError, match="VISTA_BACKEND_ENCRYPTION_KEY"):
        _settings(
            monkeypatch,
            VISTA_BACKEND_DEPLOYMENT="hosted",
            VISTA_BACKEND_ENCRYPTION_KEY=DEV_ENCRYPTION_KEY,
            VISTA_MCP_SECRET="s",
        )


def test_hosted_in_prod_without_a_key_refuses(monkeypatch):
    with pytest.raises(ValidationError, match="encryption_key|ENCRYPTION_KEY"):
        _settings(
            monkeypatch,
            VISTA_ENV="prod",
            VISTA_BACKEND_DEPLOYMENT="hosted",
            VISTA_MCP_SECRET="s",
        )


def test_hosted_without_an_mcp_secret_refuses(monkeypatch):
    with pytest.raises(ValidationError, match="VISTA_MCP_SECRET"):
        _settings(
            monkeypatch,
            VISTA_BACKEND_DEPLOYMENT="hosted",
            VISTA_BACKEND_ENCRYPTION_KEY=REAL_KEY,
        )


def test_hosted_with_both_starts(monkeypatch):
    s = _settings(
        monkeypatch,
        VISTA_BACKEND_DEPLOYMENT="hosted",
        VISTA_BACKEND_ENCRYPTION_KEY=REAL_KEY,
        VISTA_MCP_SECRET="s",
    )
    assert s.deployment == "hosted"


def test_desktop_needs_neither(monkeypatch):
    s = _settings(monkeypatch)
    assert s.deployment == "desktop"
    assert s.encryption_key.get_secret_value() == DEV_ENCRYPTION_KEY
    assert s.mcp_secret is None
