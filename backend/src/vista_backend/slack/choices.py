"""
`offer_choices`: when a request is ambiguous in Slack, offer buttons.

The tool exists only on Slack turns (its `prepare` hides it elsewhere, where
the researcher can type a path as easily as tap one). It asks through the
run's prompt path as a form whose schema is one enum field, `choice`;
`Confirmations` shows that as up to five buttons and answers with the option
tapped. Only the requester's tap counts, and an unanswered offer expires like
a confirmation. Choosing changes nothing, so the tool is read-only.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.tools import ToolDefinition

from .gate import READ_ONLY

MAX_CHOICES = 5
FIELD = "choice"

Ask = Callable[[str, list[str]], Awaitable[str | None]]
""" (question, options) → the option tapped, or None if none was. """


def choice_schema(options: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {FIELD: {"type": "string", "enum": options}},
        "required": [FIELD],
    }


def options_of(schema: dict[str, Any] | None) -> list[str] | None:
    """The options if `schema` is a choice, else None (an ordinary form)."""
    props = (schema or {}).get("properties") or {}
    if set(props) != {FIELD}:
        return None
    enum = props[FIELD].get("enum")
    if not isinstance(enum, list) or not all(isinstance(o, str) for o in enum):
        return None
    return enum


def register_choice_tool(
    agent: Agent, *, channel: Callable[[], str], ask: Callable[[], Ask | None]
) -> None:
    async def only_in_slack(
        ctx: RunContext[Any], tool_def: ToolDefinition
    ) -> ToolDefinition | None:
        return tool_def if channel() == "slack" else None

    @agent.tool_plain(prepare=only_in_slack, metadata=READ_ONLY)
    async def offer_choices(question: str, options: list[str]) -> str:
        """
        Ask the researcher to pick one of a few options, shown as buttons. Use it
        when a request could mean several recorded jobs, job folders or scripts,
        instead of asking them to type a name or path. Give 2 to 5 short options
        (a label each); returns the option they picked.
        """
        cleaned = list(dict.fromkeys(o.strip() for o in options if o.strip()))
        if len(cleaned) < 2:
            return "Give at least two different options."
        if len(cleaned) > MAX_CHOICES:
            return (
                f"At most {MAX_CHOICES} options fit; narrow them down, or ask the "
                "researcher a question that does."
            )
        emit = ask()
        if emit is None:
            return "Buttons aren't available here; ask in words."
        chosen = await emit(question, cleaned)
        if chosen is None:
            return "The researcher didn't pick an option. Don't guess; ask again if needed."
        return f"The researcher picked: {chosen}"
