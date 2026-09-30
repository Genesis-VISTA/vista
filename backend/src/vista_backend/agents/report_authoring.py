"""
LLM-driven drafting of a report from a chat conversation.

The user clicks "Generate report" in the chat UI; the frontend posts the chat's
PydanticAI `ModelMessage` history here, and we ask the model to write up what
the conversation did and found: a readable summary followed by a detailed record
of concrete values, paths, and job IDs. The result is returned uncommitted so
the user can edit it before saving it to the project or turning it into a skill.
"""

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from .inference import build_model_for
from ..db.schemas import UserPublicWithConfig


class ConversationReport(BaseModel):
    """A draft report produced by the LLM from a conversation."""

    title: str
    """ Short human-readable title for the report. """
    slug_suggestion: str
    """ Kebab-case slug suitable for the report's filename. """
    summary: str
    """ One sentence saying what was done and what was found. """
    body: str
    """ The markdown body of the report (no YAML frontmatter). """


_SYSTEM_PROMPT = """\
You write up the preceding conversation as a report. It is read by two
audiences: a researcher skimming what happened, and an agent in a later chat
that needs the exact details to continue the work.

Output strictly:
- title: a short descriptive title (under 80 characters).
- slug_suggestion: a kebab-case slug (lowercase, dashes only, 2-6 words).
- summary: one sentence saying what was done and what was found.
- body: Markdown only, with no YAML frontmatter (the --- delimited block) and
  no top-level # title. Structure it as:
  1. ## Summary - a few short paragraphs or bullets: the goal, the approach, the
     key results, and open questions or next steps. Readable on its own.
  2. ## Record - the detailed record: exact values and units, compositions,
     parameters and settings, tool calls that mattered, HPC job IDs and
     clusters, file paths, and what failed and why. Prefer bullets and tables.
  Reference output files by their sandbox path (e.g. /mnt/data/output/...);
  embed images as ![caption](/mnt/data/output/...). Do not invent paths,
  values, or results that do not appear in the conversation.
"""


async def generate_report_draft(
    message_history: list[ModelMessage],
    hint: str | None = None,
    user: UserPublicWithConfig | None = None,
) -> ConversationReport:
    """
    Draft a report from a chat conversation.

    Args:
        message_history: PydanticAI `ModelMessage`s from the source chat. Passed
            through to the drafting agent so it sees the full transcript.
        hint: Optional user-supplied focus (e.g. "focus on the density results").
            Becomes part of the user prompt.
        user: The signed-in user, whose settings row supplies the model,
            endpoint, and credential. Without it this falls back to `Settings`,
            which on an install configured only through the UI has no key.

    Returns:
        A `ConversationReport` the caller can edit before saving.
    """
    agent = Agent(
        model=build_model_for(user),
        system_prompt=_SYSTEM_PROMPT,
        output_type=ConversationReport,
    )
    user_prompt = "Write a report of the conversation above." + (
        f"\n\nUser hint: {hint.strip()}" if hint and hint.strip() else ""
    )
    result = await agent.run(user_prompt, message_history=message_history)
    return result.output
