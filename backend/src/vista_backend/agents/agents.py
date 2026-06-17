"""
Logic to build the actual PydanticAI Agent
"""
import json, logging, os, shutil, uuid, asyncio
from typing import AsyncIterator, Literal, Annotated as A, Any
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, Field, Discriminator
from pydantic_ai import Agent, RunContext, UsageLimits, RunUsage, AgentRunResultEvent
from pydantic_ai.mcp import MCPServer, MCPServerStdio, MCPServerStreamableHTTP, ProcessToolCallback, CallToolFunc, ToolResult
from pydantic_ai.messages import (
    AgentStreamEvent,
    ModelMessage,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RetryPromptPart,
)
from pydantic_ai.models import infer_model
import mcp.client.session
import mcp.shared.context
import mcp.types
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.db import get_engine
from ..db.schemas import ProjectPublic, SkillTable, UserPublicWithConfig
from ..utils.streams import StreamMerger, StreamClosedError
from ..utils.misc import json_dump_if, tool_allowed
from ..vistaguard import VistaGuardSidecar
from ..vistaguard.capabilities import (
    ApprovalOutcome,
    VistaGuardApprovalCapability,
    VistaGuardDeny,
)
from ..vistaguard.quarantine import (
    build_code_intent_extraction_agent,
    build_intent_extraction_agent,
    build_quarantine_agent,
)
from .skills import to_prompt


BASE_SYSTEM_PROMPT = (Path(__file__).parent / "base_system_prompt.md").read_text()


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

class McpToolApprovalEvent(BaseModel):
    """
    A high-stakes tool call awaiting human approval (VISTAGuard, Phase 3.5).

    Emitted when a tool registered with `requires_approval=True` is called.
    Resolve it with the existing `resolve_elicitation(elicitation_id, action,
    content)` API: `action="accept"` approves (optional `content` overrides
    the tool args), `decline`/`cancel` denies.
    """
    event_kind: Literal["mcp_tool_approval"] = "mcp_tool_approval"
    mode: Literal["tool_approval"] = "tool_approval"
    elicitation_id: str
    tool_name: str
    message: str
    args: dict[str, Any] | None = None
    decision_metadata: dict[str, Any] | None = None
    """
    VISTAGuard gate decision metadata for this call (e.g. G5's fast-tier
    summary: resolved SLURM script, account verified, resource ceiling
    passed, no denylist match). Populated from the sidecar's
    `pending_approval_metadata`; None for tool calls with no gate context.
    """

McpElicitationEvent = McpFormElicitationEvent | McpUrlElicitationEvent | McpToolApprovalEvent


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
    def __init__(self, project: ProjectPublic, user: UserPublicWithConfig, session_id: uuid.UUID | None):
        self.project = project
        self.user = user
        self.session_id = session_id
        # Id per chat session. A new ProjectAgent with the same session gets
        # the same sandbox/storage roots; different sessions for the same
        # project+user are isolated.
        self.id = f"{session_id}" if session_id else f"{project.id}-{user.id}"
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
        # Note: we lock to prevent parallel run_stream calls on the same live
        # session agent. Session-scoped agents make that granularity
        # reasonable.
        self._run_lock = asyncio.Lock()
        self._cur_mcp_elicitation_callback: mcp.client.session.ElicitationFnT | None = None
        self._cur_mcp_process_tool_call: ProcessToolCallback | None = None
        self._cur_mcp_log_handler: mcp.client.session.LoggingFnT | None = None
        # Per-run emitter for high-stakes tool-approval requests (VISTAGuard
        # R6); set in run_stream when elicitation is enabled.
        self._cur_approval_emit: Callable[..., Any] | None = None
        self.agent = self._build_agent()

    @property
    def sidecar(self) -> VistaGuardSidecar:
        """The per-session VISTAGuard sidecar (capability registry, trust
        scorer, incident manager). The trust scorer backing the
        ``/vistaguard/state`` and ``/vistaguard/reauth`` endpoints lives
        here."""
        return self._sidecar

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

        # VISTAGuard gates are PydanticAI capabilities (Phase 3.5). The
        # sidecar is the factory that builds the capability list from its
        # active gate set; a flag-off build yields an empty list, keeping
        # the agent byte-identical to baseline VISTA. The Q-LLM agents
        # (built from the top-level model spec) are threaded in for the
        # slow tiers.
        quarantine_agent = None
        intent_extraction_agent = None
        code_intent_extraction_agent = None
        if settings.vistaguard.quarantine_enabled:
            quarantine_agent = build_quarantine_agent(settings.model)
            intent_extraction_agent = build_intent_extraction_agent(settings.model)
            # Shared by G4 and G5 slow tiers ("what is this code/job
            # trying to do?").
            code_intent_extraction_agent = build_code_intent_extraction_agent(
                settings.model
            )

        capabilities = self._sidecar.build_capabilities(
            quarantine_agent=quarantine_agent,
            intent_extraction_agent=intent_extraction_agent,
            code_intent_extraction_agent=code_intent_extraction_agent,
        )
        # Human-in-the-loop approval for `requires_approval=True` tools
        # (VISTAGuard R6). Inert until such a tool is registered (G5 in
        # Phase 4); resolves via the existing resolve_elicitation surface.
        capabilities.append(
            VistaGuardApprovalCapability(request_approval=self._request_tool_approval)
        )

        agent = Agent(
            model=infer_model(settings.model),
            toolsets=toolsets,
            end_strategy='exhaustive',
            capabilities=capabilities,
        )

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
        Check if a tool should be included in the project or not.

        Match `name` against a list of fnmatch-style patterns in project.tools. Entries beginning
        with `!` are deny patterns; everything else is an allow pattern. A tool is allowed iff at
        least one allow pattern matches and no deny pattern matches. If there are no allow_patterns,
        assume allow "*".
        """
        patterns = list(self.project.tools)
        if not self.project.knowledge_bases and "!rag_search" not in patterns:
            patterns.append("!rag_search")
        return tool_allowed(name, patterns)

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

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        """
        Call an MCP tool by name, searching across all attached MCP servers.

        Honors the project's `tools` filter. Returns the raw `CallToolResult`
        envelope (does not unwrap or raise on `isError=True`).
        """
        if not self._tool_allowed(name):
            raise KeyError(f"Tool {name!r} not found on any MCP server")
        for server in self._mcp_servers:
            tools = await server.list_tools()
            if any(t.name == name for t in tools):
                # TODO: This bypasses process_tool_call. That's probably fine for VistaGuard as these
                # calls are user triggered. But will break job submission. Leaving for now as using
                # metadata for job submission credentials is a temporary solution anyways
                return await server._client.call_tool(name, arguments)
        raise KeyError(f"Tool {name!r} not found on any MCP server")

    async def __aenter__(self):
        await self._setup_volumes()
        await self.agent.__aenter__()
        return self

    async def __aexit__(self, *exc):
        # Flush/stop the VISTAGuard provenance sink (e.g. the Flowcept
        # broker controller) before tearing down the agent. Best-effort:
        # provenance teardown must never mask the agent's own exit.
        try:
            self._sidecar.provenance.close()
        except Exception:  # noqa: BLE001 - teardown must not raise
            logging.getLogger(__name__).warning(
                "VISTAGuard: provenance.close() failed", exc_info=True
            )
        await self.agent.__aexit__(*exc)

    async def _setup_volumes(self) -> None:
        """
        Prepare the sandbox volumes. Note that volumes persist across reboots and ProjectAgent
        evictions.
        """
        self.volume_root.mkdir(parents=True, exist_ok=True)

        # Set up skill volume
        async with AsyncSession(get_engine()) as session:
            rows = (await session.exec(
                select(SkillTable).where(col(SkillTable.name).in_(self.project.skills))
            )).all()
        skill_dirs = {row.name: settings.data_dir / row.path for row in rows}

        shutil.rmtree(self.skills_volume_dir, ignore_errors=True)
        self.skills_volume_dir.mkdir()
        for name in self.project.skills:
            src = skill_dirs.get(name)
            if src is None or not src.is_dir():
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
            metadata: dict[str, Any] = {
                "vista": {
                    "project_paths": {
                        "skills_dir": str(self.skills_volume_dir),
                        "output_dir": str(self.output_dir),
                        "uploads_dir": str(self.uploads_dir),
                    },
                },
            }
            HPC_TOOLS = {
                "submit_hpc_job", "get_hpc_job_status",
                "get_hpc_job_outputs", "list_hpc_jobs", "cancel_hpc_job",
            }
            if name in HPC_TOOLS:
                metadata["vista"]["user"] = self.user.model_dump(mode='json')

            # VISTAGuard no longer mediates here: gate enforcement runs
            # via the Agent's capability hooks (Phase 3.5). This callback
            # only injects the per-call MCP metadata.
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

    async def _request_tool_approval(self, *, tool_name: str, tool_call_id: str, args: Any) -> ApprovalOutcome:
        """
        Ask the user to approve a high-stakes (`requires_approval`) tool
        call. Called by `VistaGuardApprovalCapability`.

        Routes through the current run's approval emitter (set in
        `run_stream` when elicitation is enabled). When no approval
        channel is active (e.g. `enable_elicitation=False`), fails closed
        with a deny.
        """
        emit = self._cur_approval_emit
        if emit is None:
            return ApprovalOutcome(
                approved=False,
                message=(
                    f"Tool {tool_name!r} requires approval but no approval "
                    f"channel is active for this run."
                ),
            )
        return await emit(tool_name=tool_name, tool_call_id=tool_call_id, args=args)

    def _make_approval_emitter(self, stream_merger: StreamMerger[ProjectAgentStreamEvent]) -> Callable[..., Awaitable[ApprovalOutcome]]:
        async def request_approval(*, tool_name: str, tool_call_id: str, args: Any) -> ApprovalOutcome:
            # Reuse the elicitation Future registry + resolve_elicitation
            # surface so the frontend's existing approve/deny flow applies.
            if tool_call_id in self._elicitations:
                return ApprovalOutcome(approved=False, message="Duplicate approval id.")

            # VISTAGuard gate decision metadata for the approval UI (e.g.
            # G5's fast-tier summary), stashed on the sidecar by the gate
            # capability that deferred this call.
            decision_metadata = self._sidecar.pending_approval_metadata.get(
                tool_call_id
            )
            event = McpToolApprovalEvent(
                elicitation_id=tool_call_id,
                tool_name=tool_name,
                message=f"Approve call to {tool_name!r}?",
                args=args if isinstance(args, dict) else None,
                decision_metadata=decision_metadata,
            )
            future: asyncio.Future[mcp.types.ElicitResult] = asyncio.get_running_loop().create_future()
            self._elicitations[tool_call_id] = future
            try:
                stream_merger.send(event)
            except StreamClosedError:
                self._elicitations.pop(tool_call_id, None)
                self._sidecar.note_approval_outcome(tool_call_id, approved=False)
                return ApprovalOutcome(approved=False, message="Run finished before approval.")

            try:
                result = await asyncio.wait_for(future, timeout=5 * 60)
            except asyncio.TimeoutError:
                self._sidecar.note_approval_outcome(tool_call_id, approved=False)
                return ApprovalOutcome(approved=False, message="Approval request timed out.")
            finally:
                self._elicitations.pop(tool_call_id, None)

            approved = result.action == "accept"
            # Record the outcome: clears the pending metadata and, on a
            # decline of a G5-gated call, marks the sticky-on-decline.
            self._sidecar.note_approval_outcome(tool_call_id, approved=approved)
            if approved:
                override = result.content if isinstance(result.content, dict) else None
                return ApprovalOutcome(approved=True, override_args=override)
            return ApprovalOutcome(approved=False, message=f"Tool call {result.action} by user.")
        return request_approval

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

        async def agent_stream() -> AsyncIterator[ProjectAgentStreamEvent]:
            async with self._run_lock:
                self._cur_mcp_elicitation_callback = self._make_mcp_elicitation_callback(merger) if enable_elicitation else None
                self._cur_mcp_process_tool_call = self._make_mcp_process_tool_call()
                self._cur_mcp_log_handler = log_handler
                self._cur_approval_emit = self._make_approval_emitter(merger) if enable_elicitation else None
                try:
                    # SEV1 termination (VISTAGuard incident playbook): once
                    # the trust scorer is terminated, refuse every request
                    # without invoking the model -- gate-agnostic, so the
                    # session stays dead even if G1 is disabled.
                    if self._sidecar.trust_scorer.terminated:
                        yield log("ERROR", "VISTAGuard",
                                  "Session terminated by a prior SEV1 incident; refusing request.")
                        yield ProjectAgentResultEvent(result=ProjectAgentResult(
                            new_messages=[],
                            usage=RunUsage(),
                            logs=list(logs),
                        ))
                        return

                    yield log("INFO", "Agent", "\n".join([
                        f"New request:",
                        f"    project: {self.project.name}",
                        f"    userMessage: {json.dumps(user_prompt[:200])}",
                        f"    historyTurns: {len(message_history or [])}",
                    ]))

                    # G1 early-rejection runs via the capability's
                    # before_run hook and raises VistaGuardDeny (caught
                    # below) before any model request.
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
                                if isinstance(event.part, RetryPromptPart):
                                    yield log("WARNING", f"Tool:{event.part.tool_name}", f"Tool {event.part.tool_name} failed")
                                else:
                                    yield log("INFO", f"Tool:{event.part.tool_name}", f"Tool {event.part.tool_name} completed")

                            yield event
                except VistaGuardDeny as deny:
                    yield log("WARNING", "VISTAGuard:G1", deny.decision.reason)
                    yield ProjectAgentResultEvent(result=ProjectAgentResult(
                        new_messages=[],
                        usage=RunUsage(),
                        logs=list(logs),
                    ))
                    return
                finally:
                    self._cur_mcp_elicitation_callback = None
                    self._cur_mcp_process_tool_call = None
                    self._cur_mcp_log_handler = None
                    self._cur_approval_emit = None

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
