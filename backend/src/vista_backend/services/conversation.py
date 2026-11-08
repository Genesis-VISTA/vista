"""
Starting a turn in a conversation, for every channel the agent is reached through.

The in-app chat (`api/agent.py`) and Slack (`slack/`) both start turns here, so
they share one agent core: the same pooled `ProjectAgent`, the same history, the
same `ChatRun`. What differs by channel is decided inside the agent from the
run's `channel` (see `ProjectAgent.run_stream`), never by a second code path.
"""

import uuid

from pydantic_ai.messages import ModelMessage
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import Channel
from ..agents.inference import require_inference_credential
from ..db.schemas import LocalSettingsPublic, ProjectPublic
from . import chat_session as chat_session_service
from .chat_run import ChatRun, ChatRunRegistry, chat_run_registry
from .project_agent import ProjectAgentKey


async def start_turn(
    session: AsyncSession,
    *,
    local: LocalSettingsPublic,
    project: ProjectPublic,
    chat_session_id: uuid.UUID,
    user_prompt: str,
    channel: Channel = "app",
    fallback_history: list[ModelMessage] | None = None,
    registry: ChatRunRegistry | None = None,
) -> ChatRun:
    """
    Start a turn in `chat_session_id` and return its run.

    Commits `session` before the run starts: the run writes through its own
    session and must not wait on a lock this one holds.

    Raises:
        HTTPException(404): the conversation is not in `project`.
        MissingInferenceCredential: before anything is written.
        RunBusy: the conversation already has a turn running.
    """
    chat_row = await chat_session_service.get_chat_session(
        session, project_id=project.id, chat_session_id=chat_session_id
    )
    # The chat's own model counts, so it can send on a provider with no default.
    require_inference_credential(local, chat_row.inference_model)
    agent_key: ProjectAgentKey = (chat_row.id, project.id)
    history = await chat_session_service.get_effective_message_history(
        session,
        project_id=project.id,
        fallback_history=fallback_history or [],
        chat_session_id=chat_session_id,
    )
    await session.commit()
    return await (registry or chat_run_registry).start(
        chat_session_id=chat_session_id,
        project_id=project.id,
        agent_key=agent_key,
        user_prompt=user_prompt,
        prior_history=history,
        channel=channel,
    )
