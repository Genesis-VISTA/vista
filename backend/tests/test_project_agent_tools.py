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

from harness import agent_under_test, make_project, make_user, scripted_model
from vista_backend.agents.agents import ProjectAgent
from vista_backend.config import settings
from vista_backend.metrics import get_recorder

pytestmark = pytest.mark.unit


def _agent(project=None, user=None) -> ProjectAgent:
    return ProjectAgent(project or make_project(), user or make_user(), uuid.uuid4())


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
    Without this map `display_file` has nothing to resolve a path against, so it
    refuses every one of them and a plot the agent has just written cannot be
    shown. The templates are the UI's file routes, because the browser is what
    fetches the result.

    That these templates actually resolve is pinned in the MCP server's own
    suite (`test_display_file.py`), which is where `resolve_uri` lives — the
    two packages have separate virtualenvs and cannot import each other.
    """
    agent = _agent(make_project(name="molten salt"))
    uri_map = agent._build_vista_metadata("display_file")["vista"]["uri_map"]

    # The sandbox paths are the ones the agent actually sees: get_hpc_job_outputs
    # and the dev sandbox both hand back /mnt/data/... paths.
    assert uri_map["file:///mnt/data/output/{path}"] == (
        "/api/files/outputs/{path}?project_name=molten%20salt"
    )
    assert uri_map["file:///mnt/data/uploads/{path}"] == (
        "/api/files/uploads/{path}?project_name=molten%20salt"
    )
    # Host volume paths map too, so a tool reporting a host path still renders.
    assert uri_map[f"file://{agent.output_dir}/{{path}}"] == (
        "/api/files/outputs/{path}?project_name=molten%20salt"
    )
    assert uri_map[f"file://{agent.uploads_dir}/{{path}}"] == (
        "/api/files/uploads/{path}?project_name=molten%20salt"
    )


def test_vista_metadata_maps_sandbox_files_to_project_download_urls():
    project = make_project(name="molten salt/analysis")
    uri_map = _agent(project)._build_vista_metadata("display_file")["vista"]["uri_map"]

    assert uri_map["file:///mnt/data/output/{path}"] == (
        "/api/files/outputs/{path}?project_name=molten%20salt%2Fanalysis"
    )
    assert uri_map["file:///mnt/data/uploads/{path}"] == (
        "/api/files/uploads/{path}?project_name=molten%20salt%2Fanalysis"
    )


def test_vista_metadata_uri_map_targets_are_relative_and_project_scoped():
    """
    Targets stay UI-relative so the backend never needs its external origin, and
    each carries its own project_name — one project's map cannot serve another's
    files.
    """
    a = _agent(make_project(name="water4energy"))
    b = _agent(make_project(name="molten-salt"))
    a_map = a._build_vista_metadata("display_file")["vista"]["uri_map"]
    b_map = b._build_vista_metadata("display_file")["vista"]["uri_map"]

    for target in a_map.values():
        assert target.startswith("/api/files/"), target
        assert "project_name=water4energy" in target
    sandbox_key = "file:///mnt/data/output/{path}"
    assert a_map[sandbox_key] != b_map[sandbox_key]


def test_vista_metadata_paths_are_scoped_per_project_and_user():
    """Volume paths key on (project, user) so one tenant cannot read another's."""
    user = make_user()
    a = _agent(make_project(), user)
    b = _agent(make_project(), user)
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
        "list_hpc_jobs",
        "cancel_hpc_job",
    ],
)
def test_vista_metadata_attaches_user_credentials_for_hpc_tools(tool):
    user = make_user(
        odo_s3m_token="odo-s3m-secret",
        frontier_s3m_token="frontier-s3m-secret",
        nersc_iri_token="iri-secret",
        frontier_account="chm243",
    )
    metadata = _agent(make_project(), user)._build_vista_metadata(tool)["vista"]["user"]
    assert metadata["odo_s3m_token"] == "odo-s3m-secret"
    assert metadata["frontier_s3m_token"] == "frontier-s3m-secret"
    # The legacy single token is never sent: the MCP server no longer falls
    # back to it, and a stale copy would disagree with the per-cluster ones.
    assert "s3m_token" not in metadata
    assert metadata["nersc_iri_token"] == "iri-secret"
    assert metadata["frontier_account"] == "chm243"
    assert metadata["id"] == str(user.id)


@pytest.mark.parametrize("tool", ["rag_search", "display_file", "run_bash"])
def test_vista_metadata_withholds_credentials_from_non_hpc_tools(tool):
    user = make_user(odo_s3m_token="s3m-secret", nersc_iri_token="iri-secret")
    metadata = _agent(make_project(), user)._build_vista_metadata(tool)
    assert "user" not in metadata["vista"]
    assert "s3m-secret" not in str(metadata)


def test_vista_metadata_omits_metrics_when_the_recorder_is_off():
    assert not get_recorder().enabled, "metrics recorder should default to off"
    metadata = _agent()._build_vista_metadata("submit_hpc_job")
    assert "metrics" not in metadata["vista"]


# ---------------------------------------------------------------------------
# The filter as the model sees it
# ---------------------------------------------------------------------------


def _tools_offered_to_model(project, user) -> list[str]:
    seen: list[list[str]] = []

    def driver(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append([t.name for t in info.function_tools])
        return ModelResponse(parts=[TextPart("ok")])

    async def run() -> list[str]:
        with agent_under_test(project, user, scripted_model(driver)) as (agent, _):
            async for _ in agent.run_stream(user_prompt="hi"):
                pass
        return seen[0]

    return run()


@pytest.mark.anyio
async def test_denied_tools_are_not_offered_to_the_model():
    project = make_project(tools=["*", "!run_bash"], knowledge_bases=["kb"])
    offered = await _tools_offered_to_model(project, make_user())
    assert "run_bash" not in offered
    assert "rag_search" in offered


@pytest.mark.anyio
async def test_allow_list_limits_the_tools_offered_to_the_model():
    project = make_project(tools=["display_file"], knowledge_bases=["kb"])
    offered = await _tools_offered_to_model(project, make_user())
    assert "display_file" in offered
    assert "rag_search" not in offered
    assert "submit_hpc_job" not in offered


@pytest.mark.anyio
async def test_rag_search_is_hidden_from_the_model_without_a_knowledge_base():
    project = make_project(tools=["*"], knowledge_bases=[])
    offered = await _tools_offered_to_model(project, make_user())
    assert "rag_search" not in offered
    assert "submit_hpc_job" in offered


def test_settings_model_is_the_hermetic_test_model():
    """Guards the conftest default that keeps PR CI from needing an API key."""
    assert settings.model == "test"
