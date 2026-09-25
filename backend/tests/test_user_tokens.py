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
