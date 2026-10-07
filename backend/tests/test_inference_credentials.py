"""
The inference credential comes from the running application, not a file.

On a packaged single-user install the researcher's key is pasted into the
settings modal, so it lands on their user row rather than in the environment.
These tests pin the resolution order, that the stored value is ciphertext on
disk, that an absent key is a named condition rather than a traceback, and
that a change takes effect without a restart.
"""

import sqlite3
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.agents.inference import (
    PROVIDER_PRESETS,
    MissingInferenceCredential,
    MissingInferenceModel,
    SETTINGS_LOCATION,
    build_inference_model,
    build_model_for,
    require_inference_credential,
    resolve_inference_target,
)
from vista_backend.config import settings
from vista_backend.db.schemas import (
    UserPublicWithConfig,
    UserSelfUpdate,
    UserTable,
)
from vista_backend.utils.crypto import get_fernet

pytestmark = pytest.mark.unit


async def _dummy_agent() -> object:
    """Stand-in for a built `ProjectAgent`; the eviction path never uses it."""
    return object()


async def _noop_cleanup(_agent: object) -> None:
    return None


def _user(**overrides) -> UserPublicWithConfig:
    return UserPublicWithConfig(
        id=uuid.uuid4(), email="r@example.org", is_admin=True, **overrides
    )


# ---------------------------------------------------------------------------
# 2.2 — resolution order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row_key,env_key,expected",
    [
        ("from-row", "from-env", "from-row"),  # row wins
        ("from-row", None, "from-row"),
        (None, "from-env", "from-env"),  # environment fills in
        (None, None, None),  # nothing configured
    ],
    ids=["both", "row-only", "env-only", "neither"],
)
def test_credential_precedence(monkeypatch, row_key, env_key, expected):
    """
    The row wins because on a single-user install it is the only surface the
    researcher can reach without editing files: a value typed into the
    settings modal has to beat a stale exported one.
    """
    monkeypatch.setattr(
        settings, "openai_api_key", SecretStr(env_key) if env_key else None
    )
    target = resolve_inference_target(_user(inference_api_key=row_key))
    assert target.api_key == expected
    assert target.has_credential is (expected is not None)


def test_model_and_endpoint_precedence(monkeypatch):
    monkeypatch.setattr(settings, "model", "openai:server-default")
    monkeypatch.setattr(settings, "openai_base_url", "https://server.example/v1")

    fallback = resolve_inference_target(_user())
    assert fallback.model == "openai:server-default"
    assert fallback.base_url == "https://server.example/v1"

    overridden = resolve_inference_target(
        _user(
            inference_provider="custom",
            inference_model="openai:user-choice",
            inference_base_url="https://user.example/v1",
        )
    )
    assert overridden.model == "openai:user-choice"
    assert overridden.base_url == "https://user.example/v1"


def test_no_user_falls_back_to_settings(monkeypatch):
    """Background callers with no request user still resolve a target."""
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("from-env"))
    assert resolve_inference_target(None).api_key == "from-env"


def test_resolved_target_reaches_the_built_client(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    target = resolve_inference_target(
        _user(
            inference_provider="custom",
            inference_model="openai:claude-sonnet",
            inference_base_url="https://user.example/v1",
            inference_custom_api_key="row-key",
        )
    )
    model = build_inference_model(
        target.model, api_key=target.api_key, base_url=target.base_url
    )
    assert str(model.client.base_url).rstrip("/") == "https://user.example/v1"
    assert model.client.api_key == "row-key"


# ---------------------------------------------------------------------------
# Provider presets and the provider-aware resolution order
# ---------------------------------------------------------------------------


@pytest.fixture
def i2_settings(monkeypatch):
    """An installation whose own configuration is the i2 preset's, keyless."""
    monkeypatch.setattr(settings, "model", "openai:claude-sonnet")
    monkeypatch.setattr(
        settings, "openai_base_url", "https://api.i2-core.american-science-cloud.org"
    )
    monkeypatch.setattr(settings, "openai_api_key", None)


def test_presets():
    i2, mag, olcf, custom = (
        PROVIDER_PRESETS[p] for p in ("i2", "mag", "olcf", "custom")
    )
    assert list(PROVIDER_PRESETS) == ["i2", "mag", "olcf", "custom"]
    assert i2.base_url == "https://api.i2-core.american-science-cloud.org"
    assert i2.default_model == "claude-sonnet"
    assert mag.base_url == "https://i2-api.staging.american-science-cloud.org/v1"
    assert mag.default_model is None
    assert olcf.name == "OLCF Inference"
    assert olcf.base_url == "https://s3m.olcf.ornl.gov/olcf/open/v1/inference"
    assert olcf.default_model == "gpt-oss-120b"
    assert custom.takes_url and custom.default_model is None
    assert not i2.takes_url and not mag.takes_url and not olcf.takes_url


_ALL_KEYS = dict(
    inference_api_key="i2-key",
    inference_mag_api_key="mag-key",
    inference_olcf_api_key="olcf-key",
    inference_custom_api_key="custom-key",
    inference_base_url="https://custom.example/v1",
)


@pytest.mark.parametrize(
    "provider,base_url,key,model",
    [
        ("i2", PROVIDER_PRESETS["i2"].base_url, "i2-key", "openai:claude-sonnet"),
        ("mag", PROVIDER_PRESETS["mag"].base_url, "mag-key", None),
        ("olcf", PROVIDER_PRESETS["olcf"].base_url, "olcf-key", "openai:gpt-oss-120b"),
        ("custom", "https://custom.example/v1", "custom-key", None),
    ],
)
def test_row_provider_picks_its_url_key_and_default(
    i2_settings, provider, base_url, key, model
):
    """Each provider uses its own key, so switching never loses another's."""
    target = resolve_inference_target(_user(inference_provider=provider, **_ALL_KEYS))
    assert target.provider == provider
    assert target.source == "user"
    assert target.base_url == base_url
    assert target.api_key == key
    assert target.model == model
    assert target.model_is_default is (model is not None)


def test_olcf_uses_its_own_key_not_the_cluster_s3m_tokens(i2_settings):
    """A compute token is not known to be accepted for inference."""
    cluster_tokens = dict(odo_s3m_token="odo-s3m", frontier_s3m_token="frontier-s3m")
    target = resolve_inference_target(
        _user(inference_provider="olcf", **cluster_tokens)
    )
    assert target.api_key is None
    target = resolve_inference_target(
        _user(
            inference_provider="olcf",
            inference_olcf_api_key="olcf-key",
            **cluster_tokens,
        )
    )
    assert target.api_key == "olcf-key"


def test_row_provider_uses_the_chosen_model(i2_settings):
    target = resolve_inference_target(
        _user(inference_provider="mag", inference_model="openai:gpt-oss", **_ALL_KEYS)
    )
    assert target.model == "openai:gpt-oss"
    assert target.model_is_default is False


def test_i2_key_falls_back_to_the_environment(i2_settings, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("from-env"))
    assert (
        resolve_inference_target(_user(inference_provider="i2")).api_key == "from-env"
    )
    # Not to another provider: the environment's key is i2's.
    assert resolve_inference_target(_user(inference_provider="mag")).api_key is None


def test_nothing_set_is_i2_with_its_default(i2_settings):
    target = resolve_inference_target(_user())
    assert target.provider == "i2"
    assert target.source == "default"
    assert target.model == "openai:claude-sonnet"
    assert target.model_is_default is True
    assert target.base_url == PROVIDER_PRESETS["i2"].base_url


def test_existing_key_is_the_i2_key(i2_settings):
    """A key saved before providers existed keeps working, as i2's."""
    target = resolve_inference_target(
        _user(inference_api_key="saved-before", inference_base_url="https://old/v1")
    )
    assert target.provider == "i2"
    assert target.api_key == "saved-before"
    # The old endpoint column is Custom's now, and unused for i2.
    assert target.base_url == PROVIDER_PRESETS["i2"].base_url


@pytest.mark.parametrize(
    "field,value",
    [("openai_base_url", "https://dev.example/v1"), ("model", "openai:other")],
)
def test_configuration_differing_from_i2_is_custom_from_config(
    i2_settings, monkeypatch, field, value
):
    monkeypatch.setattr(settings, field, value)
    target = resolve_inference_target(_user(inference_api_key="row-key"))
    assert target.provider == "custom"
    assert target.source == "config"
    assert target.base_url == settings.openai_base_url
    assert target.model == settings.model
    assert target.api_key == "row-key"


def test_a_chosen_provider_beats_the_configuration(i2_settings, monkeypatch):
    monkeypatch.setattr(settings, "openai_base_url", "https://dev.example/v1")
    target = resolve_inference_target(_user(inference_provider="mag", **_ALL_KEYS))
    assert target.provider == "mag"
    assert target.base_url == PROVIDER_PRESETS["mag"].base_url


def test_no_model_is_a_named_condition(i2_settings):
    with pytest.raises(MissingInferenceModel) as excinfo:
        require_inference_credential(
            _user(inference_provider="mag", inference_mag_api_key="t")
        )
    assert "AmSC MAG" in excinfo.value.detail
    assert "model picker" in excinfo.value.detail


def test_no_model_still_builds_an_agent_model(i2_settings):
    """
    Agents are built before anything reaches the model, so building must not
    fail; using the built model raises the named condition.
    """
    from pydantic_ai import Agent

    model = build_model_for(_user(inference_provider="mag", inference_mag_api_key="t"))
    with pytest.raises(MissingInferenceModel):
        Agent(model).run_sync("hi")


@pytest.mark.anyio
async def test_new_keys_are_not_plaintext_in_the_database_file(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", SecretStr(key))
    get_fernet.cache_clear()
    secrets = {
        "inference_mag_api_key": "mag-do-not-store-in-the-clear",
        "inference_olcf_api_key": "olcf-do-not-store-in-the-clear",
        "inference_custom_api_key": "custom-do-not-store-in-the-clear",
    }
    try:
        db_file = tmp_path / "vista.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as s:
            s.add(UserTable(email="r@example.org", is_admin=True, **secrets))
            await s.commit()
        await engine.dispose()

        raw = db_file.read_bytes()
        for secret in secrets.values():
            assert secret.encode() not in raw
    finally:
        get_fernet.cache_clear()


# ---------------------------------------------------------------------------
# 2.3 — an absent credential is a named condition
# ---------------------------------------------------------------------------


def test_missing_credential_names_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:claude-sonnet")
    with pytest.raises(MissingInferenceCredential) as excinfo:
        require_inference_credential(_user())
    detail = excinfo.value.detail
    assert SETTINGS_LOCATION in detail
    assert "claude-sonnet" in detail
    # Must not read as a fault: it says what still works.
    assert "knowledge bases" in detail.lower()


def test_present_credential_passes_the_guard(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    target = require_inference_credential(_user(inference_api_key="row-key"))
    assert target.api_key == "row-key"


@pytest.mark.parametrize(
    "model", ["test", "ollama:qwen3", "anthropic:claude-sonnet-4-5"]
)
def test_guard_only_applies_to_our_endpoint(monkeypatch, model):
    """
    A model that does not reach `openai_base_url` needs no key from us, so
    demanding one would refuse work that would have succeeded.
    """
    monkeypatch.setattr(settings, "openai_api_key", None)
    assert require_inference_credential(_user(inference_model=model)).api_key is None


# ---------------------------------------------------------------------------
# 2.1 — ciphertext at rest
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_key_is_not_plaintext_in_the_database_file(tmp_path, monkeypatch):
    """
    Read the value back with the `sqlite3` module rather than through
    SQLAlchemy, so the `EncryptedStr` decrypt never runs and the assertion is
    about the bytes actually on disk.
    """
    secret = "sk-do-not-store-me-in-the-clear"
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", SecretStr(key))
    get_fernet.cache_clear()
    try:
        db_file = tmp_path / "vista.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with AsyncSession(engine) as s:
            s.add(
                UserTable(
                    email="r@example.org",
                    is_admin=True,
                    inference_api_key=secret,
                )
            )
            await s.commit()
        await engine.dispose()

        assert secret.encode() not in db_file.read_bytes()

        with sqlite3.connect(db_file) as raw:
            (stored,) = raw.execute("SELECT inference_api_key FROM app_user").fetchone()
        assert stored != secret
        assert Fernet(key.encode()).decrypt(stored.encode()).decode() == secret
    finally:
        get_fernet.cache_clear()


# ---------------------------------------------------------------------------
# 2.4 — a change takes effect without a restart
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_updating_the_key_evicts_the_pooled_agent(session, alice, monkeypatch):
    """
    The pool caches a built agent for 30 minutes, so without eviction a new
    key would not be used until it expired. `update_user` already registers an
    after-commit invalidation; this pins that the inference key travels that
    same path.
    """
    from vista_backend.services import project_agent as project_agent_service
    from vista_backend.services import user as user_service

    pool = project_agent_service.project_agent_pool
    key = (None, uuid.uuid4(), alice.id)
    # Populate the pool through its own API with a stand-in agent: building a
    # real one needs a live MCP server, and the eviction path under test does
    # not care what the value is.
    monkeypatch.setattr(pool, "_factory", lambda k: _dummy_agent())
    monkeypatch.setattr(pool, "_cleanup", _noop_cleanup)
    async with pool.get(key):
        pass
    assert key in pool.keys()

    try:
        await user_service.update_user(
            session,
            alice.id,
            UserSelfUpdate(inference_api_key="a-new-key"),
            user="system",
        )
        await session.commit()

        assert key not in pool.keys(), (
            "committing a user update must evict that user's pooled agents, "
            "or a new key is ignored until the 30-minute TTL expires"
        )
    finally:
        pool.delete(key)

    row = (
        await session.exec(select(UserTable).where(UserTable.id == alice.id))
    ).first()
    assert row is not None and row.inference_api_key == "a-new-key"


@pytest.mark.anyio
async def test_clearing_the_key_writes_null_not_empty_string(session, alice):
    """
    The modal sends `null` for a blanked field, and `""` must be coerced too —
    an empty-but-not-null key would satisfy `has_credential` and produce a
    provider 401 instead of the guidance message.
    """
    from vista_backend.services import user as user_service

    await user_service.update_user(
        session, alice.id, UserSelfUpdate(inference_api_key="k"), user="system"
    )
    await user_service.update_user(
        session, alice.id, UserSelfUpdate(inference_api_key=""), user="system"
    )
    row = (
        await session.exec(select(UserTable).where(UserTable.id == alice.id))
    ).first()
    assert row is not None and row.inference_api_key is None


# ---------------------------------------------------------------------------
# The field that nothing read
# ---------------------------------------------------------------------------


def test_the_globus_credential_is_read_by_something():
    """
    Inverted, and for the same reason it was written. It asserted that
    `globus_token` must not be offered because nothing read it -- a field whose
    value no component consumes is a field that discards it.

    The MCP server now declares every Globus field and resolves a file
    operation's credential through them, so the reason no longer holds and the
    credential can be offered. What the interface offers is an authorization
    rather than a box to type a token into, which is why this asserts the
    reading side; the interface side is asserted where that control is built.

    Both halves of each credential are checked. The collection tokens are the
    newer half and the easier to add on one side only -- stored by the backend
    and never read, they would be exactly the discarded field this test was
    written about.
    """
    user_config = (
        Path(__file__).resolve().parents[2]
        / "mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/user_config.py"
    ).read_text(encoding="utf-8")

    for field in (
        "odo_globus_token",
        "frontier_globus_token",
        "globus_token",
        "odo_globus_https_token",
        "frontier_globus_https_token",
        "globus_https_token",
    ):
        assert field in user_config, field
    assert "def require_globus_token" in user_config


# ---------------------------------------------------------------------------
# 2.3 / 2.7 — the HTTP surface
# ---------------------------------------------------------------------------


class TestHttpSurface:
    """
    Exercised against the real FastAPI app so the status code and the response
    shape are the ones a browser actually receives.
    """

    RUN = "/projects/{name}/agent/run"

    @staticmethod
    def _no_credential(monkeypatch) -> None:
        """A fresh install: our endpoint is the target and nothing supplies a key."""
        monkeypatch.setattr(settings, "model", "openai:claude-sonnet")
        monkeypatch.setattr(settings, "openai_api_key", None)

    @pytest.mark.anyio
    @pytest.mark.integration
    @pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
    async def test_chat_with_no_credential_is_reported_not_crashed(
        self, session, monkeypatch, stream
    ):
        from harness import api_client, seed_project, seed_user

        self._no_credential(monkeypatch)
        alice = await seed_user(session)
        project = await seed_project(session, alice, name="alices-project")

        with api_client(session) as (client, _):
            response = await client.post(
                self.RUN.format(name=project.name),
                json={"user_prompt": "hi", "stream": stream},
                headers={"X-Vista-User-Email": alice.email},
            )

        assert response.status_code < 500, response.text
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert SETTINGS_LOCATION in detail
        assert "claude-sonnet" in detail

    @pytest.mark.anyio
    @pytest.mark.integration
    @pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
    async def test_chat_on_mag_with_no_model_is_reported_not_crashed(
        self, session, monkeypatch, stream
    ):
        from harness import api_client, seed_project, seed_user

        self._no_credential(monkeypatch)
        alice = await seed_user(session)
        row = await session.get(UserTable, alice.id)
        assert row is not None
        row.inference_provider = "mag"
        row.inference_mag_api_key = "mag-token"
        session.add(row)
        await session.flush()
        project = await seed_project(session, alice, name="alices-project")

        with api_client(session) as (client, _):
            response = await client.post(
                self.RUN.format(name=project.name),
                json={"user_prompt": "hi", "stream": stream},
                headers={"X-Vista-User-Email": alice.email},
            )

        assert response.status_code == 409, response.text
        assert "AmSC MAG" in response.json()["detail"]
        assert "model picker" in response.json()["detail"]

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_chat_succeeds_once_the_key_is_on_the_row(self, session, monkeypatch):
        """
        The other half of 2.3: with a key present the guard is transparent and
        the run reaches the (scripted) model.
        """
        from harness import (
            agent_under_test,
            api_client,
            say,
            seed_project,
            seed_user,
            step_model,
        )

        self._no_credential(monkeypatch)
        alice = await seed_user(session)
        row = (
            await session.exec(select(UserTable).where(UserTable.id == alice.id))
        ).first()
        assert row is not None
        row.inference_api_key = "row-key"
        session.add(row)
        await session.flush()
        # Re-read: `ProjectAgent` snapshots the user at construction, and in
        # production the pool is evicted on every user write so the snapshot is
        # always current. A stale object here would trip the agent's own guard.
        alice = UserPublicWithConfig.model_validate(row)

        project = await seed_project(session, alice, name="alices-project")
        with agent_under_test(project, alice, step_model([say("done")])) as (agent, _):
            with api_client(session, agent=agent) as (client, _):
                response = await client.post(
                    self.RUN.format(name=project.name),
                    json={"user_prompt": "hi"},
                    headers={"X-Vista-User-Email": alice.email},
                )

        assert response.status_code == 200, response.text

    @pytest.mark.anyio
    @pytest.mark.integration
    @pytest.mark.parametrize(
        "path", ["/projects", "/skills", "/knowledge-bases"], ids=lambda p: p.strip("/")
    )
    async def test_non_inference_features_stay_available(
        self, session, monkeypatch, path
    ):
        """
        2.7. These share the app with chat but never reach the model, so a
        missing credential must not gate them — a researcher has to be able to
        look around before they have a key.
        """
        from harness import api_client, seed_project, seed_user

        self._no_credential(monkeypatch)
        alice = await seed_user(session)
        await seed_project(session, alice, name="alices-project")

        with api_client(session) as (client, _):
            response = await client.get(
                path, headers={"X-Vista-User-Email": alice.email}
            )

        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# A credential the provider refuses
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.integration
async def test_rejected_credential_is_reported_not_a_broken_stream(session):
    """
    A typo'd key is the likeliest first-run failure, and it can only be
    discovered by asking the provider — after the response has started. It has
    to arrive as a message naming the setting, on the same path VISTAGuard's
    refusal uses, rather than as a 500 or a truncated stream.
    """
    from pydantic_ai.exceptions import ModelHTTPError

    from harness import (
        agent_under_test,
        api_client,
        parse_sse,
        scripted_model,
        seed_project,
        seed_user,
    )
    from vista_backend.agents.inference import rejected_credential_detail

    alice = await seed_user(session)
    project = await seed_project(session, alice, name="alices-project")

    from vista_backend.services import chat_session as chat_session_service

    conversation = await chat_session_service.create_chat_session(
        session, project_id=project.id, user_id=alice.id
    )
    conversation_id = str(conversation.id)
    await session.commit()

    def reject(messages, info):
        raise ModelHTTPError(status_code=401, model_name="claude-sonnet")

    with agent_under_test(project, alice, scripted_model(reject)) as (agent, _):
        with api_client(session, agent=agent) as (client, _):
            response = await client.post(
                "/projects/alices-project/agent/run",
                json={
                    "user_prompt": "hi",
                    "stream": True,
                    "chat_session_id": conversation_id,
                },
                headers={"X-Vista-User-Email": alice.email},
            )

    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    kinds = [name for name, _ in events]
    assert "agent_run_result" in kinds, kinds
    body = response.text
    assert SETTINGS_LOCATION in body
    assert rejected_credential_detail("claude-sonnet")[:40] in body


@pytest.mark.anyio
@pytest.mark.integration
async def test_other_provider_errors_still_propagate(session):
    """
    A 500 from the endpoint is an outage, not a configuration problem, and
    must not be described as one.
    """
    from pydantic_ai.exceptions import ModelHTTPError

    from harness import agent_under_test, scripted_model, seed_project, seed_user

    alice = await seed_user(session)
    project = await seed_project(session, alice, name="alices-project")

    def outage(messages, info):
        raise ModelHTTPError(status_code=503, model_name="claude-sonnet")

    with agent_under_test(project, alice, scripted_model(outage)) as (agent, _):
        with pytest.raises(ModelHTTPError) as excinfo:
            async for _ in agent.run_stream(user_prompt="hi"):
                pass
    assert excinfo.value.status_code == 503


class TestCitationCredentials:
    """
    Citation extraction has to see a key entered in the settings modal.

    `build_rag` resolves its LLM from the process environment, which cannot
    reach an encrypted value in the user's row. Without the carrier built here,
    a researcher on a fresh install uploads PDFs, gets searchable chunks, and
    silently gets no titles, authors, or DOIs.
    """

    @staticmethod
    def _scrubbed(monkeypatch):
        """Neither the environment nor Settings supplies a credential."""
        for name in (
            "OPENAI_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_DEPLOYMENT_NAME",
            "ENDPOINT_URL",
            "DEPLOYMENT_NAME",
            "OPENAI_MODEL",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(settings, "openai_api_key", None)

    def test_no_credential_anywhere_disables_extraction(self, monkeypatch):
        from vista_backend.agents.inference import citation_credentials
        from vista_backend.utils.indexer import has_llm_credentials

        self._scrubbed(monkeypatch)
        credentials = citation_credentials(None)
        assert credentials.is_usable is False
        assert has_llm_credentials(credentials) is False

    def test_row_credential_enables_extraction(self, monkeypatch):
        from vista_backend.agents.inference import citation_credentials
        from vista_backend.utils.indexer import has_llm_credentials

        self._scrubbed(monkeypatch)
        user = UserPublicWithConfig(
            id=uuid.uuid4(),
            email="researcher@example.org",
            is_admin=False,
            inference_provider="custom",
            inference_custom_api_key="sk-from-the-settings-modal",
            inference_base_url="https://endpoint.example/v1",
            inference_model="openai:my-model",
        )
        credentials = citation_credentials(user)
        assert credentials.is_usable is True
        assert has_llm_credentials(credentials) is True
        assert credentials.api_key == "sk-from-the-settings-modal"
        assert credentials.base_url == "https://endpoint.example/v1"

    def test_no_model_disables_extraction_despite_the_environment(self, monkeypatch):
        """
        On MAG with no model there is nothing to extract with. An environment
        key must not turn extraction back on against some other model.
        """
        from vista_backend.agents.inference import citation_credentials
        from vista_backend.utils.indexer import has_llm_credentials

        self._scrubbed(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        user = UserPublicWithConfig(
            id=uuid.uuid4(),
            email="researcher@example.org",
            is_admin=False,
            inference_provider="mag",
            inference_mag_api_key="mag-token",
        )
        credentials = citation_credentials(user)
        assert credentials.disabled is True
        assert has_llm_credentials(credentials) is False

    def test_model_id_loses_its_provider_prefix(self, monkeypatch):
        """
        `Settings.model` and the row hold a pydantic-ai id (`provider:name`),
        but this value goes straight into an OpenAI `model=` field where the
        prefix is not a valid model name.
        """
        from vista_backend.agents.inference import citation_credentials

        self._scrubbed(monkeypatch)
        user = UserPublicWithConfig(
            id=uuid.uuid4(),
            email="researcher@example.org",
            is_admin=False,
            inference_api_key="sk-x",
            inference_model="openai:claude-sonnet",
        )
        assert citation_credentials(user).model == "claude-sonnet"

    def test_supplied_credential_beats_the_environment(self, monkeypatch):
        """
        An explicit credential means the caller already decided; re-deriving
        from the environment could only contradict it.
        """
        from vista_backend.agents.inference import citation_credentials
        from vista_backend.utils.indexer import _get_text_rag_cls

        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.example/v1")
        user = UserPublicWithConfig(
            id=uuid.uuid4(),
            email="researcher@example.org",
            is_admin=False,
            inference_provider="custom",
            inference_custom_api_key="sk-from-the-row",
            inference_base_url="https://row.example/v1",
            inference_model="openai:row-model",
        )

        _get_text_rag_cls()  # puts the repo root on sys.path
        import build_rag

        resolved = build_rag._resolve_llm_config(60.0, citation_credentials(user))
        assert resolved.model == "row-model"
        assert str(resolved.client.base_url).rstrip("/") == "https://row.example/v1"

    def test_environment_path_is_unchanged(self, monkeypatch):
        """Deployments configured through `.env` keep working untouched."""
        from vista_backend.utils.indexer import _get_text_rag_cls, has_llm_credentials

        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.example/v1")
        monkeypatch.setenv("VISTA_BACKEND_MODEL", "openai:env-model")
        assert has_llm_credentials(None) is True

        _get_text_rag_cls()
        import build_rag

        resolved = build_rag._resolve_llm_config(60.0, None)
        assert resolved.model == "env-model"
        assert (
            str(resolved.client.base_url).rstrip("/")
            == "https://environment.example/v1"
        )
