"""
Logic to build the actual PydanticAI Agent
"""
import fnmatch, json, logging, os, shutil, uuid, asyncio
from typing import AsyncIterator, Callable, Literal, Annotated as A, Any
from pathlib import Path

from pydantic import BaseModel, Field, Discriminator
from pydantic_ai import Agent, RunContext, UsageLimits, RunUsage, AgentRunResultEvent
from pydantic_ai.mcp import MCPServer, MCPServerStdio, MCPServerStreamableHTTP, ProcessToolCallback, CallToolFunc, ToolResult
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import infer_model
import mcp.client.session
import mcp.shared.context
import mcp.types

from ..config import settings
from ..db.schemas import ProjectPublic, UserPublicWithConfig
from ..utils.streams import StreamMerger, StreamClosedError
from ..utils.misc import json_dump_if
from ..vistaguard import VistaGuardSidecar
from ..vistaguard.quarantine import (
    build_intent_extraction_agent,
    build_quarantine_agent,
)
from .skills import to_prompt


BASE_SYSTEM_PROMPT = (Path(__file__).parent / "base_system_prompt.md").read_text()


def _wrap_as_call_tool_result(raw: Any, *, is_error: bool) -> mcp.types.CallToolResult:
    """Build a ``mcp.types.CallToolResult`` from a pydantic-ai ``ToolResult``.

    ``MCPServer.direct_call_tool`` returns ``str | BinaryContent | dict | list
    | Sequence`` directly (PydanticAI unwraps the MCP envelope for us). The
    HTTP `/mcp/call` endpoint, on the other hand, exposes the original MCP
    ``CallToolResult`` envelope to clients. Wrap whatever we got back into
    the envelope shape so callers (the HTTP route + the agenthpc worker
    pool's `_parse_tool_result`) see a stable type.
    """
    content: list[Any] = []
    if isinstance(raw, str):
        content.append(mcp.types.TextContent(type="text", text=raw))
    elif isinstance(raw, (dict, list)):
        content.append(mcp.types.TextContent(type="text", text=json.dumps(raw)))
    elif isinstance(raw, mcp.types.TextContent | mcp.types.ImageContent | mcp.types.EmbeddedResource):
        content.append(raw)
    elif isinstance(raw, list | tuple):
        for item in raw:
            content.extend(_wrap_as_call_tool_result(item, is_error=is_error).content)
    else:
        # Fallback: stringify anything else (e.g. BinaryContent edge cases).
        content.append(mcp.types.TextContent(type="text", text=str(raw)))
    return mcp.types.CallToolResult(content=content, isError=is_error)


class LogEntry(BaseModel):
    """
    A log line emitted during an agent run.

    Covers both the backend agent's own logs and logs forwarded from the
    VISTA MCP Server.
    """

    level: str
    """ Log level, e.g. "INFO", "WARNING", "ERROR". """
    area: str
    """ Where the log came from, e.g. "Agent", "Tool:run_bash", "MCP Server". """
    message: str

class LogEvent(LogEntry):
    event_kind: Literal["log"] = "log"

class McpFormElicitationEvent(BaseModel):
    event_kind: Literal["mcp_form_elicitation"] = "mcp_form_elicitation"
    mode: Literal["form"] = "form"
    elicitation_id: str
    message: str
    requested_schema: mcp.types.ElicitRequestedSchema

class McpUrlElicitationEvent(BaseModel):
    event_kind: Literal["mcp_url_elicitation"] = "mcp_url_elicitation"
    mode: Literal["url"] = "url"
    elicitation_id: str
    message: str
    url: str

McpElicitationEvent = McpFormElicitationEvent | McpUrlElicitationEvent


class ProjectAgentResult(BaseModel):
    """
    Result of a single agent turn (`ProjectAgent.run`, or the terminal event
    of `ProjectAgent.run_stream`).

    `new_messages` is a list of PydanticAI `ModelMessage` objects produced during this
    run -- model requests, tool calls, tool returns, and the final text response. Append
    them to your stored `message_history` to continue the conversation on the next call.

    See https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents for the agent
    run model and https://pydantic.dev/docs/ai/core-concepts/messages/ for the message
    schema.
    """
    new_messages: list[ModelMessage]
    """ Messages produced during this run; append to `message_history` for the next call. """
    usage: RunUsage
    """ Token / request usage for this run -- see `pydantic_ai.RunUsage`. """
    logs: list[LogEntry] = Field(default_factory=list)
    """ All log lines emitted during this run (also streamed live as `LogEvent`s). """

class ProjectAgentResultEvent(BaseModel):
    """ Terminal event of `ProjectAgent.run_stream`, carrying the `ProjectAgentResult`. """
    event_kind: Literal["agent_run_result"] = "agent_run_result"
    result: ProjectAgentResult

ProjectAgentStreamEvent = A[AgentStreamEvent | LogEvent | McpElicitationEvent | ProjectAgentResultEvent, Discriminator("event_kind")]


def get_vista_mcp_server(
    elicitation_callback: mcp.client.session.ElicitationFnT | None = None,
    process_tool_call: ProcessToolCallback | None = None,
    log_handler: mcp.client.session.LoggingFnT | None = None,
) -> MCPServerStreamableHTTP:
    """ Connect to the VISTA MCP Server (HTTP) for HPC, RAG, and file display tools. """
    return MCPServerStreamableHTTP(
        url=settings.mcp_url,
        elicitation_callback=elicitation_callback,
        process_tool_call=process_tool_call,
        log_handler=log_handler,
        log_level="info" if log_handler else None,
        timeout=10,
        read_timeout=1800 + 60,
    )

def get_dev_mcp_server(
    volumes: list[tuple[str, str, Literal['r', 'w']]],
    elicitation_callback: mcp.client.session.ElicitationFnT | None = None,
    process_tool_call: ProcessToolCallback | None = None,
    log_handler: mcp.client.session.LoggingFnT | None = None,
) -> MCPServerStdio:
    """
    Launch an dev_mcp_server instance via STDIO for sandbox tools (run_bash, create_file, view).

    This is intended to be called once per `ProjectAgent` so that each (user, project) pair gets an
    isolated sandbox.
    """
    dev_server_dir = settings.mcp_servers_path / "dev_mcp_server"
    return MCPServerStdio(
        command="uv",
        args=["run", "dev-mcp-server", "--transport=stdio"],
        cwd=str(dev_server_dir),
        env={
            # TODO: This is fine for dev server, but when we allow custom servers we'll need to rethink how env vars are set
            # and how the MCP server itself is sandboxed.
            **os.environ,
            "VISTA_DEV_MCP_VOLUMES": json.dumps(volumes),
        },
        elicitation_callback=elicitation_callback,
        process_tool_call=process_tool_call,
        log_handler=log_handler,
        log_level="info" if log_handler else None,
        timeout=120,
        read_timeout=1800 + 60,
    )


class ProjectAgent:
    def __init__(self, project: ProjectPublic, user: UserPublicWithConfig):
        self.project = project
        self.user = user
        # Id per project agent. A new ProjectAgent with the same input Project x User will have the
        # same id
        self.id = f"{project.id}-{user.id}"
        self.volume_root = settings.data_dir / "volumes" / self.id
        self.output_dir = self.volume_root / "data" / "output"
        self.uploads_dir = self.volume_root / "data" / "uploads"
        self.skills_volume_dir = self.volume_root / "skills"
        self._elicitations: dict[str, asyncio.Future] = {}
        self._sidecar = VistaGuardSidecar(settings.vistaguard, project)

        # We need to pass constant callbacks to the MCP server, so that we can reuse the same PydanticAI MCPServer
        # instance between run_stream calls and not relaunch the MCP servers each call. However, we need to
        # customize the callbacks in each run_stream call to hook up the streaming output and such. We set the callbacks
        # to dispatch to these, which we will swap out in run_stream.
        # TODO: Note, this means we have to lock to prevent parallel run_stream calls. We should look for a way to remove
        # that restriction. Though, once we implement sessions, we can scope the agent to each session and then locking
        # would be more reasonable.
        self._run_lock = asyncio.Lock()
        self._cur_mcp_elicitation_callback: mcp.client.session.ElicitationFnT | None = None
        self._cur_mcp_process_tool_call: ProcessToolCallback | None = None
        self._cur_mcp_log_handler: mcp.client.session.LoggingFnT | None = None
        # Set during run_stream so backend-side tools (e.g. agenthpc_run_workers,
        # which runs N async sub-agents inside one tool call) can push log lines
        # into the same stream the chat agent sees. Signature: (level, area, msg).
        self._cur_log_emitter: Callable[[str, str, str], None] | None = None
        # Emits a synthesized TextPart so the chat UI's intermediate "agent
        # thinking" bubble renderer picks up the message. Used by long-running
        # backend tools (workers) to surface per-trial progress in the same
        # visual channel the model's own thinking text uses.
        self._cur_progress_emitter: Callable[[str], None] | None = None
        # Synthesizes a (FunctionToolCallEvent, FunctionToolResultEvent) pair
        # so the chat UI's tool-result handler fires for an MCP tool the user
        # never sees the agent call directly (e.g. workers showing the plot
        # mid-run). The content is whatever the MCP tool returned.
        self._cur_tool_event_emitter: Callable[[str, dict[str, Any], Any], None] | None = None
        self.agent = self._build_agent()

    def _build_agent(self) -> Agent:
        """
        Construct a PydanticAI Agent for a project.
        """
        async def elicitation_callback(
            context: mcp.shared.context.RequestContext, params: mcp.types.ElicitRequestParams,
        ) -> mcp.types.ElicitResult | mcp.types.ErrorData:
            cb = self._cur_mcp_elicitation_callback
            if cb:
                return await cb(context, params)
            else:
                return mcp.types.ElicitResult(action="cancel")

        async def process_tool_call(ctx: RunContext[Any], call_tool: CallToolFunc, name: str, tool_args: dict[str, Any]):
            cb = self._cur_mcp_process_tool_call
            if cb:
                return await cb(ctx, call_tool, name, tool_args)
            else:
                return await call_tool(name, tool_args, None)

        async def log_handler(params: mcp.types.LoggingMessageNotificationParams) -> None:
            cb = self._cur_mcp_log_handler
            if cb:
                return await cb(params)

        self._mcp_servers: list[MCPServer] = [
            get_vista_mcp_server(
                elicitation_callback=elicitation_callback,
                process_tool_call=process_tool_call,
                log_handler=log_handler,
            ),
            get_dev_mcp_server(
                volumes=[
                    (str(self.volume_root), "/mnt", 'w'),
                ],
                elicitation_callback=elicitation_callback,
                process_tool_call=process_tool_call,
                log_handler=log_handler,
            ),
        ]
        toolsets = [s.filtered(lambda ctx, tool: self._tool_allowed(tool.name)) for s in self._mcp_servers]

        agent = Agent(
            model=infer_model(settings.model),
            toolsets=toolsets,
            end_strategy='exhaustive',
        )

        # Backend-side tool: drives a multi-worker pool for the alloy-design
        # use case. Kept here rather than in the MCP server because each
        # worker is itself a PydanticAI sub-Agent run that uses settings.model
        # via the backend's infer_model. Workers share state via the parent
        # ProjectAgent's MCP session (same session_id -> same _results_cache).
        # Gated by the project's tool allow-list, same as MCP tools.
        if self._tool_allowed("agenthpc_run_workers"):
            from .agenthpc_workers import run_alloy_workers

            @agent.tool_plain
            async def agenthpc_run_workers(
                num_workers: int,
                app_type: str = "monbtaw",
                target_score: float | None = None,
                max_trials: int | None = None,
            ) -> dict[str, Any]:
                """
                Run a parallel agenthpc optimization with ``num_workers``
                independent LLM-driven worker loops. Each worker proposes one
                composition, claims it, submits to HPC, waits for completion,
                and records the score — sharing the per-session results cache
                so two workers never evaluate the same composition. Blocks
                until the campaign stops (threshold reached, budget exhausted,
                or the user cancels via ``agenthpc_cancel_all_pending``).
                Returns a final summary ``{best_parameters, best_score,
                num_trials, threshold_reached, budget_exhausted,
                stopped_by_user, trials}``.

                Use this only when the user asked for ``num_workers > 1``. For
                single-worker runs, drive the loop directly via the existing
                ``agenthpc_*`` tools described in the alloy-design skill.
                """
                def emit(level: str, message: str) -> None:
                    cb = self._cur_log_emitter
                    if cb is not None:
                        cb(level, "Workers", message)

                def emit_progress(text: str) -> None:
                    cb = self._cur_progress_emitter
                    if cb is not None:
                        cb(text)

                def emit_tool_event(tool_name: str, args: dict[str, Any], content: Any) -> None:
                    cb = self._cur_tool_event_emitter
                    if cb is not None:
                        cb(tool_name, args, content)

                return await run_alloy_workers(
                    project_agent=self,
                    app_type=app_type,
                    num_workers=num_workers,
                    target_score=target_score,
                    max_trials=max_trials,
                    log=emit,
                    progress=emit_progress,
                    emit_tool_event=emit_tool_event,
                )

        if settings.vistaguard.quarantine_enabled:
            self._sidecar.attach_quarantine_agent(
                build_quarantine_agent(settings.model)
            )
            # G1 slow-tier shares the Q-LLM model spec but uses a
            # separate Agent (different `output_type`).
            self._sidecar.attach_intent_extraction_agent(
                build_intent_extraction_agent(settings.model)
            )

        # VISTAGuard G1 tier banner 
        if self._sidecar.is_gate_enabled("G1"):
            @agent.system_prompt
            def vistaguard_tier_banner(ctx: RunContext[Any]) -> str:
                tier = self._sidecar.trust_scorer.current_tier()
                return f"[VISTAGUARD] Session tier: {tier.value.upper()}"

        @agent.system_prompt
        def system_prompt(ctx: RunContext[str]) -> str:
            parts = [BASE_SYSTEM_PROMPT]
            if self.project.system_prompt:
                parts.append("## Project Information")
                parts.append(self.project.system_prompt)

            # TODO Should cache these, but do need them to update when the project is edited
            if self.project.knowledge_bases:
                kb_lines = "\n".join(f"  - {slug}" for slug in self.project.knowledge_bases)
                kb_block = (
                    "Knowledge Bases available to this project (pass one of these "
                    "slugs as the `kb_slug` argument to `rag_search`):\n" + kb_lines
                )
            else:
                kb_block = (
                    "No Knowledge Bases are configured for this project; the "
                    "`rag_search` tool is not available."
                )
            parts.append(kb_block)

            skills_block = to_prompt(
                [self.skills_volume_dir / skill for skill in self.project.skills],
                {self.skills_volume_dir: "/mnt/skills"},
            )
            parts.append(skills_block)

            return "\n\n".join([p for p in parts if p])

        return agent

    def _tool_allowed(self, name: str) -> bool:
        """
        Check if a tool should be included in the project or not

        Match `name` against a list of fnmatch-style patterns in project.tools. Entries beginning
        with `!` are deny patterns; everything else is an allow pattern. A tool is allowed iff at
        least one allow pattern matches and no deny pattern matches. If there are no allow_patterns,
        assume allow "*".
        """
        allow_patterns = [p for p in self.project.tools if not p.startswith("!")]
        if not allow_patterns:
            allow_patterns = ['*']
        deny_patterns = [p[1:] for p in self.project.tools if p.startswith("!")]

        if not self.project.knowledge_bases and "rag_search" not in deny_patterns:
            deny_patterns.append("rag_search")

        if not any(fnmatch.fnmatchcase(name, p) for p in allow_patterns):
            return False
        if any(fnmatch.fnmatchcase(name, p) for p in deny_patterns):
            return False
        return True

    async def list_tools(self) -> list[mcp.types.Tool]:
        """
        List MCP tools available to this project across all attached MCP servers.

        The list is filtered by the project's `tools` allow/deny patterns (the same
        filter applied to the agent's toolsets), so callers see exactly the set the
        agent itself can invoke.
        """
        tools: list[mcp.types.Tool] = []
        for server in self._mcp_servers:
            for tool in await server.list_tools():
                if self._tool_allowed(tool.name):
                    tools.append(tool)
        return tools

    # Tool names that require the user's identity for HPC credential
    # lookup. ``_build_mcp_metadata`` adds the user payload for these.
    _HPC_TOOLS_NEEDING_USER: set[str] = {
        "submit_hpc_job", "get_hpc_job_status",
        "get_hpc_job_outputs", "list_hpc_jobs", "cancel_hpc_job",
    }

    def _build_mcp_metadata(self, tool_name: str) -> dict[str, Any]:
        """Build the ``vista`` metadata blob the MCP server expects: the
        per-agent project paths (always), plus the user payload for HPC
        tools that need credentials. Kept here so both ``call_tool`` and
        the agent-runtime ``process_tool_call`` use the same shape."""
        metadata: dict[str, Any] = {
            "vista": {
                "project_paths": {
                    "skills_dir": str(self.skills_volume_dir),
                    "output_dir": str(self.output_dir),
                    "uploads_dir": str(self.uploads_dir),
                },
            },
        }
        if tool_name in self._HPC_TOOLS_NEEDING_USER:
            metadata["vista"]["user"] = self.user.model_dump(mode='json')
        return metadata

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        """
        Call an MCP tool by name, searching across all attached MCP servers.

        Honors the project's `tools` filter. Returns the raw `CallToolResult`
        envelope (does not unwrap or raise on `isError=True`).

        Passes the same ``vista`` metadata the agent runtime would attach
        (project_paths, plus user for HPC tools) so tools like
        ``display_file`` can resolve ``/mnt/...`` sandbox paths against the
        calling agent's host volumes.
        """
        if not self._tool_allowed(name):
            raise KeyError(f"Tool {name!r} not found on any MCP server")
        metadata = self._build_mcp_metadata(name)
        for server in self._mcp_servers:
            tools = await server.list_tools()
            if any(t.name == name for t in tools):
                # NOTE: This bypasses process_tool_call (the VistaGuard
                # sidecar). For internal calls driven by backend tools
                # (e.g. agenthpc workers showing the plot) the bypass is
                # intentional — the gate has already been applied to the
                # outer tool call that triggered this one.
                try:
                    raw = await server.direct_call_tool(name, arguments, metadata)
                    is_error = False
                except Exception as exc:
                    raw = str(exc)
                    is_error = True
                return _wrap_as_call_tool_result(raw, is_error=is_error)
        raise KeyError(f"Tool {name!r} not found on any MCP server")

    async def __aenter__(self):
        await self._setup_volumes()
        await self.agent.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.agent.__aexit__(*exc)

    async def _setup_volumes(self) -> None:
        """
        Prepare the sandbox volumes. Note that volumes persist across reboots and ProjectAgent
        evictions.
        """
        self.volume_root.mkdir(parents=True, exist_ok=True)

        # Set up skill volume
        shutil.rmtree(self.skills_volume_dir, ignore_errors=True)
        self.skills_volume_dir.mkdir()
        for name in self.project.skills:
            src = settings.skills_dir / name
            if not src.is_dir():
                logging.warning(f"Skill {name!r} not found at {src}; skipping")
                continue
            shutil.copytree(src, self.skills_volume_dir / name, symlinks=True)
        proc = await asyncio.create_subprocess_exec("chmod", "-R", "o+rX", str(self.skills_volume_dir))
        await proc.wait()

    def _make_mcp_process_tool_call(self):
        async def process_tool_call(ctx: RunContext[Any], call_tool: CallToolFunc, name: str, tool_args: dict[str, Any]) -> ToolResult:
            # TODO Temporary scaffolding for getting per-user HPC credentials to the MCP server, we set up the s3m
            # creds via MCP metadata. Later we'll set up more generic MCP server configuration that supports 3rd party
            # MCP servers. It will launch isolated MCP server instances per project, and the user can configure any
            # environment vars/headers necessary.
            # The project_paths should also be changed to use env vars or some other mechanism.
            metadata = self._build_mcp_metadata(name)

            if self._sidecar.is_active():
                async def call_tool_wrapper(inner_name, inner_args, inner_metadata = None):
                    inner_metadata = {**(inner_metadata or {}), **metadata} # Merge in metadatas
                    return await call_tool(inner_name, inner_args, inner_metadata)
                return await self._sidecar.process_tool_call(ctx, call_tool_wrapper, name, tool_args)
            else:
                return await call_tool(name, tool_args, metadata)
        return process_tool_call

    def _make_mcp_elicitation_callback(self, stream_merger: StreamMerger[ProjectAgentStreamEvent]):
        async def elicitation_callback(
            context: mcp.shared.context.RequestContext[mcp.client.session.ClientSession, Any, Any],
            params: mcp.types.ElicitRequestParams,
        ) -> mcp.types.ElicitResult | mcp.types.ErrorData:
            if isinstance(params, mcp.types.ElicitRequestFormParams):
                event = McpFormElicitationEvent(
                    elicitation_id=str(uuid.uuid4()),
                    message=params.message,
                    requested_schema=params.requestedSchema,
                )
            elif isinstance(params, mcp.types.ElicitRequestURLParams):
                event = McpUrlElicitationEvent(
                    # TODO probably shouldn't assume the sent elicitationId is globally unique
                    elicitation_id=params.elicitationId,
                    url=params.url,
                    message=params.message,
                )
            else:
                return mcp.types.ElicitResult(action="cancel")

            # URL-mode ids come from the upstream server; refuse a duplicate sent by the MCP server
            if event.elicitation_id in self._elicitations:
                return mcp.types.ElicitResult(action="cancel")

            future: asyncio.Future[mcp.types.ElicitResult] = asyncio.get_running_loop().create_future()
            self._elicitations[event.elicitation_id] = future
            try:
                stream_merger.send(event)
            except StreamClosedError:
                # Merger already closed, the run finished before this notification.
                return mcp.types.ElicitResult(action="cancel")

            try:
                return await asyncio.wait_for(future, timeout=5 * 60)
            except asyncio.TimeoutError:
                return mcp.types.ElicitResult(action="cancel")
            finally:
                self._elicitations.pop(event.elicitation_id, None)
        return elicitation_callback

    async def run(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
    ) -> ProjectAgentResult:
        """
        Run the agent for a single agent "turn".
        """

        async for event in self.run_stream(
            user_prompt = user_prompt,
            message_history = message_history,
            enable_elicitation=False,
        ):
            if isinstance(event, ProjectAgentResultEvent):
                return event.result
        raise RuntimeError("Agent didn't emit a result") # Should be unreachable

    def run_stream(self,
        user_prompt: str,
        message_history: list[ModelMessage]|None = None,
        enable_elicitation: bool = False,
    ) -> AsyncIterator[ProjectAgentStreamEvent]:
        """
        Run the agent and return a stream of events.

        Yields all events from Pydantic, see `pydantic_ai.messages.AgentStreamEvent` and
        https://pydantic.dev/docs/ai/core-concepts/agent/#running-agents for more info.

        Also adds LogEvents of our own, that contains log lines from the agent and MCP server.

        Pass enable_elicitation to support MCP elicitation. When enabled, it will yield an McpElicitation
        event when elicitation is requested. You should call agent.resolve_elicitation with the result.
        """
        usage_limits = UsageLimits(**(self.project.usage_limits or {}))

        # Merge the agent's own event stream with our own MCP Server log notifications
        merger = StreamMerger[ProjectAgentStreamEvent]()
        logs: list[LogEntry] = []

        def log(level: str, area: str, message: str):
            logs.append(LogEntry(level = level, area = area, message = message))
            return LogEvent(level = level, area = area, message = message)

        async def log_handler(params: mcp.types.LoggingMessageNotificationParams):
            try:
                merger.send(log(str(params.level).upper(), "MCP Server", json_dump_if(params.data)))
            except StreamClosedError:
                pass # Merger already closed, the run finished before this notification.

        def emit_log(level: str, area: str, message: str) -> None:
            # Direct synchronous emitter used by backend-side tools that want
            # to stream progress (e.g. agenthpc_run_workers' per-trial lines).
            try:
                merger.send(log(level, area, message))
            except StreamClosedError:
                pass

        # Synthetic-part index allocator. The chat agent's own run uses small
        # indices starting at 0; we use a high base so backend-tool-emitted
        # parts never collide.
        synthetic_index = [10_000]

        def emit_progress(text: str) -> None:
            """Push an intermediate TextPart that the UI renders as an "agent
            thinking" bubble. Each call gets its own index so it shows up as
            a standalone bubble that collapses when the final response lands."""
            if not text:
                return
            idx = synthetic_index[0]
            synthetic_index[0] += 1
            part = TextPart(content=text)
            try:
                merger.send(PartStartEvent(index=idx, part=part))
                merger.send(PartEndEvent(index=idx, part=part))
            except StreamClosedError:
                pass

        def emit_tool_event(tool_name: str, args: dict[str, Any], content: Any) -> None:
            """Synthesize the (call, result) event pair for an MCP tool the
            chat agent did not invoke directly. Used to surface workers'
            display_file call so the chat UI's image renderer fires."""
            try:
                call_part = ToolCallPart(tool_name=tool_name, args=args)
                merger.send(FunctionToolCallEvent(part=call_part))
                return_part = ToolReturnPart(
                    tool_name=tool_name,
                    content=content,
                    tool_call_id=call_part.tool_call_id,
                )
                # Use `part=` rather than the deprecated `result=` so the
                # serialized JSON includes the field name the UI expects.
                merger.send(FunctionToolResultEvent(part=return_part))
            except StreamClosedError:
                pass

        async def agent_stream() -> AsyncIterator[ProjectAgentStreamEvent]:
            async with self._run_lock:
                self._cur_mcp_elicitation_callback = self._make_mcp_elicitation_callback(merger) if enable_elicitation else None
                self._cur_mcp_process_tool_call = self._make_mcp_process_tool_call()
                self._cur_mcp_log_handler = log_handler
                self._cur_log_emitter = emit_log
                self._cur_progress_emitter = emit_progress
                self._cur_tool_event_emitter = emit_tool_event
                try:
                    yield log("INFO", "Agent", "\n".join([
                        f"New request:",
                        f"    project: {self.project.name}",
                        f"    userMessage: {json.dumps(user_prompt[:200])}",
                        f"    historyTurns: {len(message_history or [])}",
                    ]))


                    # VISTAGuard G1 early-rejection. No-op when G1 isn't active.
                    g1_deny = await self._sidecar.evaluate_user_prompt(user_prompt)
                    if g1_deny is not None:
                        yield log("WARNING", "VISTAGuard:G1", g1_deny.reason)
                        yield ProjectAgentResultEvent(result=ProjectAgentResult(
                            new_messages=[],
                            usage=RunUsage(),
                            logs=list(logs),
                        ))
                        return

                    async for event in self.agent.run_stream_events(
                        user_prompt,
                        message_history=message_history,
                        usage_limits=usage_limits,
                    ):
                        if isinstance(event, AgentRunResultEvent):
                            yield log("INFO", "Agent", f"Turn completed")
                            result = ProjectAgentResult(
                                new_messages=event.result.new_messages(),
                                usage=event.result.usage(),
                                logs=list(logs),
                            )
                            yield ProjectAgentResultEvent(result = result)
                            break # Ignore any further log events
                        else:
                            # Add some logging
                            if isinstance(event, FunctionToolCallEvent):
                                yield log("INFO", f"Tool:{event.part.tool_name}", message=f"Called {event.part.tool_name} args: {json_dump_if(event.part.args)}")
                            elif isinstance(event, FunctionToolResultEvent):
                                # `event.part` in current PydanticAI; `.result`
                                # is the deprecated alias. Use the new name.
                                result_part = event.part
                                if isinstance(result_part, RetryPromptPart):
                                    yield log("WARNING", f"Tool:{result_part.tool_name}", f"Tool {result_part.tool_name} failed")
                                else:
                                    yield log("INFO", f"Tool:{result_part.tool_name}", f"Tool {result_part.tool_name} completed")

                            yield event
                finally:
                    self._cur_mcp_elicitation_callback = None
                    self._cur_mcp_process_tool_call = None
                    self._cur_mcp_log_handler = None
                    self._cur_log_emitter = None
                    self._cur_progress_emitter = None
                    self._cur_tool_event_emitter = None

        merger.add_stream(agent_stream())
        return aiter(merger)

    def resolve_elicitation(self,
        elicitation_id: str,
        action: Literal["accept", "decline", "cancel"],
        content: dict[str, Any] | None = None
    ):
        """ Resolve the elicitation request with a value """
        future: asyncio.Future | None = self._elicitations.pop(elicitation_id, None)
        if future is None or future.done():
            raise KeyError(f"Elicitation {elicitation_id} not found or already resolved")
        future.set_result(mcp.types.ElicitResult(
            action=action,
            content=content if action == "accept" else None,
        ))

    def cancel_elicitations(self):
        """ Cancel all outstanding elicitation requests """
        for future in self._elicitations.values():
            if not future.done():
                future.set_result(mcp.types.ElicitResult(action="cancel"))
        self._elicitations.clear()
