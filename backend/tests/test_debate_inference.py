"""
A debate's agents reach the model with the same credential a chat does.

On a packaged install the inference key is pasted into the settings modal,
so it lives on the installation's settings row and nowhere in the
environment. The Hypothesis Lab built its role agents with a bare
`infer_model`, which reads only `OPENAI_API_KEY` / `OPENAI_BASE_URL`, so
opening a debate there failed with a 500 from `OpenAIError: Missing
credentials` while chat, which goes through `agents/inference.py`, worked.

Every test here scrubs the environment first. `config.py` loads each `.env`
from the filesystem root down to the working directory, so a developer's own
key would otherwise be picked up and hide exactly this failure.
"""

import pytest
from pydantic import SecretStr

from vista_backend.agents.forum.grounding import Grounding
from vista_backend.agents.forum.roles import RoleAgents
from vista_backend.config import settings
from vista_backend.db.schemas import LocalSettingsUpdate
from vista_backend.services import local_settings

pytestmark = pytest.mark.unit

ROLES = ("proposer", "reviewer", "referee")


@pytest.fixture
def no_env_credential(monkeypatch):
    """Neither the process environment nor Settings supplies a credential."""
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "openai_base_url", "https://deployment.example/v1")
    monkeypatch.setattr(settings, "model", "openai:deployment-model")


def _clients(roles: RoleAgents):
    return {role: getattr(roles, role).model.client for role in ROLES}


def test_roles_use_the_configured_credential_not_only_the_environment(
    no_env_credential, monkeypatch
):
    """
    `VISTA_BACKEND_OPENAI_API_KEY` and a deployment's `openai_base_url` reach
    `Settings` without being `OPENAI_*` in the environment. A role must use
    them, as chat does, rather than fail to build.
    """
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("from-settings"))

    clients = _clients(RoleAgents())

    for role, client in clients.items():
        assert client.api_key == "from-settings", role
        assert str(client.base_url).rstrip("/") == "https://deployment.example/v1", role


def test_the_orchestrator_uses_the_settings(no_env_credential):
    """
    The key, endpoint and model typed into the settings modal are the ones a
    debate's three roles use.
    """
    from vista_backend.agents.forum.wiring import build_orchestrator
    from vista_backend.db.schemas import LocalSettingsPublic
    import uuid

    local = LocalSettingsPublic(
        install_id=uuid.uuid4(),
        inference_provider="custom",
        inference_custom_api_key="sk-from-the-settings-modal",
        inference_base_url="https://user.example/v1",
        inference_model="openai:user-model",
    )

    orchestrator = build_orchestrator(object(), local=local, grounding=Grounding())

    for role in ROLES:
        model = getattr(orchestrator.roles, role).model
        assert model.client.api_key == "sk-from-the-settings-modal", role
        assert str(model.client.base_url).rstrip("/") == "https://user.example/v1", role
        assert model.model_name == "user-model", role


@pytest.mark.anyio
async def test_a_background_debate_reads_the_settings(no_env_credential, session):
    """
    A debate argues in a background task that has only the run, so it has to
    read the settings again, credentials included.
    """

    await local_settings.update(
        session, LocalSettingsUpdate(inference_api_key="sk-the-researchers-key")
    )

    local = await local_settings.get_public(session)

    assert local.inference_api_key == "sk-the-researchers-key"


def _custom_settings():
    import uuid

    from vista_backend.db.schemas import LocalSettingsPublic

    return LocalSettingsPublic(
        install_id=uuid.uuid4(),
        inference_provider="custom",
        inference_custom_api_key="sk-settings",
        inference_base_url="https://user.example/v1",
        inference_model="openai:default-a",
    )


def test_a_role_with_its_own_model_runs_on_it_and_the_others_on_the_default(
    no_env_credential,
):
    from vista_backend.agents.forum.wiring import build_orchestrator

    orchestrator = build_orchestrator(
        object(),
        local=_custom_settings(),
        grounding=Grounding(),
        models={"referee": "openai:referee-b"},
    )

    names = {r: getattr(orchestrator.roles, r).model.model_name for r in ROLES}
    assert names == {
        "proposer": "default-a",
        "reviewer": "default-a",
        "referee": "referee-b",
    }
    # Provider, endpoint and key stay the settings'.
    referee = orchestrator.roles.referee.model
    assert referee.client.api_key == "sk-settings"
    assert str(referee.client.base_url).rstrip("/") == "https://user.example/v1"
    # What each post will record is the resolved model.
    assert orchestrator.model_names == {
        "proposer": "openai:default-a",
        "reviewer": "openai:default-a",
        "referee": "openai:referee-b",
    }


@pytest.mark.anyio
async def test_a_continued_debate_keeps_its_roles_models(no_env_credential, session):
    """
    The background task rebuilds the orchestrator from the stored roster, so
    continuing without changes runs the Referee on B again.
    """
    from vista_backend.agents.forum.wiring import build_orchestrator, models_for
    from vista_backend.db.schemas import ProjectTable
    from vista_backend.services import debate as debate_service
    from vista_backend.services.forum_git import Participant

    project = ProjectTable(name="models-for")
    session.add(project)
    await session.flush()
    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        topic="t",
        thread_id="thread",
        framing=None,
        rounds=1,
    )
    for role in ROLES:
        await debate_service.add_participant(
            session,
            run_id=run.id,
            participant=Participant(identity=f"vista-{role}", role=role),
            debate_role=role,
            model="openai:referee-b" if role == "referee" else None,
        )

    stored = await models_for(session, run)
    assert stored == {
        "proposer": None,
        "reviewer": None,
        "referee": "openai:referee-b",
    }

    orchestrator = build_orchestrator(
        object(), local=_custom_settings(), grounding=Grounding(), models=stored
    )
    assert orchestrator.roles.referee.model.model_name == "referee-b"
    assert orchestrator.roles.proposer.model.model_name == "default-a"
