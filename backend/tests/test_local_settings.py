"""The installation's one settings row, and the `/settings` routes over it.

VISTA has one researcher per installation, so what used to live on a user row
(cluster credentials, remote dirs, the research profile, inference keys) lives
on `local_settings`, made at startup by `init_db`.
"""

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel, select

from vista_backend.agents.campaign.mcp_invoke import build_metadata
from vista_backend.api.settings import get_inference, get_settings, update_settings
from vista_backend.db.db import _add_missing_columns
from vista_backend.db.schemas import (
    LocalSettingsPublic,
    LocalSettingsTable,
    LocalSettingsUpdate,
)
from vista_backend.services import local_settings


async def _local(session) -> LocalSettingsPublic:
    return await local_settings.get_public(session)


async def _put(session, update: LocalSettingsUpdate) -> LocalSettingsPublic:
    """`PUT /settings` as the route runs it: the settings in effect, then the update."""
    return await update_settings(update, session, await _local(session))


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_never_makes_a_second_row(session):
    first = await local_settings.ensure(session)
    second = await local_settings.ensure(session)
    rows = (await session.exec(select(LocalSettingsTable))).all()
    assert len(rows) == 1
    assert first.install_id == second.install_id


@pytest.mark.anyio
async def test_ensure_creates_the_row_with_an_install_id(engine):
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with AsyncSession(engine) as s:
        await s.delete(await local_settings.get(s))
        await s.commit()
    async with AsyncSession(engine) as s:
        row = await local_settings.ensure(s)
        assert isinstance(row.install_id, uuid.UUID)


@pytest.mark.anyio
async def test_get_refuses_to_create_the_row(engine):
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with AsyncSession(engine) as s:
        await s.delete(await local_settings.get(s))
        await s.commit()
    async with AsyncSession(engine) as s:
        with pytest.raises(RuntimeError, match="init_db"):
            await local_settings.get(s)


@pytest.mark.anyio
async def test_install_id_cannot_be_set_and_survives_updates(session):
    before = (await _local(session)).install_id
    update = LocalSettingsUpdate.model_validate(
        {"install_id": str(uuid.uuid4()), "display_name": "Ada"}
    )
    saved = await _put(session, update)
    assert saved.install_id == before
    assert saved.display_name == "Ada"


@pytest.mark.parametrize(
    "email", ["ada@ornl", "ada.ornl.gov", "ada @ornl.gov", "a@b@c.org"]
)
def test_a_malformed_email_is_refused(email):
    """It would be saved and then fail every notification without a word."""
    with pytest.raises(ValueError, match="enter an email address"):
        LocalSettingsUpdate(email=email)


def test_an_email_is_trimmed_and_a_blank_one_clears_it():
    assert LocalSettingsUpdate(email="  ada@ornl.gov ").email == "ada@ornl.gov"
    assert LocalSettingsUpdate(email="").email is None


@pytest.mark.anyio
async def test_a_malformed_email_is_a_422_over_http(session):
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.put("/settings", json={"email": "ada@ornl"})
    assert resp.status_code == 422
    assert (await _local(session)).email is None


@pytest.mark.anyio
async def test_install_id_survives_a_put_over_http(session):
    from harness import api_client

    before = (await _local(session)).install_id
    with api_client(session) as (client, _):
        resp = await client.put(
            "/settings", json={"install_id": str(uuid.uuid4()), "email": "a@x.org"}
        )
        assert resp.status_code == 200, resp.text
        got = (await client.get("/settings")).json()
    assert got["install_id"] == str(before)
    assert got["email"] == "a@x.org"


@pytest.mark.anyio
async def test_routes_need_no_identity_and_users_is_gone(session):
    from harness import api_client

    with api_client(session) as (client, _):
        assert (await client.get("/settings")).status_code == 200
        assert (await client.get("/projects")).status_code == 200
        assert (await client.get("/users/me")).status_code == 404
        assert (await client.get("/users")).status_code == 404


@pytest.mark.anyio
async def test_get_settings_returns_the_row(session, local):
    assert await get_settings(local) == local


# ---------------------------------------------------------------------------
# Per-cluster S3M tokens
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_per_cluster_tokens_round_trip(session):
    saved = await _put(
        session,
        LocalSettingsUpdate(odo_s3m_token="odo-tok", frontier_s3m_token="fr-tok"),
    )
    assert saved.odo_s3m_token == "odo-tok"
    assert saved.frontier_s3m_token == "fr-tok"

    shown = await get_settings(await _local(session))
    assert shown.odo_s3m_token == "odo-tok"
    assert shown.frontier_s3m_token == "fr-tok"


@pytest.mark.anyio
async def test_clearing_one_token_leaves_the_other(session):
    await _put(
        session,
        LocalSettingsUpdate(odo_s3m_token="odo-tok", frontier_s3m_token="fr-tok"),
    )
    # A cleared input arrives as "", which must store as null.
    saved = await _put(session, LocalSettingsUpdate(frontier_s3m_token=""))
    assert saved.frontier_s3m_token is None
    assert saved.odo_s3m_token == "odo-tok"


def test_retired_fields_are_not_part_of_the_settings():
    """`s3m_token`, `remote_hpc_jobs_dir` and `frontier_account` were never
    carried over to the settings row."""
    update = LocalSettingsUpdate.model_validate(
        {"s3m_token": "x", "frontier_account": "x", "remote_hpc_jobs_dir": "/x"}
    )
    assert update.model_dump(exclude_unset=True) == {}
    for name in ("s3m_token", "frontier_account", "remote_hpc_jobs_dir"):
        assert name not in LocalSettingsTable.model_fields
        assert name not in LocalSettingsPublic.model_fields


def test_existing_settings_table_gains_new_columns(tmp_path):
    """A settings table made by an earlier version picks new columns up at startup."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE local_settings (id INTEGER PRIMARY KEY, "
                "install_id CHAR(32))"
            )
        )
        conn.execute(
            text("INSERT INTO local_settings (id, install_id) VALUES (1, :i)"),
            {"i": uuid.uuid4().hex},
        )
        assert "local_settings" in SQLModel.metadata.tables
        _add_missing_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("local_settings")}
        personalized = conn.execute(
            text("SELECT personalize_responses FROM local_settings")
        ).scalar_one()
    assert {
        "odo_s3m_token",
        "frontier_s3m_token",
        "research_role",
        "personalize_responses",
        "hpc_hidden_clusters",
        "onboarding_tour",
        "odo_remote_dir",
        "inference_provider",
        "inference_olcf_api_key",
    } <= columns
    assert personalized == 1


# ---------------------------------------------------------------------------
# Research profile used by the main assistant
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_profile_round_trip(session):
    saved = await _put(
        session,
        LocalSettingsUpdate(
            research_role="Computational materials scientist",
            research_institution="Oak Ridge National Laboratory",
            research_interests=[" Molten salts ", "Corrosion", "Molten salts"],
            preferred_units="si",
            technical_depth="expert",
            evidence_preference="always_cite",
            personalize_responses=False,
        ),
    )

    assert saved.research_role == "Computational materials scientist"
    assert saved.research_institution == "Oak Ridge National Laboratory"
    assert saved.research_interests == ["Molten salts", "Corrosion"]
    assert saved.preferred_units == "si"
    assert saved.technical_depth == "expert"
    assert saved.evidence_preference == "always_cite"
    assert saved.personalize_responses is False
    assert (await _local(session)).research_interests == ["Molten salts", "Corrosion"]


def test_research_profile_rejects_invalid_preferences_and_long_interest_lists():
    with pytest.raises(ValueError, match="preferred_units"):
        LocalSettingsUpdate(preferred_units="imperial")
    with pytest.raises(ValueError, match="at most 12"):
        LocalSettingsUpdate(research_interests=[f"interest-{i}" for i in range(13)])
    with pytest.raises(ValueError, match="80 characters"):
        LocalSettingsUpdate(research_interests=["x" * 81])


# ---------------------------------------------------------------------------
# Which clusters the NavRail shows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hidden_clusters_default_to_none_hidden(session):
    assert (await _local(session)).hpc_hidden_clusters == []


@pytest.mark.anyio
async def test_hidden_clusters_round_trip(session):
    await _put(session, LocalSettingsUpdate(nersc_iri_token="iri-tok"))
    saved = await _put(
        session, LocalSettingsUpdate(hpc_hidden_clusters=["perlmutter", "perlmutter"])
    )
    assert saved.hpc_hidden_clusters == ["perlmutter"]
    # Hiding a cluster leaves its credential alone.
    assert saved.nersc_iri_token == "iri-tok"

    shown_again = await _put(session, LocalSettingsUpdate(hpc_hidden_clusters=[]))
    assert shown_again.hpc_hidden_clusters == []


def test_unknown_cluster_is_rejected():
    with pytest.raises(ValueError, match="hpc_hidden_clusters"):
        LocalSettingsUpdate.model_validate({"hpc_hidden_clusters": ["polaris"]})


@pytest.mark.anyio
async def test_lux_can_be_hidden(session):
    saved = await _put(session, LocalSettingsUpdate(hpc_hidden_clusters=["lux"]))
    assert saved.hpc_hidden_clusters == ["lux"]


# ---------------------------------------------------------------------------
# The first-boot tour's progress
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_onboarding_tour_is_null_on_a_fresh_row(session):
    assert (await _local(session)).onboarding_tour is None


@pytest.mark.anyio
async def test_onboarding_tour_round_trips_and_can_be_forgotten(session):
    state = {"status": "in_progress", "step": "welcome"}
    saved = await _put(
        session, LocalSettingsUpdate.model_validate({"onboarding_tour": state})
    )
    assert saved.onboarding_tour == state
    assert (await _local(session)).onboarding_tour == state

    cleared = await _put(
        session, LocalSettingsUpdate.model_validate({"onboarding_tour": None})
    )
    assert cleared.onboarding_tour is None


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "done", "step": "welcome"},
        {"status": "finished"},
        {"status": "finished", "step": "welcome", "extra": 1},
    ],
)
def test_onboarding_tour_rejects_a_bad_write(bad):
    with pytest.raises(ValueError, match="onboarding_tour"):
        LocalSettingsUpdate.model_validate({"onboarding_tour": bad})


@pytest.mark.anyio
async def test_a_bad_tour_status_is_a_422_over_http(session):
    from harness import api_client

    with api_client(session) as (client, _):
        resp = await client.put(
            "/settings", json={"onboarding_tour": {"status": "done", "step": "x"}}
        )
    assert resp.status_code == 422
    assert (await _local(session)).onboarding_tour is None


@pytest.mark.anyio
async def test_tour_enabled_comes_from_configuration_and_a_put_cannot_set_it(
    session, monkeypatch
):
    from harness import api_client

    from vista_backend.config import settings

    monkeypatch.setattr(settings, "onboarding_tour", False)
    assert (await _local(session)).onboarding_tour_enabled is False
    monkeypatch.setattr(settings, "onboarding_tour", True)
    with api_client(session) as (client, _):
        resp = await client.put("/settings", json={"onboarding_tour_enabled": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["onboarding_tour_enabled"] is True
        assert (await client.get("/settings")).json()["onboarding_tour_enabled"] is True


# ---------------------------------------------------------------------------
# Where each cluster's jobs live
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_remote_dirs_round_trip_and_reach_the_mcp_server(session):
    saved = await _put(
        session,
        LocalSettingsUpdate(
            odo_remote_dir="/odo/proj/vista",
            frontier_remote_dir="/frontier/proj/vista",
            lux_remote_dir="/lux/me/vista",
            lux_account="abc123",
        ),
    )
    assert saved.odo_remote_dir == "/odo/proj/vista"
    assert saved.frontier_remote_dir == "/frontier/proj/vista"
    assert saved.lux_remote_dir == "/lux/me/vista"
    assert saved.lux_account == "abc123"

    meta = build_metadata(await _local(session), {})
    # Still `user` on the wire: the MCP server reads it under that name.
    creds = meta["vista"]["user"]
    assert creds["odo_remote_dir"] == "/odo/proj/vista"
    assert creds["frontier_remote_dir"] == "/frontier/proj/vista"
    assert creds["lux_remote_dir"] == "/lux/me/vista"
    assert creds["lux_account"] == "abc123"


@pytest.mark.anyio
async def test_a_cleared_remote_dir_is_not_set(session):
    await _put(
        session,
        LocalSettingsUpdate(odo_remote_dir="/odo/vista", lux_remote_dir="/lux/vista"),
    )
    saved = await _put(session, LocalSettingsUpdate(lux_remote_dir=""))
    assert saved.lux_remote_dir is None
    assert saved.odo_remote_dir == "/odo/vista"


# ---------------------------------------------------------------------------
# Inference provider and its per-provider keys
# ---------------------------------------------------------------------------


@pytest.fixture
def i2_settings(monkeypatch):
    """An installation whose own configuration is the i2 preset's, keyless."""
    from vista_backend.config import settings

    monkeypatch.setattr(settings, "model", "openai:claude-sonnet")
    monkeypatch.setattr(
        settings, "openai_base_url", "https://api.i2-core.american-science-cloud.org"
    )
    monkeypatch.setattr(settings, "openai_api_key", None)


@pytest.mark.anyio
async def test_changing_provider_clears_the_model(session, i2_settings):
    await _put(session, LocalSettingsUpdate(inference_model="openai:claude-opus"))

    saved = await _put(session, LocalSettingsUpdate(inference_provider="mag"))
    assert saved.inference_provider == "mag"
    assert saved.inference_model is None


@pytest.mark.anyio
async def test_provider_change_with_a_model_keeps_that_model(session, i2_settings):
    saved = await _put(
        session,
        LocalSettingsUpdate(inference_provider="mag", inference_model="openai:gpt-oss"),
    )
    assert saved.inference_model == "openai:gpt-oss"


@pytest.mark.anyio
async def test_choosing_the_provider_in_effect_keeps_the_model(session, i2_settings):
    """An existing researcher on i2 who picks i2 explicitly loses nothing."""
    await _put(session, LocalSettingsUpdate(inference_model="openai:claude-opus"))
    saved = await _put(session, LocalSettingsUpdate(inference_provider="i2"))
    assert saved.inference_model == "openai:claude-opus"


@pytest.mark.anyio
async def test_switching_provider_keeps_every_key(session, i2_settings):
    await _put(session, LocalSettingsUpdate(inference_api_key="i2-key"))
    await _put(
        session,
        LocalSettingsUpdate(inference_provider="mag", inference_mag_api_key="mag-key"),
    )
    saved = await _put(session, LocalSettingsUpdate(inference_provider="i2"))
    assert saved.inference_api_key == "i2-key"
    assert saved.inference_mag_api_key == "mag-key"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        LocalSettingsUpdate(inference_provider="azure")


@pytest.mark.anyio
async def test_inference_view_has_no_secrets(session, i2_settings):
    await _put(
        session,
        LocalSettingsUpdate(
            inference_api_key="i2-secret-value",
            inference_mag_api_key="mag-secret-value",
        ),
    )

    view = (await get_inference(await _local(session))).model_dump(mode="json")

    assert "secret-value" not in str(view)
    assert [p["id"] for p in view["providers"]] == ["i2", "mag", "olcf", "custom"]
    assert view["providers"][0] == {
        "id": "i2",
        "name": "AmSC i2",
        "takes_url": False,
        "default_model": "claude-sonnet",
    }
    assert view["providers"][2] == {
        "id": "olcf",
        "name": "OLCF Inference",
        "takes_url": False,
        "default_model": "gpt-oss-120b",
    }
    assert view["providers"][3]["takes_url"] is True
    assert view["provider"] == "i2"
    assert view["source"] == "default"
    assert view["model"] == "openai:claude-sonnet"
    assert view["model_is_default"] is True
    assert view["has_credential"] is True
    assert view["keys_set"] == {
        "i2": True,
        "mag": True,
        "olcf": False,
        "custom": False,
    }


@pytest.mark.anyio
async def test_inference_view_over_http(session, i2_settings):
    from harness import api_client

    with api_client(session) as (client, _):
        response = await client.get("/settings/inference")
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "i2"


@pytest.mark.anyio
async def test_inference_view_on_mag_uses_its_default(session, i2_settings):
    await _put(session, LocalSettingsUpdate(inference_provider="mag"))
    view = await get_inference(await _local(session))
    assert view.provider == "mag"
    assert view.source == "user"
    assert view.model == "openai:claude-sonnet"
    assert view.model_is_default is True
    assert view.has_credential is False
