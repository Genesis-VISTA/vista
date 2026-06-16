"""
Best-effort outbound email (SMTP).

Used by the campaign monitor to notify a user when a long-queued HPC job completes.
When SMTP is not configured (`settings.email.is_configured` is False), `send_email`
logs and returns False rather than raising — notification is never allowed to break
the monitor loop.
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from ..config import EmailSettings, settings


logger = logging.getLogger("vista.email")


def build_message(*, to: str, subject: str, body: str, from_addr: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _smtp_send(msg: EmailMessage, cfg: EmailSettings) -> None:
    """Synchronous SMTP delivery; run off the event loop via asyncio.to_thread."""
    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as smtp:
        if cfg.use_tls:
            smtp.starttls()
        if cfg.username and cfg.password:
            smtp.login(cfg.username, cfg.password.get_secret_value())
        smtp.send_message(msg)


async def send_email(*, to: str, subject: str, body: str) -> bool:
    """
    Send an email. Returns True if delivery was attempted successfully, False if
    email is unconfigured or delivery failed (both logged, never raised).
    """
    cfg = settings.email
    if not cfg.is_configured:
        logger.info("Email not configured; skipping notification to %s (%r)", to, subject)
        return False
    msg = build_message(to=to, subject=subject, body=body, from_addr=cfg.from_addr)
    try:
        await asyncio.to_thread(_smtp_send, msg, cfg)
        logger.info("Sent email to %s (%r)", to, subject)
        return True
    except Exception:  # noqa: BLE001 - notification is best-effort
        logger.warning("Failed to send email to %s", to, exc_info=True)
        return False
