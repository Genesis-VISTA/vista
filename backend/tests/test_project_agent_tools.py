"""
Tool allow/deny filtering and per-call vista metadata (testing roadmap
Milestone B).

`_tool_allowed` decides which MCP tools a project can see, and
`_build_vista_metadata` is what ships the caller's HPC credentials and volume
paths to the MCP servers — both are security-relevant, so they get
table-driven regression coverage here.
"""

import uuid

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo

from harness import agent_under_test, make_local_settings, make_project, scripted_model
from vista_backend.agents.agents import ProjectAgent
from vista_backend.config import settings
from vista_backend.metrics import get_recorder

pytestmark = pytest.mark.unit


def _agent(project=None, local=None) -> ProjectAgent:
    return ProjectAgent(
        project or make_project(), local or make_local_settings(), uuid.uuid4()
    )


# ---------------------------------------------------------------------------
# _tool_allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patterns", "tool", "expected"),
    [
        # No allow patterns means allow everything.
        ([], "run_bash", True),
        (["*"], "run_bash", True),
        # Explicit allow list.
        (["run_bash"], "run_bash", True),
        (["run_bash"], "create_file", False),
        # Wildcards are fnmatch, not regex.
        (["get_hpc_*"], "get_hpc_job_status", True),
        (["get_hpc_*"], "submit_hpc_job", False),
        # Deny wins over allow.
        (["*", "!run_bash"], "run_bash", False),
        (["run_bash", "!run_bash"], "run_bash", False),
        (["*", "!submit_hpc_*"], "submit_hpc_job", False),
        (["*", "!submit_hpc_*"], "get_hpc_job_status", True),
        # A deny-only list still allows everything else.
        (["!run_bash"], "create_file", True),
        # Matching is case sensitive.
        (["run_bash"], "RUN_BASH", False),
        # A stored deny of a tool that no longer exists (list_hpc_jobs) is a
        # no-op: it matches nothing, and the rest of the toolchain is allowed.
        (["*", "!list_hpc_jobs"], "get_hpc_job_status", True),
    ],
)
def test_tool_allowed_patterns(patterns, tool, expected):
    project = make_project(tools=patterns, knowledge_bases=["kb"])
    assert _agent(project)._tool_allowed(tool) is expected


def test_rag_search_denied_when_project_has_no_knowledge_bases():
    """Without a KB there is nothing to search, so the tool is auto-denied."""
    project = make_project(tools=["*"], knowledge_bases=[])
    assert _agent(project)._tool_allowed("rag_search") is False


def test_rag_search_allowed_when_project_has_a_knowledge_base():
    project = make_project(tools=["*"], knowledge_bases=["msre-reports"])
    assert _agent(project)._tool_allowed("rag_search") is True


def test_explicit_rag_search_deny_survives_a_configured_knowledge_base():
    project = make_project(tools=["*", "!rag_search"], knowledge_bases=["msre-reports"])
    assert _agent(project)._tool_allowed("rag_search") is False


# ---------------------------------------------------------------------------
# _build_vista_metadata
# ---------------------------------------------------------------------------


def test_vista_metadata_includes_project_paths():
    agent = _agent()
    paths = agent._build_vista_metadata("display_file")["vista"]["project_paths"]
    assert paths == {
        "skills_dir": str(agent.skills_volume_dir),
        "output_dir": str(agent.output_dir),
        "uploads_dir": str(agent.uploads_dir),
    }


def test_vista_metadata_carries_a_uri_map_for_display_file():
    """
    Without this map `display_file` has nothing to resolve a sandbox path
    against, so it refuses every one of them and a plot the agent has just
    written cannot be shown. The templates are the UI's file routes, because
    the browser is what fetches the result.

    That these templates actually resolve is pinned in the MCP server's own
    suite (`test_display_file.py`), which is where `resolve_uri` lives — the
    two packages have separate virtualenvs and cannot import each other.
    """
    agent = _agent(make_project(name="molten salt"))
    uri_map = agent._build_vista_metadata("display_file")["vista"]["uri_map"]
    assert uri_map == {
        "file:///mnt/data/output/{path}": (
            "/api/files/outputs/{path}?project_name=molten%20salt"
        ),
        "file:///mnt/data/uploads/{path}": (
            "/api/files/uploads/{path}?project_name=molten%20salt"
        ),
    }


def test_vista_metadata_maps_sandbox_files_to_project_download_urls():
    project = make_project(name="molten salt/analysis")
    uri_map = _agent(project)._build_vista_metadata("display_file")["vista"]["uri_map"]

    assert uri_map == {
        "file:///mnt/data/output/{path}": (
            "/api/files/outputs/{path}?project_name=molten%20salt%2Fanalysis"
        ),
        "file:///mnt/data/uploads/{path}": (
            "/api/files/uploads/{path}?project_name=molten%20salt%2Fanalysis"
        ),
    }


def test_vista_metadata_paths_are_scoped_per_project():
    """Volume paths key on the project, so one project cannot read another's."""
    local = make_local_settings()
    a = _agent(make_project(), local)
    b = _agent(make_project(), local)
    assert a.volume_root != b.volume_root
    assert (
        a._build_vista_metadata("display_file")["vista"]["project_paths"]
        != b._build_vista_metadata("display_file")["vista"]["project_paths"]
    )


@pytest.mark.parametrize(
    "tool",
    [
        "submit_hpc_job",
        "get_hpc_job_status",
        "get_hpc_job_outputs",
        "cancel_hpc_job",
        "hpc_list_files",
        "hpc_download_files",
        "hpc_upload_files",
    ],
)
def test_vista_metadata_attaches_settings_credentials_for_hpc_tools(tool):
    local = make_local_settings(
        odo_s3m_token="odo-s3m-secret",
        frontier_s3m_token="frontier-s3m-secret",
        nersc_iri_token="iri-secret",
        odo_remote_dir="/odo/proj/vista",
    )
    # Still `user` on the wire: the MCP server reads that key.
    metadata = _agent(make_project(), local)._build_vista_metadata(tool)["vista"][
        "user"
    ]
    assert metadata["odo_s3m_token"] == "odo-s3m-secret"
    assert metadata["frontier_s3m_token"] == "frontier-s3m-secret"
    # The legacy single token is never sent: the MCP server no longer falls
    # back to it, and a stale copy would disagree with the per-cluster ones.
    assert "s3m_token" not in metadata
    assert metadata["nersc_iri_token"] == "iri-secret"
    assert metadata["odo_remote_dir"] == "/odo/proj/vista"


@pytest.mark.parametrize("tool", ["rag_search", "display_file", "run_bash"])
def test_vista_metadata_withholds_credentials_from_non_hpc_tools(tool):
    local = make_local_settings(
        odo_s3m_token="s3m-secret", nersc_iri_token="iri-secret"
    )
    metadata = _agent(make_project(), local)._build_vista_metadata(tool)
    assert "user" not in metadata["vista"]
    assert "s3m-secret" not in str(metadata)


def test_vista_metadata_omits_metrics_when_the_recorder_is_off():
    assert not get_recorder().enabled, "metrics recorder should default to off"
    metadata = _agent()._build_vista_metadata("submit_hpc_job")
    assert "metrics" not in metadata["vista"]


# ---------------------------------------------------------------------------
# The filter as the model sees it
# ---------------------------------------------------------------------------


def _tools_offered_to_model(project, local) -> list[str]:
    seen: list[list[str]] = []

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append([t.name for t in info.function_tools])
        return ModelResponse(parts=[TextPart("ok")])

    async def run() -> list[str]:
        with agent_under_test(project, local, scripted_model(driver)) as (agent, _):
            async for _ in agent.run_stream(user_prompt="hi"):
                pass
        return seen[0]

    return run()


@pytest.mark.anyio
async def test_denied_tools_are_not_offered_to_the_model():
    project = make_project(tools=["*", "!run_bash"], knowledge_bases=["kb"])
    offered = await _tools_offered_to_model(project, make_local_settings())
    assert "run_bash" not in offered
    assert "rag_search" in offered


@pytest.mark.anyio
async def test_allow_list_limits_the_tools_offered_to_the_model():
    project = make_project(tools=["display_file"], knowledge_bases=["kb"])
    offered = await _tools_offered_to_model(project, make_local_settings())
    assert "display_file" in offered
    assert "rag_search" not in offered
    assert "submit_hpc_job" not in offered


@pytest.mark.anyio
async def test_rag_search_is_hidden_from_the_model_without_a_knowledge_base():
    project = make_project(tools=["*"], knowledge_bases=[])
    offered = await _tools_offered_to_model(project, make_local_settings())
    assert "rag_search" not in offered
    assert "submit_hpc_job" in offered


def test_settings_model_is_the_hermetic_test_model():
    """Guards the conftest default that keeps PR CI from needing an API key."""
    assert settings.model == "test"


# ---------------------------------------------------------------------------
# Background commands (openspec change background-tasks, tasks 4.2 and 4.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("patterns", [["*"], ["wait_task", "signal_task"], []])
def test_the_watchers_own_tools_are_never_offered(patterns):
    agent = _agent(make_project(tools=patterns))
    assert not agent._tool_allowed("wait_task")
    assert not agent._tool_allowed("signal_task")


@pytest.mark.anyio
async def test_the_model_is_not_shown_the_arguments_vista_fills_in():
    from pydantic_ai.tools import ToolDefinition

    from vista_backend.agents.agents import _hide_backend_args

    run_bash = ToolDefinition(
        name="run_bash",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "background": {"type": "boolean"},
                "timeout": {"type": "integer"},
                "task_id": {"type": "string"},
                "log_path": {"type": "string"},
            },
            "required": ["command"],
        },
    )
    view = ToolDefinition(name="view", parameters_json_schema={"type": "object"})

    shown = await _hide_backend_args(None, [run_bash, view])  # type: ignore[arg-type]

    assert set(shown[0].parameters_json_schema["properties"]) == {
        "command",
        "background",
        "timeout",
    }
    assert shown[1] is view


class _Forwarded:
    """
    The MCP call a `process_tool_call` sends on, recorded. A background
    `run_bash` starts its task in `sandbox`, as the dev MCP server's does.
    """

    def __init__(self, sandbox=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.sandbox = sandbox

    async def __call__(self, name, args, metadata=None):
        self.calls.append((name, args))
        if self.sandbox is not None and args.get("background"):
            self.sandbox.start(args["task_id"])
        return "ok"


@pytest.fixture
async def background(engine, session, monkeypatch, tmp_path):
    """A conversation's agent whose sandbox is a `FakeSandbox`, on the test database."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    from harness import FakeSandbox, seed_project
    from vista_backend.services import background_tasks, inbox
    from vista_backend.services import chat_session as chat_session_service

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        background_tasks, "session_factory", lambda: AsyncSession(engine)
    )
    registry = background_tasks.BackgroundTaskRegistry()
    monkeypatch.setattr(background_tasks, "background_task_registry", registry)
    monkeypatch.setattr(inbox, "_listeners", [])
    project = await seed_project(session)
    row = await chat_session_service.create_chat_session(session, project_id=project.id)
    chat_id = row.id
    await session.commit()
    sandbox = FakeSandbox()

    def build() -> ProjectAgent:
        agent = ProjectAgent(make_project(tools=["*"]), make_local_settings(), chat_id)
        agent.project = agent.project.model_copy(update={"id": project.id})
        agent.call_sandbox_tool = sandbox  # type: ignore[method-assign]
        return agent

    class Env:
        pass

    env = Env()
    env.engine, env.chat_id, env.sandbox, env.registry, env.build = (
        engine,
        chat_id,
        sandbox,
        registry,
        build,
    )
    yield env
    await registry.shutdown()


async def _start(agent: ProjectAgent, args: dict, forwarded: _Forwarded | None = None):
    forwarded = forwarded or _Forwarded(getattr(agent, "call_sandbox_tool", None))
    result = await agent._make_mcp_process_tool_call()(
        None, forwarded, "run_bash", args
    )  # type: ignore[arg-type]
    return result, forwarded


@pytest.mark.anyio
async def test_a_background_run_bash_is_recorded_and_sent_with_its_id_and_log(
    background,
):
    from vista_backend.services import background_tasks

    agent = background.build()
    result, forwarded = await _start(
        agent,
        {"command": "sleep 60", "background": True, "timeout": 120, "task_id": "x"},
    )

    async with background_tasks.session_factory() as db:
        [record] = await background_tasks.list_for_chat(db, background.chat_id)
    [(name, args)] = forwarded.calls
    assert name == "run_bash"
    assert args == {
        "command": "sleep 60",
        "background": True,
        "timeout": 120,
        "task_id": str(record.id),
        "log_path": f"/mnt/data/tasks/{record.id}.log",
    }, "VISTA's id and log, whatever the model put"
    assert record.timeout_s == 120 and record.state == "running"
    assert "Started background task bg-1" in result
    assert record.log_path in result
    assert background.registry.watching(record.id)


@pytest.mark.anyio
async def test_a_foreground_run_bash_goes_straight_to_the_sandbox(background):
    agent = background.build()
    result, forwarded = await _start(agent, {"command": "ls"})

    assert result == "ok"
    assert forwarded.calls == [("run_bash", {"command": "ls"})]
    assert not background.registry._watches


@pytest.mark.anyio
async def test_a_48_hour_limit_is_refused_without_reaching_the_sandbox(background):
    agent = background.build()
    result, forwarded = await _start(
        agent, {"command": "sleep 1", "background": True, "timeout": 48 * 3600}
    )

    assert result.startswith("Refused:") and "24 hours" in result
    assert forwarded.calls == []


@pytest.mark.anyio
async def test_a_fifth_command_is_refused_and_the_four_run_on(background):
    agent = background.build()
    for n in range(4):
        await _start(agent, {"command": f"sleep {n}", "background": True})

    result, forwarded = await _start(agent, {"command": "sleep 9", "background": True})

    assert result.startswith("Refused:") and "4 background commands" in result
    assert forwarded.calls == []
    assert len(background.registry._watches) == 4


@pytest.mark.anyio
async def test_tasks_are_listed_stopped_and_reported_after_the_agent_is_rebuilt(
    background,
):
    from vista_backend.services import inbox

    first = background.build()
    for command in ("sleep 600", "python fit.py"):
        await _start(first, {"command": command, "background": True})

    # A Settings or model change: the conversation's next turn has a new agent.
    rebuilt = background.build()
    tools = rebuilt.agent._function_toolset.tools

    listed = await tools["list_tasks"].function()
    assert "bg-1  command  running" in listed and "`sleep 600`" in listed
    assert "bg-2  command  running" in listed

    assert await tools["stop_task"].function(task_id="bg-1") == "bg-1 is stopped."
    assert "signal_task" in background.sandbox.names_called(), (
        "the signal went to the sandbox that owns the process"
    )

    [fit] = [w.record for w in background.registry._watches.values()]
    background.sandbox.exit(str(fit.id), 0)
    await background.registry.wait(fit.id)
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with AsyncSession(background.engine) as db:
        texts = [i.text for i in await inbox.list_queued(db, background.chat_id)]
    assert any(t.startswith("Background task bg-1 was stopped") for t in texts)
    assert any(t.startswith("Background task bg-2 exited with status 0") for t in texts)

    listed = await tools["list_tasks"].function()
    assert "bg-1  command  stopped" in listed and "bg-2  command  exited 0" in listed
    assert await tools["stop_task"].function(task_id="bg-2") == (
        "bg-2 has already ended (exited)."
    )
    assert "No background task 'bg-9'" in await tools["stop_task"].function(
        task_id="bg-9"
    )
