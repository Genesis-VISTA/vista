"""Tests for the best-effort email service."""
import pytest

from vista_backend.config import settings
from vista_backend.services import email as email_service


def test_build_message_sets_headers_and_body():
    msg = email_service.build_message(
        to="user@ornl.gov", subject="hi", body="line1\nline2", from_addr="vista@localhost"
    )
    assert msg["To"] == "user@ornl.gov"
    assert msg["From"] == "vista@localhost"
    assert msg["Subject"] == "hi"
    assert "line1" in msg.get_content()


@pytest.mark.anyio
async def test_send_email_noop_when_unconfigured(monkeypatch):
    # Default config has email disabled.
    monkeypatch.setattr(settings.email, "enabled", False)
    called = False

    def _boom(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(email_service, "_smtp_send", _boom)
    sent = await email_service.send_email(to="user@ornl.gov", subject="s", body="b")
    assert sent is False
    assert called is False


@pytest.mark.anyio
async def test_send_email_delivers_when_configured(monkeypatch):
    monkeypatch.setattr(settings.email, "enabled", True)
    monkeypatch.setattr(settings.email, "host", "smtp.example.com")
    sent_messages = []

    def _capture(msg, cfg):
        sent_messages.append((msg["To"], msg["Subject"]))

    monkeypatch.setattr(email_service, "_smtp_send", _capture)
    sent = await email_service.send_email(to="user@ornl.gov", subject="done", body="body")
    assert sent is True
    assert sent_messages == [("user@ornl.gov", "done")]


@pytest.mark.anyio
async def test_send_email_swallows_transport_errors(monkeypatch):
    monkeypatch.setattr(settings.email, "enabled", True)
    monkeypatch.setattr(settings.email, "host", "smtp.example.com")

    def _raise(msg, cfg):
        raise OSError("connection refused")

    monkeypatch.setattr(email_service, "_smtp_send", _raise)
    sent = await email_service.send_email(to="user@ornl.gov", subject="s", body="b")
    assert sent is False  # error logged, not raised
