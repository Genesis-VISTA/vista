"""
LLM-driven drafting of new SKILL.md content from a chat conversation.

The user clicks "Save as skill" in the chat UI; the frontend posts the chat's
PydanticAI `ModelMessage` history here, and we ask the model to summarise the
multi-step workflow as a reusable SKILL.md body plus suggested name and
description. The result is returned uncommitted so the user can edit it before
the actual `POST /skills` write.
"""

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from .inference import build_model_for
from ..db.schemas import UserPublicWithConfig

from ..config import settings


class SkillDraft(BaseModel):
    """A draft skill produced by the LLM from a conversation."""

    name_suggestion: str
    """ Kebab-case slug suitable for the skill directory and frontmatter name. """
    description_suggestion: str
    """ One- to three-sentence description of what the skill does and when to use it. """
    body: str
    """ The markdown body of the SKILL.md file (no YAML frontmatter). """


_SYSTEM_PROMPT = """\
You distill the preceding conversation into a reusable AgentSkills SKILL.md body
that another agent can follow to reproduce the same workflow.

Treat the conversation as a worked example of a multi-step workflow. Identify
the user's underlying goal, the sequence of steps taken (tool calls, inputs,
intermediate results), and any decisions or branches.

Output strictly:
- name_suggestion: a kebab-case slug (lowercase, dashes only, 2-5 words).
- description_suggestion: 1-3 sentences saying what the skill does and when an
  agent should invoke it. Lead with a verb. Do not include the phrase
  "use this skill".
- body: Markdown for the SKILL.md body only. Do NOT include YAML frontmatter
  (the --- delimited block). Use ## headings for sections. A typical layout is:
  brief overview, prerequisites, step-by-step instructions, expected outputs.
  Reference any concrete tool names, file paths, or commands that appeared in
  the conversation. Generalise specific user-supplied values (compositions,
  temperatures, etc.) into parameters where it makes sense.
"""


async def generate_skill_draft(
    message_history: list[ModelMessage],
    hint: str | None = None,
    user: UserPublicWithConfig | None = None,
) -> SkillDraft:
    """
    Draft a SKILL.md from a chat conversation.

    Args:
        message_history: PydanticAI `ModelMessage`s from the source chat. Passed
            through to the drafting agent so it sees the full transcript.
        hint: Optional user-supplied focus (e.g. "focus on the phase-diagram
            step"). Becomes part of the user prompt.
        user: The signed-in user, whose settings row supplies the model,
            endpoint, and credential. Without it this falls back to `Settings`,
            which on an install configured only through the UI has no key.

    Returns:
        A `SkillDraft` the caller can edit before persisting via `POST /skills`.
    """
    agent = Agent(
        model=build_model_for(user),
        system_prompt=_SYSTEM_PROMPT,
        output_type=SkillDraft,
    )
    user_prompt = "Draft a SKILL.md from the conversation above." + (
        f"\n\nUser hint: {hint.strip()}" if hint and hint.strip() else ""
    )
    result = await agent.run(user_prompt, message_history=message_history)
    return result.output
