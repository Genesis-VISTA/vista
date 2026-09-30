"""
Conversation reports: drafting one from a chat and saving it to the project.

A saved report is an ordinary upload at `uploads/reports/<slug>.md`, so it shows
up on the Datasets page and the agent can read it at `/mnt/data/uploads/reports/`.
Its YAML header records which chat it came from; that is how saving again from
the same chat finds and replaces it instead of adding a copy.
"""

import re
import uuid
from datetime import date
from pathlib import Path

import yaml
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.report_authoring import ConversationReport, generate_report_draft
from ..config import settings
from ..utils.misc import path_is_under, write_file_unique
from . import project as project_service
from ._helpers import ServiceUser
from .files import _require_user_id
from .project_agent import get_project_agent_key, project_agent_pool


REPORTS_DIR = "reports"
""" Subdirectory of the user's project uploads that holds saved reports. """


class ReportSave(BaseModel):
    """A report as edited in the UI, ready to be written to the project."""

    title: str
    summary: str
    slug: str
    body: str
    chat_session_id: uuid.UUID


async def generate_draft(
    session: AsyncSession,
    project_name: str,
    message_history: list[ModelMessage],
    hint: str | None,
    user: ServiceUser,
) -> ConversationReport:
    """Draft a report from a chat in a project the user can access. Persists nothing."""
    await project_service.get_project_by_name(session, project_name, user)
    return await generate_report_draft(
        message_history, hint, None if user == "system" else user
    )


async def save_report(
    session: AsyncSession, project_name: str, report: ReportSave, user: ServiceUser
) -> str:
    """
    Write `report` into the user's project uploads and return its path relative
    to the uploads directory (e.g. `reports/eutectic-sweep.md`).

    If a report from the same chat is already saved, it is overwritten in place
    under its existing filename; otherwise a new file is created, suffixed if
    another chat's report already has the name.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(
        session, project_id=project.id, user_id=_require_user_id(user)
    )
    contents = _render(report).encode("utf-8")
    if len(contents) > settings.max_upload_size:
        raise HTTPException(
            status_code=400,
            detail=f"Report exceeds the {settings.max_upload_size.human_readable()} upload limit.",
        )
    async with project_agent_pool.get(agent_key) as agent:
        uploads_dir: Path = agent.uploads_dir
        reports_dir = uploads_dir / REPORTS_DIR
        reports_dir.mkdir(parents=True, exist_ok=True)

        existing = _find_report(reports_dir, report.chat_session_id)
        if existing is not None:
            existing.write_bytes(contents)
            path = existing
        else:
            path = write_file_unique(
                reports_dir / f"{_slugify(report.slug)}.md", contents
            )
        return path.relative_to(uploads_dir).as_posix()


def _slugify(slug: str) -> str:
    """Reduce a suggested slug to a flat, safe filename stem."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return cleaned or "report"


def _one_line(text: str) -> str:
    """Collapse whitespace, so no header value can span lines or fake a `---`."""
    return " ".join(text.split())


def _render(report: ReportSave) -> str:
    header = yaml.safe_dump(
        {
            "title": _one_line(report.title),
            "summary": _one_line(report.summary),
            "date": date.today().isoformat(),
            "chat_session_id": str(report.chat_session_id),
        },
        sort_keys=False,
        allow_unicode=True,
    )
    return f"---\n{header}---\n\n{report.body.strip()}\n"


def _read_header(path: Path) -> dict | None:
    """The YAML header of a saved report, or None if it has no readable one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        header = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return header if isinstance(header, dict) else None


def _find_report(reports_dir: Path, chat_session_id: uuid.UUID) -> Path | None:
    """The saved report whose header names `chat_session_id`, if any."""
    for path in sorted(reports_dir.glob("*.md")):
        if not path.is_file() or not path_is_under(reports_dir, path):
            continue
        header = _read_header(path)
        if header and str(header.get("chat_session_id")) == str(chat_session_id):
            return path
    return None
