import sys
import getpass
import asyncssh
from collections import OrderedDict
from fastmcp import Context
from pydantic import Field, create_model


class TTYSSHClient(asyncssh.SSHClient):
    """SSHClient that prompts via /dev/tty, bypassing the stdin pipe."""

    def kbdint_auth_requested(self) -> str:
        return ""

    def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: list[tuple[str, bool]],
    ) -> list[str] | None:
        try:
            tty = open("/dev/tty", "r")
        except OSError:
            return None
        try:
            if instructions:
                print(instructions, file=sys.stderr)
            responses = []
            for prompt, echo in prompts:
                if echo:
                    print(prompt, file=sys.stderr, end="", flush=True)
                    responses.append(tty.readline().rstrip("\n"))
                else:
                    responses.append(getpass.getpass(prompt, stream=sys.stderr))
            return responses
        finally:
            tty.close()


class MCPElicitationSSHClient(asyncssh.SSHClient):
    """
    SSHClient that prompts via MCP elicitation.
    """

    def __init__(self, ctx: Context, login_message: str | None = None):
        super().__init__()
        self.ctx = ctx
        self.login_message = login_message or "Login:"

    def kbdint_auth_requested(self) -> str:
        return ""

    async def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: list[tuple[str, bool]],
    ) -> list[str] | None:
        if not prompts:
            return []

        fields = OrderedDict()
        for i, (prompt_text, echo) in enumerate(prompts):
            # This is kinda hacky, but the Frontend elicitation modal will hide inputs on password_*
            # fields. I can't pass `format: password` as MCP doesn't support it, and
            # json_schema_extra gets stripped off as well.
            fields[f"{'field' if echo else 'password'}_{i}"] = (str, Field(title=prompt_text))
        ChallengeResponse = create_model("ChallengeResponse", **fields)

        message = self.login_message
        if instructions:
            message = f"{message}\n{instructions}"

        result = await self.ctx.elicit(
            message=message,
            response_type=ChallengeResponse,
        )

        if result.action != "accept":
            return None

        return [getattr(result.data, f) for f in fields.keys()]
