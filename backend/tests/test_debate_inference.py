"""
A debate's agents reach the model with the same credential a chat does.

On a packaged single-user install the inference key is pasted into the
settings modal, so it lives on the researcher's user row and nowhere in the
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
from vista_backend.db.schemas import UserSelfUpdate
from vista_backend.services import user as user_service

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


def test_the_orchestrator_uses_the_openers_settings(no_env_credential):
    """
    The key, endpoint and model typed into the settings modal are the opener's,
    and a debate's three roles use them.
    """
    from vista_backend.agents.forum.wiring import build_orchestrator
    from vista_backend.db.schemas import UserPublicWithConfig
    import uuid

    opener = UserPublicWithConfig(
        id=uuid.uuid4(),
        email="researcher@example.org",
        is_admin=False,
        inference_api_key="sk-from-the-settings-modal",
        inference_base_url="https://user.example/v1",
        inference_model="openai:user-model",
    )

    orchestrator = build_orchestrator(object(), user=opener, grounding=Grounding())

    for role in ROLES:
        model = getattr(orchestrator.roles, role).model
        assert model.client.api_key == "sk-from-the-settings-modal", role
        assert str(model.client.base_url).rstrip("/") == "https://user.example/v1", role
        assert model.model_name == "user-model", role


@pytest.mark.anyio
async def test_a_background_debate_finds_its_openers_settings(
    no_env_credential, session, alice
):
    """
    A debate argues in a background task that has only the run, so it has to
    look the opener up again, credentials included.
    """
    from types import SimpleNamespace

    from vista_backend.agents.forum.wiring import opener_of

    await user_service.update_user(
        session,
        alice.id,
        UserSelfUpdate(inference_api_key="sk-alices-key"),
        user="system",
    )

    opener = await opener_of(session, SimpleNamespace(user_id=alice.id))

    assert opener is not None
    assert opener.inference_api_key == "sk-alices-key"
