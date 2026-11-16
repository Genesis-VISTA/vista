"""
A stand-in for the dev MCP server's hidden task tools, `wait_task` and
`signal_task` (openspec change background-tasks), as the background-task
watcher calls them through `ProjectAgent.call_sandbox_tool`.

A test starts a task here, then makes it exit, makes the server die, or lets
the watcher signal it, and reads back which calls the watcher made.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Task:
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    exit_status: int | None = None
    lost: bool = False


class FakeSandbox:
    def __init__(self) -> None:
        self.tasks: dict[str, _Task] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self, task_id: str) -> None:
        self.tasks[task_id] = _Task()

    def exit(self, task_id: str, status: int) -> None:
        """The command exits by itself with `status`."""
        task = self.tasks[task_id]
        task.exit_status = status
        task.ended.set()

    def lose(self, task_id: str) -> None:
        """The dev MCP server dies with the call held."""
        task = self.tasks[task_id]
        task.lost = True
        task.ended.set()

    def names_called(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args))
        task = self.tasks.get(args["id"])
        if name == "wait_task":
            if task is None:
                return {"state": "unknown"}
            try:
                await asyncio.wait_for(task.ended.wait(), args["max_wait"])
            except TimeoutError:
                return {"state": "running"}
            if task.lost:
                raise ConnectionError("the dev MCP server went away")
            if task.exit_status is None:
                return {"state": "gone"}
            return {"state": "exited", "exit_status": task.exit_status}
        if name == "signal_task":
            if task is None:
                return {"state": "unknown"}
            task.ended.set()  # its group is gone, with no `.exit`
            return {"state": "ended"}
        raise KeyError(name)
