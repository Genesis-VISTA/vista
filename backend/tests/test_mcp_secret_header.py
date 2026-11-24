"""The backend's MCP client carries the shared secret the MCP server requires."""

from pydantic import SecretStr

from vista_backend.agents.agents import get_vista_mcp_server
from vista_backend.config import settings


def test_the_client_sends_the_secret(monkeypatch):
    monkeypatch.setattr(settings, "mcp_secret", SecretStr("s3cret"))
    assert get_vista_mcp_server().headers == {"X-Vista-MCP-Secret": "s3cret"}


def test_no_secret_configured_sends_no_header(monkeypatch):
    monkeypatch.setattr(settings, "mcp_secret", None)
    assert not get_vista_mcp_server().headers
