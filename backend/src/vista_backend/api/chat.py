import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import mcp.types
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import build_project_agent, get_mcp_server, run_project_agent_stream
from ..db.db import get_engine
from ..db.schemas import ProjectPublic, ProjectTable


router = APIRouter()

# TODO: These endpoints need significant refactoring, but right now I'm trying to keep it mostly compatible
# with the current front end.


class HistoryMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryMessage] = Field(default_factory=list)
    loadedSlugs: list[str] = Field(default_factory=list)


class ElicitationSubmit(BaseModel):
    id: str
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None



@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    stdout: str = ""
    stderr: str = ""
    ok: bool = True
    plotPath: str | None = None
    displayHtml: str | None = None


@dataclass
class ActiveRequest:
    """State accessible from anywhere in the request task via ContextVar."""

    sse_queue: asyncio.Queue[dict[str, Any] | None]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    closed: bool = False

    def emit(self, event: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.sse_queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover — unbounded queue
            pass


_active_request: ContextVar[ActiveRequest | None] = ContextVar(
    "vista_active_request", default=None
)


_SSE_LOGGER_PREFIXES = ("vista",)


class SseLogHandler(logging.Handler):
    """Forward records from `vista.*` loggers to the active SSE stream."""

    def emit(self, record: logging.LogRecord) -> None:
        if not any(record.name.startswith(p) for p in _SSE_LOGGER_PREFIXES):
            return
        active = _active_request.get()
        if active is None:
            return
        area = record.name[len("vista.") :] if record.name.startswith("vista.") else record.name
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover
            message = record.msg if isinstance(record.msg, str) else repr(record.msg)
        payload: dict[str, Any] = {
            "type": "log",
            "level": record.levelname,
            "area": area or "Agent",
            "message": message,
        }
        extra = getattr(record, "extra_data", None)
        if isinstance(extra, dict):
            payload["extra"] = extra
        active.emit(payload)


def _logger(area: str) -> logging.Logger:
    return logging.getLogger(f"vista.{area}")


def _log(area: str, level: int, message: str, **extra: Any) -> None:
    rec = _logger(area)
    if extra:
        rec.log(level, message, extra={"extra_data": extra})
    else:
        rec.log(level, message)


# ---------------------------------------------------------------------
# Elicitation registry — bridges MCP elicitation to /chat/elicitation POST
# ---------------------------------------------------------------------

ELICITATION_TIMEOUT_S = 5 * 60


@dataclass
class ElicitationEntry:
    future: asyncio.Future[mcp.types.ElicitResult]
    timer: asyncio.TimerHandle


def make_elicitation_callback(app) -> Callable[[mcp.types.ElicitRequestParams], Any]:
    """Build the callback PydanticAI's MCP toolset hands MCP elicitation requests to."""

    async def callback(
        params: mcp.types.ElicitRequestParams,
    ) -> mcp.types.ElicitResult | mcp.types.ErrorData:
        active = _active_request.get()
        if active is None or active.closed:
            return mcp.types.ElicitResult(action="cancel")

        if not isinstance(params, mcp.types.ElicitRequestFormParams):
            return mcp.types.ElicitResult(action="decline")

        bridge_id = f"elicit-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[mcp.types.ElicitResult] = loop.create_future()

        def expire() -> None:
            entry = app.state.elicitations.pop(bridge_id, None)
            if entry is not None and not entry.future.done():
                entry.future.set_result(mcp.types.ElicitResult(action="cancel"))

        timer = loop.call_later(ELICITATION_TIMEOUT_S, expire)
        app.state.elicitations[bridge_id] = ElicitationEntry(future=future, timer=timer)

        active.emit(
            {
                "type": "elicitation",
                "id": bridge_id,
                "message": params.message,
                "schema": params.requestedSchema,
            }
        )

        _log("Elicitation", logging.INFO, f"Awaiting browser response for {bridge_id}")
        try:
            result = await future
        finally:
            entry = app.state.elicitations.pop(bridge_id, None)
            if entry is not None:
                entry.timer.cancel()
        return result

    return callback


# ---------------------------------------------------------------------
# process_tool_call — tap MCP tool results for SSE side-channel events
# ---------------------------------------------------------------------


_PLOT_PATH_RE = re.compile(r"Plot saved to\s+(/\S+\.(?:png|jpe?g|svg|gif|webp))")


def _stringify_tool_result(result: Any) -> tuple[str, str | None]:
    """Return (text, html) extracted from a process_tool_call return value."""
    if result is None:
        return "", None
    if isinstance(result, str):
        text = result
    elif isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(json.dumps(item))
            else:
                parts.append(str(item))
        text = "\n".join(p for p in parts if p)
    elif isinstance(result, dict):
        text = json.dumps(result)
    else:
        text = str(result)
    html = text if "<img" in text else None
    return text, html


async def _process_tool_call(
    ctx,
    call_tool,
    name: str,
    tool_args: dict[str, Any],
) -> Any:
    """MCP `process_tool_call` hook.

    Forwards to the real tool, captures the result for the agent_response
    summary, and emits an `agent_turn { toolCall }` SSE event whenever the
    call produced display HTML so the UI can refresh the output panel
    mid-loop.
    """
    active = _active_request.get()
    started = time.monotonic()
    record = ToolCallRecord(tool=name, args=dict(tool_args))
    try:
        result = await call_tool(name, tool_args, None)
    except Exception as exc:
        record.ok = False
        record.stderr = f"{type(exc).__name__}: {exc}"
        if active is not None:
            active.tool_calls.append(record)
        _log(
            f"Tool:{name}",
            logging.ERROR,
            f"Failed after {int((time.monotonic() - started) * 1000)}ms: {record.stderr}",
        )
        raise

    text, html = _stringify_tool_result(result)
    record.stdout = text
    if name == "display_file" and html:
        record.displayHtml = html
    elif text:
        match = _PLOT_PATH_RE.search(text)
        if match:
            record.plotPath = match.group(1)

    elapsed = int((time.monotonic() - started) * 1000)
    _log(
        f"Tool:{name}",
        logging.INFO,
        f"Completed in {elapsed}ms",
        ok=record.ok,
        stdoutLength=len(record.stdout),
        stdoutPreview=record.stdout[:300],
    )

    if active is not None:
        active.tool_calls.append(record)
        if record.displayHtml or record.plotPath:
            active.emit(
                {
                    "type": "agent_turn",
                    "toolCall": {
                        "tool": record.tool,
                        "args": record.args,
                        "stdout": record.stdout,
                        "stderr": record.stderr,
                        "ok": record.ok,
                        "plotPath": record.plotPath,
                        "displayHtml": record.displayHtml,
                    },
                }
            )

    return result


# ---------------------------------------------------------------------
# Project selection — pick the project whose agent should run this turn
# ---------------------------------------------------------------------


_DEFAULT_PROJECT_NAME = "molten-salt"


async def _select_project(loaded_slugs: set[str]) -> ProjectPublic:
    """Pick a Project whose `skills` overlap with the loaded slug set.

    Falls back to a default project (`molten-salt`) when nothing matches.
    """
    async with AsyncSession(get_engine()) as session:
        all_projects = list((await session.exec(select(ProjectTable))).all())

    if not all_projects:
        raise HTTPException(
            status_code=500, detail="No projects configured; database seed missing."
        )

    if loaded_slugs:
        for project in all_projects:
            if loaded_slugs & set(project.skills or []):
                return ProjectPublic.model_validate(project)

    for project in all_projects:
        if project.name == _DEFAULT_PROJECT_NAME:
            return ProjectPublic.model_validate(project)

    return ProjectPublic.model_validate(all_projects[0])



def _build_message_history(history: list[HistoryMessage]):
    """Translate the wire-format chat history into PydanticAI messages."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    messages = []
    for msg in history:
        if msg.role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
        elif msg.role == "assistant":
            messages.append(
                ModelResponse(parts=[TextPart(content=msg.content)])
            )
    return messages


async def _run_agent(
    request: Request, chat_req: ChatRequest, active: ActiveRequest
) -> tuple[str, list[ToolCallRecord]]:
    loaded_slugs = {s for s in chat_req.loadedSlugs if isinstance(s, str) and s}
    project = await _select_project(loaded_slugs)
    history = _build_message_history(chat_req.history)

    _log(
        "Agent",
        logging.INFO,
        "New request",
        project=project.name,
        userMessage=chat_req.message[:200],
        historyTurns=len(history),
        slugs=sorted(loaded_slugs),
    )

    agent = build_project_agent(
        project,
        elicitation_callback=request.app.state.elicitation_callback,
        process_tool_call=_process_tool_call,
    )

    final_text = ""
    async with run_project_agent_stream(
        project,
        agent,
        chat_req.message,
        history,
    ) as stream:
        async for delta in stream.stream_text(delta=True):
            if delta:
                final_text += delta

    _log(
        "Agent",
        logging.INFO,
        f"Completed with {len(active.tool_calls)} tool call(s)",
    )
    return final_text, list(active.tool_calls)



def _sse_encode(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, default=str)}\n\n".encode("utf-8")


@router.post("/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    chat_req = ChatRequest.model_validate(body)
    if not chat_req.message.strip():
        raise HTTPException(status_code=400, detail="Message is required.")

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    active = ActiveRequest(sse_queue=queue)

    async def driver() -> None:
        token = _active_request.set(active)
        started = time.monotonic()
        try:
            response, tool_calls = await _run_agent(request, chat_req, active)
            elapsed = int((time.monotonic() - started) * 1000)
            _log(
                "POST",
                logging.INFO,
                f"Request completed in {elapsed}ms",
                toolCallCount=len(tool_calls),
                responseLength=len(response),
            )
            active.emit(
                {
                    "type": "agent_response",
                    "response": response,
                    "toolCalls": [
                        {
                            "tool": t.tool,
                            "args": t.args,
                            "stdout": t.stdout,
                            "stderr": t.stderr,
                            "ok": t.ok,
                            "plotPath": t.plotPath,
                            "displayHtml": t.displayHtml,
                        }
                        for t in tool_calls
                    ],
                }
            )
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            _log("POST", logging.ERROR, f"Request failed: {err}")
            active.emit({"type": "error", "error": err})
        finally:
            active.emit({"type": "done"})
            await queue.put(None)
            _active_request.reset(token)

    driver_task = asyncio.create_task(driver())

    async def stream() -> AsyncIterator[bytes]:
        try:
            while True:
                if await request.is_disconnected():
                    active.closed = True
                    driver_task.cancel()
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield _sse_encode(event)
        finally:
            active.closed = True
            with contextlib.suppress(asyncio.CancelledError):
                await driver_task

    headers = {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "connection": "keep-alive",
        "x-accel-buffering": "no",
    }
    return StreamingResponse(stream(), headers=headers, media_type="text/event-stream")


@router.post("/chat/elicitation")
async def chat_elicitation(submit: ElicitationSubmit, request: Request) -> dict[str, bool]:
    entry = request.app.state.elicitations.pop(submit.id, None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown or expired elicitation")
    entry.timer.cancel()
    if not entry.future.done():
        entry.future.set_result(
            mcp.types.ElicitResult(action=submit.action, content=submit.content)
        )
    return {"ok": True}


@router.get("/mcp/tools")
async def mcp_tools() -> list[mcp.types.Tool]:
    async with get_mcp_server() as server:
        return await server.list_tools()


class McpCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@router.post("/mcp/call")
async def mcp_call(req: McpCallRequest) -> mcp.types.CallToolResult:
    server = get_mcp_server()
    async with server:
        try:
            return await server._client.call_tool(req.name, req.arguments)
        except Exception as exc:
            return mcp.types.CallToolResult(
                content=[mcp.types.TextContent(type="text", text=f"MCP error: {exc}")],
                isError=True,
            )
