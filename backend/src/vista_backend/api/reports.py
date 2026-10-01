from fastapi import APIRouter
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from ..agents.report_authoring import ConversationReport
from ..db.db import SessionDep
from ..services import reports as reports_service
from ..services.auth import UserDep
from ..services.reports import ReportSave


router = APIRouter()


class ReportGenerateRequest(BaseModel):
    """
    Body for `POST /projects/{name}/reports/generate` — see `generate_report_draft`.
    `message_history` uses PydanticAI's `ModelMessage` schema, same as
    `/projects/.../agent/run`.
    """

    message_history: list[ModelMessage] = Field(default_factory=list)
    hint: str | None = None


class ReportSaved(BaseModel):
    """Where a saved report landed, relative to the project's uploads."""

    path: str


@router.post("/projects/{project_name}/reports/generate")
async def generate_report(
    project_name: str, body: ReportGenerateRequest, session: SessionDep, user: UserDep
) -> ConversationReport:
    """
    Draft a report from a chat conversation. Does NOT persist anything — the
    client edits the draft and then submits `POST /projects/{name}/reports`.
    """
    return await reports_service.generate_draft(
        session, project_name, body.message_history, body.hint, user
    )


@router.post("/projects/{project_name}/reports", status_code=201)
async def save_report(
    project_name: str, payload: ReportSave, session: SessionDep, user: UserDep
) -> ReportSaved:
    """
    Save a report to the user's project uploads under `reports/`, replacing the
    report previously saved from the same chat.
    """
    path = await reports_service.save_report(session, project_name, payload, user)
    return ReportSaved(path=path)
