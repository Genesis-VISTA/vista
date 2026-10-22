"""
HTTP contract for saving and drafting conversation reports (conversation-report).

Runs in-process against the real FastAPI app and DB session with real
authentication. The agent pool is faked so a report lands in a temporary
uploads directory, and drafting runs on a `FunctionModel`, so nothing here
needs a model, a key, or the network.
"""

import uuid
from types import SimpleNamespace

import pytest
import yaml
from harness import FakeAgentPool, api_client, seed_project
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents import report_authoring
from vista_backend.services import files as files_service
from vista_backend.services import reports as reports_service

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

REPORTS = "/projects/{name}/reports"


def _report(chat_session_id: uuid.UUID, **overrides) -> dict:
    report = {
        "title": "LiF-NaF eutectic density",
        "summary": "Computed the eutectic density at 1000 K.",
        "slug": "lif-naf-eutectic-density",
        "body": "## Summary\n\nDensity found.\n\n## Record\n\n- rho = 1.95 g/cm3",
        "chat_session_id": str(chat_session_id),
    }
    return report | overrides


def _split(path) -> tuple[dict, str]:
    """A saved report's YAML header and body."""
    text = path.read_text(encoding="utf-8")
    _, header, body = text.split("---\n", 2)
    return yaml.safe_load(header), body


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """Point both the reports and files services at one temporary uploads dir."""
    uploads_dir = tmp_path / "uploads"
    agent = SimpleNamespace(uploads_dir=uploads_dir, output_dir=tmp_path / "output")
    pool = FakeAgentPool(agent=agent)
    monkeypatch.setattr(reports_service, "project_agent_pool", pool)
    monkeypatch.setattr(files_service, "project_agent_pool", pool)
    return uploads_dir


@pytest.fixture
async def project(session):
    return await seed_project(session, name="salts")


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


async def test_first_save_writes_header_and_body(session, uploads, project):
    chat = uuid.uuid4()

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
        )

    assert response.status_code == 201
    assert response.json() == {"path": "reports/lif-naf-eutectic-density.md"}
    header, body = _split(uploads / "reports" / "lif-naf-eutectic-density.md")
    assert header["title"] == "LiF-NaF eutectic density"
    assert header["summary"] == "Computed the eutectic density at 1000 K."
    assert header["chat_session_id"] == str(chat)
    assert isinstance(header["date"], str)
    assert body.strip().startswith("## Summary")


async def test_saved_report_is_listed_with_uploads(session, uploads, project):

    with api_client(session) as (client, _):
        await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4()),
        )
        listing = await client.get(f"/projects/{project.name}/uploads")

    assert listing.status_code == 200
    assert [f["name"] for f in listing.json()] == [
        "reports/lif-naf-eutectic-density.md"
    ]


async def test_resave_from_same_chat_replaces_and_keeps_filename(
    session, uploads, project
):
    chat = uuid.uuid4()

    with api_client(session) as (client, _):
        await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
        )
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(
                chat, title="Revised", slug="something-else", body="## Summary\n\nv2"
            ),
        )

    assert response.json() == {"path": "reports/lif-naf-eutectic-density.md"}
    assert [p.name for p in (uploads / "reports").iterdir()] == [
        "lif-naf-eutectic-density.md"
    ]
    header, body = _split(uploads / "reports" / "lif-naf-eutectic-density.md")
    assert header["title"] == "Revised"
    assert "v2" in body


async def test_resave_after_delete_creates_new_file(session, uploads, project):
    chat = uuid.uuid4()

    with api_client(session) as (client, _):
        first = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
        )
        (uploads / first.json()["path"]).unlink()
        second = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat, slug="fresh-start"),
        )

    assert second.json() == {"path": "reports/fresh-start.md"}
    assert (uploads / "reports" / "fresh-start.md").is_file()


async def test_slug_collision_with_another_chat_gets_unique_name(
    session, uploads, project
):

    with api_client(session) as (client, _):
        first = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4()),
        )
        second = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4(), title="Other chat"),
        )

    assert first.json()["path"] == "reports/lif-naf-eutectic-density.md"
    assert second.json()["path"] == "reports/lif-naf-eutectic-density-1.md"
    header, _ = _split(uploads / first.json()["path"])
    assert header["title"] == "LiF-NaF eutectic density"


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("../../etc/passwd", "reports/etc-passwd.md"),
        ("a/b\\c", "reports/a-b-c.md"),
        ("...", "reports/report.md"),
        ("Molten Salt: LiF!", "reports/molten-salt-lif.md"),
    ],
)
async def test_unsafe_slug_stays_inside_reports(
    session, uploads, project, slug, expected
):

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4(), slug=slug),
        )

    assert response.json() == {"path": expected}
    assert (uploads / expected).is_file()


async def test_header_with_yaml_syntax_round_trips(session, uploads, project):
    title = 'Density: "LiF-NaF" at 1000 K --- #1'

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4(), title=title, summary="a: b\n---\nc"),
        )

    header, _ = _split(uploads / response.json()["path"])
    assert header["title"] == title
    assert header["summary"] == "a: b --- c"


async def test_malformed_report_files_are_skipped(session, uploads, project):
    chat = uuid.uuid4()
    reports_dir = uploads / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "notes.md").write_text("no header here", encoding="utf-8")
    (reports_dir / "broken.md").write_text("---\n: [\n---\n", encoding="utf-8")
    (reports_dir / "binary.md").write_bytes(b"\xff\xfe\x00")

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
        )

    assert response.json() == {"path": "reports/lif-naf-eutectic-density.md"}
    assert (reports_dir / "notes.md").read_text(encoding="utf-8") == "no header here"


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


DRAFT = {
    "title": "LiF-NaF eutectic density",
    "slug_suggestion": "lif-naf-eutectic-density",
    "summary": "Computed the eutectic density at 1000 K.",
    "body": "## Summary\n\nDensity found.\n\n## Record\n\n- rho = 1.95 g/cm3",
}


@pytest.fixture
def built_with() -> list[str | None]:
    """The model override each draft asked `build_model_for` for."""
    return []


@pytest.fixture
def fake_model(monkeypatch, built_with) -> list[list]:
    calls: list[list] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, DRAFT)])

    def build(local, model=None):
        built_with.append(model)
        return FunctionModel(respond)

    monkeypatch.setattr(report_authoring, "build_model_for", build)
    return calls


async def test_generate_returns_draft_and_persists_nothing(
    session, uploads, project, fake_model
):

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name) + "/generate",
            json={"message_history": [], "hint": "focus on density"},
        )

    assert response.status_code == 200
    assert response.json() == DRAFT
    assert len(fake_model) == 1
    last = fake_model[0][-1]
    assert isinstance(last, ModelRequest)
    assert "User hint: focus on density" in str(last.parts[-1].content)
    assert not uploads.exists()


async def _chat(session, project, model=None):
    from vista_backend.db.schemas import ChatSessionUpdate
    from vista_backend.services import chat_session as chat_session_service

    chat = await chat_session_service.create_chat_session(
        session, project_id=project.id
    )
    if model is not None:
        await chat_session_service.update_chat_session(
            session,
            project_id=project.id,
            chat_session_id=chat.id,
            updates=ChatSessionUpdate(inference_model=model),
        )
    return chat


async def _generate(session, project, **body):
    with api_client(session) as (client, _):
        return await client.post(
            REPORTS.format(name=project.name) + "/generate",
            json={"message_history": [], **body},
        )


async def test_draft_from_an_overridden_chat_uses_its_model(
    session, uploads, project, fake_model, built_with
):
    chat = await _chat(session, project, "openai:chat-choice")

    response = await _generate(session, project, chat_session_id=str(chat.id))

    assert response.status_code == 200, response.text
    assert built_with == ["openai:chat-choice"]


async def test_draft_without_a_chat_or_from_a_default_chat_uses_the_default(
    session, uploads, project, fake_model, built_with
):
    chat = await _chat(session, project)

    assert (await _generate(session, project)).status_code == 200
    response = await _generate(session, project, chat_session_id=str(chat.id))

    assert response.status_code == 200
    assert built_with == [None, None]


async def test_draft_with_a_missing_or_another_projects_chat_is_a_404(
    session, uploads, project, fake_model, built_with
):
    other_project = await seed_project(session, name="other-project")
    elsewhere = await _chat(session, other_project, "openai:elsewhere")

    for chat_id in (uuid.uuid4(), elsewhere.id):
        response = await _generate(session, project, chat_session_id=str(chat_id))
        assert response.status_code == 404, response.text

    assert fake_model == [] and built_with == []
