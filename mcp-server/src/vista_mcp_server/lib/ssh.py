import sys
import getpass
import asyncssh


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
