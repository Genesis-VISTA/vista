import abc
import asyncio
from typing import Literal
from pathlib import Path


Volume = tuple[Path | str, Path | str, Literal['r', 'w']]

class Sandbox(abc.ABC):
    @classmethod
    @abc.abstractmethod
    async def spawn(
        cls,
        volumes: list[Volume] | None = None,
        env: dict[str, str] | None = None,
    ) -> Sandbox: ...

    @abc.abstractmethod
    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None, combine_streams: bool = False,
    ) -> asyncio.subprocess.Process:
        """
        Execute a command inside the sandbox.

        Args:
            command: Command to run
            args: args to the command
            env: environment variables
            cwd: workind directory
            combine_streams: Combine stdout and stderr streams (default False)
        """

    @abc.abstractmethod
    async def close(self) -> None: ...


class UnSandbox(Sandbox):
    """
    "Sandbox" implementation that executes commands on the host directly. Only intended for tests.
    """

    @classmethod
    async def spawn(cls, **kwargs) -> "UnSandbox":
        return UnSandbox()

    async def exec(
        self, command: str, args: list[str] | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None, combine_streams: bool = False,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            command, *(args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if combine_streams else asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )

    async def close(self) -> None:
        pass
