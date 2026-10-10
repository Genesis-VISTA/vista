"""Per-cluster S3M tokens on the user row.

An S3M token is scoped to one OLCF project, so Odo and Frontier each need
their own. The legacy single `s3m_token` column is left in the table but is
never read, shown, or sent to the MCP server.
"""

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from vista_backend.agents.campaign.mcp_invoke import build_metadata
from vista_backend.api.users import get_me, update_me
from vista_backend.db.db import _add_missing_columns
from vista_backend.db.schemas import (
    UserPublicWithConfig,
    UserSelfUpdate,
    UserTable,
)


@pytest.mark.anyio
async def test_per_cluster_tokens_round_trip(session, alice):
    row = await session.get(UserTable, alice.id)
    saved = await update_me(
        UserSelfUpdate(odo_s3m_token="odo-tok", frontier_s3m_token="fr-tok"),
        session,
        row,
    )
    assert saved.odo_s3m_token == "odo-tok"
    assert saved.frontier_s3m_token == "fr-tok"

    shown = await get_me(row, config=True)
    assert shown.odo_s3m_token == "odo-tok"
    assert shown.frontier_s3m_token == "fr-tok"


@pytest.mark.anyio
async def test_clearing_one_token_leaves_the_other(session, alice):
    row = await session.get(UserTable, alice.id)
    await update_me(
        UserSelfUpdate(odo_s3m_token="odo-tok", frontier_s3m_token="fr-tok"),
        session,
        row,
    )
    # A cleared input arrives as "", which must store as null.
    saved = await update_me(UserSelfUpdate(frontier_s3m_token=""), session, row)
    assert saved.frontier_s3m_token is None
    assert saved.odo_s3m_token == "odo-tok"


@pytest.mark.anyio
async def test_legacy_token_is_never_exposed(session, alice):
    row = await session.get(UserTable, alice.id)
    row.s3m_token = "legacy-tok"
    session.add(row)
    await session.flush()

    shown = (await get_me(row, config=True)).model_dump(mode="json")
    assert "s3m_token" not in shown
    assert "legacy-tok" not in str(shown)

    meta = build_metadata(UserPublicWithConfig.model_validate(row), {})
    assert "legacy-tok" not in str(meta)
    assert meta["vista"]["user"]["odo_s3m_token"] is None
    assert meta["vista"]["user"]["frontier_s3m_token"] is None


def test_legacy_field_in_a_request_is_ignored():
    update = UserSelfUpdate.model_validate({"s3m_token": "legacy-tok"})
    assert "s3m_token" not in update.model_dump(exclude_unset=True)


def test_existing_database_gains_the_new_columns(tmp_path):
    """A DB created before this change picks the columns up at startup."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE app_user (id CHAR(32) PRIMARY KEY, email VARCHAR, "
                "s3m_token VARCHAR)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app_user (id, email, s3m_token) VALUES (:id, 'a@x', 'old')"
            ),
            {"id": uuid.uuid4().hex},
        )
        # Only app_user matters here; the other tables don't exist yet, so
        # _add_missing_columns skips them.
        assert "app_user" in SQLModel.metadata.tables
        _add_missing_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("app_user")}
        legacy = conn.execute(text("SELECT s3m_token FROM app_user")).scalar_one()

    assert {"odo_s3m_token", "frontier_s3m_token"} <= columns
    assert legacy == "old"  # left in place, not migrated or dropped


# ---------------------------------------------------------------------------
# Research profile used by the main assistant
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_research_profile_round_trip(session, alice):
    row = await session.get(UserTable, alice.id)
    saved = await update_me(
        UserSelfUpdate(
            research_role="Computational materials scientist",
            research_institution="Oak Ridge National Laboratory",
            research_interests=[" Molten salts ", "Corrosion", "Molten salts"],
            preferred_units="si",
            technical_depth="expert",
            evidence_preference="always_cite",
            personalize_responses=False,
        ),
        session,
        row,
    )

    assert saved.research_role == "Computational materials scientist"
    assert saved.research_institution == "Oak Ridge National Laboratory"
    assert saved.research_interests == ["Molten salts", "Corrosion"]
    assert saved.preferred_units == "si"
    assert saved.technical_depth == "expert"
    assert saved.evidence_preference == "always_cite"
    assert saved.personalize_responses is False

    shown = await get_me(row, config=True)
    assert shown.research_interests == ["Molten salts", "Corrosion"]
    # The lightweight view used by the navigation does not expose the profile.
    assert "research_role" not in (await get_me(row)).model_dump()


def test_research_profile_rejects_invalid_preferences_and_long_interest_lists():
    with pytest.raises(ValueError, match="preferred_units"):
        UserSelfUpdate(preferred_units="imperial")
    with pytest.raises(ValueError, match="at most 12"):
        UserSelfUpdate(research_interests=[f"interest-{i}" for i in range(13)])
    with pytest.raises(ValueError, match="80 characters"):
        UserSelfUpdate(research_interests=["x" * 81])


def test_existing_database_gains_research_profile_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    existing_id = uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE app_user (id CHAR(32) PRIMARY KEY, email VARCHAR)")
        )
        conn.execute(
            text("INSERT INTO app_user (id, email) VALUES (:id, 'a@x')"),
            {"id": existing_id},
        )
        _add_missing_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("app_user")}
        personalized = conn.execute(
            text("SELECT personalize_responses FROM app_user WHERE id = :id"),
            {"id": existing_id},
        ).scalar_one()

    assert {
        "research_role",
        "research_institution",
        "research_interests",
        "preferred_units",
        "technical_depth",
        "evidence_preference",
        "personalize_responses",
    } <= columns
    assert personalized == 1


# ---------------------------------------------------------------------------
# Which clusters the NavRail shows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hidden_clusters_default_to_none_hidden(session, alice):
    row = await session.get(UserTable, alice.id)
    assert (await get_me(row)).hpc_hidden_clusters == []
    assert (await get_me(row, config=True)).hpc_hidden_clusters == []


@pytest.mark.anyio
async def test_hidden_clusters_round_trip(session, alice):
    row = await session.get(UserTable, alice.id)
    await update_me(UserSelfUpdate(nersc_iri_token="iri-tok"), session, row)
    saved = await update_me(
        UserSelfUpdate(hpc_hidden_clusters=["perlmutter", "perlmutter"]), session, row
    )
    assert saved.hpc_hidden_clusters == ["perlmutter"]
    # Hiding a cluster leaves its credential alone.
    assert saved.nersc_iri_token == "iri-tok"
    # The light view carries it too: the rail reads that one.
    assert (await get_me(row)).hpc_hidden_clusters == ["perlmutter"]

    shown_again = await update_me(UserSelfUpdate(hpc_hidden_clusters=[]), session, row)
    assert shown_again.hpc_hidden_clusters == []


def test_unknown_cluster_is_rejected():
    with pytest.raises(ValueError, match="hpc_hidden_clusters"):
        UserSelfUpdate.model_validate({"hpc_hidden_clusters": ["polaris"]})


@pytest.mark.anyio
async def test_lux_can_be_hidden(session, alice):
    row = await session.get(UserTable, alice.id)
    saved = await update_me(UserSelfUpdate(hpc_hidden_clusters=["lux"]), session, row)
    assert saved.hpc_hidden_clusters == ["lux"]


def test_existing_database_gains_the_hidden_clusters_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE app_user (id CHAR(32) PRIMARY KEY, email VARCHAR)")
        )
        _add_missing_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("app_user")}
    assert "hpc_hidden_clusters" in columns


# ---------------------------------------------------------------------------
# Where each cluster's jobs live
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_remote_dirs_round_trip_and_reach_the_mcp_server(session, alice):
    row = await session.get(UserTable, alice.id)
    saved = await update_me(
        UserSelfUpdate(
            odo_remote_dir="/odo/proj/vista",
            frontier_remote_dir="/frontier/proj/vista",
            lux_remote_dir="/lux/me/vista",
            lux_account="abc123",
        ),
        session,
        row,
    )
    assert saved.odo_remote_dir == "/odo/proj/vista"
    assert saved.frontier_remote_dir == "/frontier/proj/vista"
    assert saved.lux_remote_dir == "/lux/me/vista"
    assert saved.lux_account == "abc123"

    meta = build_metadata(UserPublicWithConfig.model_validate(row), {})
    user = meta["vista"]["user"]
    assert user["odo_remote_dir"] == "/odo/proj/vista"
    assert user["frontier_remote_dir"] == "/frontier/proj/vista"
    assert user["lux_remote_dir"] == "/lux/me/vista"
    assert user["lux_account"] == "abc123"


@pytest.mark.anyio
async def test_a_cleared_remote_dir_is_not_set(session, alice):
    row = await session.get(UserTable, alice.id)
    await update_me(
        UserSelfUpdate(odo_remote_dir="/odo/vista", lux_remote_dir="/lux/vista"),
        session,
        row,
    )
    saved = await update_me(UserSelfUpdate(lux_remote_dir=""), session, row)
    assert saved.lux_remote_dir is None
    assert saved.odo_remote_dir == "/odo/vista"


@pytest.mark.anyio
async def test_retired_hpc_fields_are_not_exposed(session, alice):
    """`remote_hpc_jobs_dir` and `frontier_account` stay in the table and
    leave every API schema: nothing reads them any more."""
    row = await session.get(UserTable, alice.id)
    row.frontier_account = "chm243"
    session.add(row)
    await session.flush()

    shown = (await get_me(row, config=True)).model_dump(mode="json")
    assert "frontier_account" not in shown
    assert "remote_hpc_jobs_dir" not in shown
    update = UserSelfUpdate.model_validate(
        {"frontier_account": "x", "remote_hpc_jobs_dir": "/x"}
    )
    assert update.model_dump(exclude_unset=True) == {}


def test_existing_database_gains_the_remote_dir_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE app_user (id CHAR(32) PRIMARY KEY, email VARCHAR)")
        )
        _add_missing_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("app_user")}
    assert {
        "odo_remote_dir",
        "frontier_remote_dir",
        "lux_remote_dir",
        "lux_account",
    } <= columns


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


def test_existing_database_gains_the_inference_provider_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE app_user (id CHAR(32) PRIMARY KEY, email VARCHAR, "
                "inference_api_key VARCHAR)"
            )
        )
        _add_missing_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("app_user")}
    assert {
        "inference_provider",
        "inference_mag_api_key",
        "inference_olcf_api_key",
        "inference_custom_api_key",
    } <= columns


@pytest.mark.anyio
async def test_changing_provider_clears_the_model(session, alice, i2_settings):
    row = await session.get(UserTable, alice.id)
    await update_me(UserSelfUpdate(inference_model="openai:claude-opus"), session, row)

    saved = await update_me(UserSelfUpdate(inference_provider="mag"), session, row)
    assert saved.inference_provider == "mag"
    assert saved.inference_model is None


@pytest.mark.anyio
async def test_provider_change_with_a_model_keeps_that_model(
    session, alice, i2_settings
):
    row = await session.get(UserTable, alice.id)
    saved = await update_me(
        UserSelfUpdate(inference_provider="mag", inference_model="openai:gpt-oss"),
        session,
        row,
    )
    assert saved.inference_model == "openai:gpt-oss"


@pytest.mark.anyio
async def test_choosing_the_provider_in_effect_keeps_the_model(
    session, alice, i2_settings
):
    """An existing researcher on i2 who picks i2 explicitly loses nothing."""
    row = await session.get(UserTable, alice.id)
    await update_me(UserSelfUpdate(inference_model="openai:claude-opus"), session, row)
    saved = await update_me(UserSelfUpdate(inference_provider="i2"), session, row)
    assert saved.inference_model == "openai:claude-opus"


@pytest.mark.anyio
async def test_switching_provider_keeps_every_key(session, alice, i2_settings):
    row = await session.get(UserTable, alice.id)
    await update_me(UserSelfUpdate(inference_api_key="i2-key"), session, row)
    await update_me(
        UserSelfUpdate(inference_provider="mag", inference_mag_api_key="mag-key"),
        session,
        row,
    )
    saved = await update_me(UserSelfUpdate(inference_provider="i2"), session, row)
    assert saved.inference_api_key == "i2-key"
    assert saved.inference_mag_api_key == "mag-key"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        UserSelfUpdate(inference_provider="azure")


@pytest.mark.anyio
async def test_inference_view_has_no_secrets(session, alice, i2_settings):
    from vista_backend.api.users import get_inference

    row = await session.get(UserTable, alice.id)
    await update_me(
        UserSelfUpdate(
            inference_api_key="i2-secret-value",
            inference_mag_api_key="mag-secret-value",
        ),
        session,
        row,
    )

    view = (await get_inference(row)).model_dump(mode="json")

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
    from harness import api_client, seed_user

    alice = await seed_user(session)
    with api_client(session) as (client, _):
        response = await client.get(
            "/users/me/inference", headers={"X-Vista-User-Email": alice.email}
        )
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "i2"


@pytest.mark.anyio
async def test_inference_view_on_mag_has_no_model(session, alice, i2_settings):
    from vista_backend.api.users import get_inference

    row = await session.get(UserTable, alice.id)
    await update_me(UserSelfUpdate(inference_provider="mag"), session, row)
    view = await get_inference(row)
    assert view.provider == "mag"
    assert view.source == "user"
    assert view.model is None
    assert view.model_is_default is False
    assert view.has_credential is False
