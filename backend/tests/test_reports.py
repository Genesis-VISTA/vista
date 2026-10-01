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
from harness import FakeAgentPool, api_client, seed_project, seed_user
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents import report_authoring
from vista_backend.services import files as files_service
from vista_backend.services import reports as reports_service

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

REPORTS = "/projects/{name}/reports"


def _headers(user) -> dict[str, str]:
    return {"X-Vista-User-Email": user.email}


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
async def owner(session):
    alice = await seed_user(session)
    project = await seed_project(session, alice, name="salts")
    return alice, project


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


async def test_first_save_writes_header_and_body(session, uploads, owner):
    alice, project = owner
    chat = uuid.uuid4()

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
            headers=_headers(alice),
        )

    assert response.status_code == 201
    assert response.json() == {"path": "reports/lif-naf-eutectic-density.md"}
    header, body = _split(uploads / "reports" / "lif-naf-eutectic-density.md")
    assert header["title"] == "LiF-NaF eutectic density"
    assert header["summary"] == "Computed the eutectic density at 1000 K."
    assert header["chat_session_id"] == str(chat)
    assert isinstance(header["date"], str)
    assert body.strip().startswith("## Summary")


async def test_saved_report_is_listed_with_uploads(session, uploads, owner):
    alice, project = owner

    with api_client(session) as (client, _):
        await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4()),
            headers=_headers(alice),
        )
        listing = await client.get(
            f"/projects/{project.name}/uploads", headers=_headers(alice)
        )

    assert listing.status_code == 200
    assert [f["name"] for f in listing.json()] == [
        "reports/lif-naf-eutectic-density.md"
    ]


async def test_resave_from_same_chat_replaces_and_keeps_filename(
    session, uploads, owner
):
    alice, project = owner
    chat = uuid.uuid4()

    with api_client(session) as (client, _):
        await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
            headers=_headers(alice),
        )
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(
                chat, title="Revised", slug="something-else", body="## Summary\n\nv2"
            ),
            headers=_headers(alice),
        )

    assert response.json() == {"path": "reports/lif-naf-eutectic-density.md"}
    assert [p.name for p in (uploads / "reports").iterdir()] == [
        "lif-naf-eutectic-density.md"
    ]
    header, body = _split(uploads / "reports" / "lif-naf-eutectic-density.md")
    assert header["title"] == "Revised"
    assert "v2" in body


async def test_resave_after_delete_creates_new_file(session, uploads, owner):
    alice, project = owner
    chat = uuid.uuid4()

    with api_client(session) as (client, _):
        first = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat),
            headers=_headers(alice),
        )
        (uploads / first.json()["path"]).unlink()
        second = await client.post(
            REPORTS.format(name=project.name),
            json=_report(chat, slug="fresh-start"),
            headers=_headers(alice),
        )

    assert second.json() == {"path": "reports/fresh-start.md"}
    assert (uploads / "reports" / "fresh-start.md").is_file()


async def test_slug_collision_with_another_chat_gets_unique_name(
    session, uploads, owner
):
    alice, project = owner

    with api_client(session) as (client, _):
        first = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4()),
            headers=_headers(alice),
        )
        second = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4(), title="Other chat"),
            headers=_headers(alice),
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
    session, uploads, owner, slug, expected
):
    alice, project = owner

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4(), slug=slug),
            headers=_headers(alice),
        )

    assert response.json() == {"path": expected}
    assert (uploads / expected).is_file()


async def test_header_with_yaml_syntax_round_trips(session, uploads, owner):
    alice, project = owner
    title = 'Density: "LiF-NaF" at 1000 K --- #1'

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4(), title=title, summary="a: b\n---\nc"),
            headers=_headers(alice),
        )

    header, _ = _split(uploads / response.json()["path"])
    assert header["title"] == title
    assert header["summary"] == "a: b --- c"


async def test_malformed_report_files_are_skipped(session, uploads, owner):
    alice, project = owner
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
            headers=_headers(alice),
        )

    assert response.json() == {"path": "reports/lif-naf-eutectic-density.md"}
    assert (reports_dir / "notes.md").read_text(encoding="utf-8") == "no header here"


async def test_non_member_cannot_save(session, uploads, owner):
    _, project = owner
    bob = await seed_user(session)

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name),
            json=_report(uuid.uuid4()),
            headers=_headers(bob),
        )

    assert response.status_code == 403
    assert not uploads.exists()


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
def fake_model(monkeypatch) -> list[list]:
    calls: list[list] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, DRAFT)])

    monkeypatch.setattr(
        report_authoring, "build_model_for", lambda user: FunctionModel(respond)
    )
    return calls


async def test_generate_returns_draft_and_persists_nothing(
    session, uploads, owner, fake_model
):
    alice, project = owner

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name) + "/generate",
            json={"message_history": [], "hint": "focus on density"},
            headers=_headers(alice),
        )

    assert response.status_code == 200
    assert response.json() == DRAFT
    assert len(fake_model) == 1
    last = fake_model[0][-1]
    assert isinstance(last, ModelRequest)
    assert "User hint: focus on density" in str(last.parts[-1].content)
    assert not uploads.exists()


async def test_non_member_cannot_generate(session, uploads, owner, fake_model):
    _, project = owner
    bob = await seed_user(session)

    with api_client(session) as (client, _):
        response = await client.post(
            REPORTS.format(name=project.name) + "/generate",
            json={"message_history": []},
            headers=_headers(bob),
        )

    assert response.status_code == 403
    assert fake_model == []
