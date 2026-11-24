"""The HTTP app refuses a request without the secret it shares with the backend."""

from pydantic import SecretStr
from starlette.testclient import TestClient

from vista_mcp_server import shared_secret
from vista_mcp_server.server import mcp


def _app(monkeypatch, secret):
    monkeypatch.setattr(
        shared_secret.settings, "secret", SecretStr(secret) if secret else None
    )
    return mcp.http_app(middleware=shared_secret.http_middleware())


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_refuses_a_request_without_the_secret(monkeypatch):
    with TestClient(_app(monkeypatch, "s3cret")) as client:
        assert client.post("/mcp", json=_INIT, headers=_HEADERS).status_code == 403


def test_refuses_a_wrong_secret(monkeypatch):
    with TestClient(_app(monkeypatch, "s3cret")) as client:
        headers = {**_HEADERS, "X-Vista-MCP-Secret": "nope"}
        assert client.post("/mcp", json=_INIT, headers=headers).status_code == 403


def test_accepts_the_secret(monkeypatch):
    with TestClient(_app(monkeypatch, "s3cret")) as client:
        headers = {**_HEADERS, "X-Vista-MCP-Secret": "s3cret"}
        assert client.post("/mcp", json=_INIT, headers=headers).status_code == 200


def test_without_a_secret_configured_everything_is_accepted(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        app = _app(monkeypatch, None)
    assert "VISTA_MCP_SECRET is not set" in caplog.text
    with TestClient(app) as client:
        assert client.post("/mcp", json=_INIT, headers=_HEADERS).status_code == 200
