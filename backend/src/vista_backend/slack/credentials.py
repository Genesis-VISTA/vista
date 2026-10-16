"""
HPC credentials running out: reminders before, and a plain answer after.

Every 15 minutes, for each linked researcher, VISTA works out when each of
their credentials expires, where that is knowable:

- Odo and Frontier S3M tokens: S3M's `plannedExpiration`, from the HPC status
  checks the desktop app already runs.
- A NERSC IRI token: its own `exp`, when it is a JWT. Read, not verified; it
  is the researcher's own token and only the time is used.
- OLCF Globus: three days from the last login, the High Assurance session
  length, which refreshing does not extend. An estimate, and labelled one.

Lux has nothing stored to expire. A reminder goes by direct message once a
credential has 24 hours left, and again at 2 hours; each is recorded, so a
renewed credential gets reminders of its own. After expiry, a Slack request
that needs the credential is refused saying what is unavailable, and the
model is told which systems are out. Re-authenticating happens only in the
desktop app; Slack never asks for anything.
"""

import base64
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..db.schemas import (
    SlackLinkTable,
    SlackReminderTable,
    UserPublicWithConfig,
    UserTable,
)
from ..services import chat_run as chat_run_module
from . import store
from .store import now
from .transport import SlackTransport

log = logging.getLogger(__name__)

INTERVAL = 15 * 60.0
GLOBUS_SESSION = timedelta(days=3)
WINDOWS = (("2h", timedelta(hours=2)), ("24h", timedelta(hours=24)))
""" Narrowest first: a credential with an hour left is in the 2-hour window. """
TITLES = {"odo": "Odo", "frontier": "Frontier", "perlmutter": "Perlmutter"}
NEEDS_CREDENTIAL = {"submit_hpc_job", "cancel_hpc_job", "get_hpc_job_outputs"}


@dataclass(frozen=True)
class Expiry:
    cluster: str
    what: str
    """ "S3M token", "NERSC IRI token" or "Globus session". """
    expires_at: datetime
    estimated: bool = False

    def label(self) -> str:
        return f"{TITLES.get(self.cluster, self.cluster)} {self.what}"


ExpiriesFn = Callable[[UserPublicWithConfig], Awaitable[list[Expiry]]]


def jwt_expiry(token: str | None) -> datetime | None:
    """A JWT's `exp`, or None for anything that is not one."""
    if not token or token.count(".") != 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except ValueError, UnicodeDecodeError, AttributeError:
        return None
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, UTC)


def globus_expiries(user: Any) -> list[Expiry]:
    out = []
    for cluster in ("odo", "frontier"):
        connected = getattr(user, f"{cluster}_globus_connected_at", None)
        token = getattr(user, f"{cluster}_globus_token", None)
        if not connected or not token:
            continue
        try:
            at = datetime.fromisoformat(connected)
        except ValueError:
            continue
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        out.append(Expiry(cluster, "Globus session", at + GLOBUS_SESSION, True))
    return out


async def known_expiries(user: UserPublicWithConfig) -> list[Expiry]:
    """Every expiry VISTA can know for `user`; the live source."""
    from ..services.hpc_status import hpc_status_service

    out: list[Expiry] = []
    try:
        status = await hpc_status_service.status(user)
    except Exception:
        log.warning("Could not read HPC status for credential reminders")
    else:
        for cluster in status.clusters:
            expires = cluster.checks.credential.expires_at
            if expires is not None and cluster.cluster in ("odo", "frontier"):
                out.append(Expiry(cluster.cluster, "S3M token", expires))
    nersc = jwt_expiry(user.nersc_iri_token)
    if nersc is not None:
        out.append(Expiry("perlmutter", "NERSC IRI token", nersc))
    return out + globus_expiries(user)


def _when(moment: datetime) -> str:
    stamp = int(moment.timestamp())
    fallback = moment.strftime("%Y-%m-%d %H:%M UTC")
    return f"<!date^{stamp}^{{date_short_pretty}} at {{time}}|{fallback}>"


def reminder_text(kind: str, items: list[Expiry]) -> str:
    left = "about 2 hours" if kind == "2h" else "about 24 hours"
    lines = [f":hourglass: Your HPC access runs out in {left}:"]
    for e in items:
        estimated = " (estimated)" if e.estimated else ""
        lines.append(f"• {e.label()}: {_when(e.expires_at)}{estimated}")
    lines.append(
        "Renew it in the VISTA desktop app (Settings → HPC). "
        "I can't do it from Slack, and won't ask for credentials here."
    )
    return "\n".join(lines)


_known: dict[uuid.UUID, list[Expiry]] = {}
""" The last expiries worked out per researcher, for the gate and the prompt. """


def known(user_id: uuid.UUID) -> list[Expiry]:
    """The expiries last worked out for this researcher (for Home)."""
    return list(_known.get(user_id, []))


def expired_clusters(user_id: uuid.UUID) -> dict[str, list[str]]:
    """Cluster → what has expired, as of now."""
    out: dict[str, list[str]] = {}
    moment = now()
    for e in _known.get(user_id, []):
        if e.expires_at <= moment:
            out.setdefault(e.cluster, []).append(e.what)
    return out


def unavailable(cluster: str) -> str:
    title = TITLES.get(cluster, cluster)
    return (
        f"Submission, cancellation and outputs on {title} are unavailable: the "
        f"credential has expired. Re-authenticate in the VISTA desktop app "
        "(Settings → HPC)."
    )


def refusal(user_id: uuid.UUID, tool_name: str, args: dict[str, Any]) -> str | None:
    """Part of the gate's check: a request for a system whose access has gone."""
    if tool_name.split("__")[-1] not in NEEDS_CREDENTIAL:
        return None
    cluster = args.get("cluster")
    if isinstance(cluster, str) and cluster in expired_clusters(user_id):
        return unavailable(cluster)
    return None


def prompt_note(user_id: uuid.UUID) -> str | None:
    """For the Slack instructions: which systems are out, so the model says so."""
    gone = expired_clusters(user_id)
    if not gone:
        return None
    lines = [
        f"- {TITLES.get(c, c)}: {', '.join(whats)} expired" for c, whats in gone.items()
    ]
    return (
        "The researcher's access to these systems has expired, so submitting, "
        "cancelling and fetching outputs there will not work until they "
        "re-authenticate in the desktop app. Say so rather than trying:\n"
        + "\n".join(lines)
    )


class CredentialReminder:
    def __init__(
        self, slack: SlackTransport, *, expiries: ExpiriesFn = known_expiries
    ) -> None:
        self._slack = slack
        self._expiries = expiries

    async def run_once(self) -> int:
        """Remind everyone due a reminder. Returns how many messages went."""
        async with chat_run_module.session_factory() as db:
            links = (await db.exec(select(SlackLinkTable))).all()
            by_user: dict[uuid.UUID, tuple[str | None, str]] = {}
            for link in links:
                by_user.setdefault(
                    link.user_id, (link.dm_channel_id, link.slack_user_id)
                )
            users = {}
            for user_id in by_user:
                row = await db.get(UserTable, user_id)
                if row is not None:
                    users[user_id] = UserPublicWithConfig.model_validate(row)
        sent = 0
        for user_id, user in users.items():
            try:
                sent += await self._remind(user, *by_user[user_id])
            except Exception:
                log.exception("Credential reminder failed for a linked user")
        return sent

    async def _remind(
        self, user: UserPublicWithConfig, dm: str | None, slack_user_id: str
    ) -> int:
        expiries = await self._expiries(user)
        _known[user.id] = expiries
        moment = now()
        due: dict[str, list[Expiry]] = {}
        for e in expiries:
            left = e.expires_at - moment
            if left <= timedelta(0):
                continue
            kind = next((k for k, w in WINDOWS if left <= w), None)
            if kind is not None and await self._claim(user.id, e, kind):
                due.setdefault(kind, []).append(e)
        if not due:
            return 0
        channel = dm or await self._slack.open_dm(slack_user_id)
        for kind, items in due.items():
            await self._slack.post(channel, reminder_text(kind, items))
        return len(due)

    async def _claim(self, user_id: uuid.UUID, e: Expiry, kind: str) -> bool:
        """Record the reminder; False if it was already sent."""
        async with chat_run_module.session_factory() as db:
            db.add(
                SlackReminderTable(
                    user_id=user_id,
                    cluster=f"{e.cluster}:{e.what}",
                    kind=kind,
                    expires_at=e.expires_at.isoformat(timespec="seconds"),
                    sent_at=store.now_iso(),
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                return False
        return True
