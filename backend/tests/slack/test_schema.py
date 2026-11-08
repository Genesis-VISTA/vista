"""
The Slack change's tables and the conversation `origin` column reach new and
existing databases through `init_db`'s `create_all` + `_add_missing_columns`.
"""

from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from vista_backend.db import schemas  # noqa: F401  (registers the tables)
from vista_backend.db.db import _add_missing_columns

SLACK_TABLES = {
    "hpc_job_record",
    "slack_config",
    "slack_link",
    "slack_link_code",
    "slack_thread",
    "slack_pending",
    "slack_detail",
    "slack_reminder",
    "slack_audit",
}


def _build(engine) -> None:
    with engine.begin() as conn:
        SQLModel.metadata.create_all(conn)
        _add_missing_columns(conn)


def test_a_fresh_database_has_the_slack_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _build(engine)
    assert SLACK_TABLES <= set(inspect(engine).get_table_names())


def test_existing_conversations_read_as_from_the_app(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _build(engine)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE chat_session DROP COLUMN "origin"'))
        conn.execute(
            text(
                "INSERT INTO chat_session (id, project_id, title, message_history,"
                " messages, run_state, run_unseen, created_at, updated_at)"
                " VALUES ('a', 'p', 't', '[]', '[]', 'idle', 0, '', '')"
            )
        )

    _build(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT origin FROM chat_session")).scalar() == "app"


def test_an_existing_link_gains_the_collaboration_identity_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _build(engine)
    with engine.begin() as conn:
        for column in ("collab_sub", "collab_name"):
            conn.execute(text(f'ALTER TABLE slack_link DROP COLUMN "{column}"'))
        conn.execute(
            text(
                "INSERT INTO slack_link (id, team_id, slack_user_id,"
                " notify_in_thread, created_at) VALUES ('l', 'T1', 'U1', 0, '')"
            )
        )

    _build(engine)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT collab_sub, collab_name FROM slack_link")).one()
    assert tuple(row) == (None, None), "a link made before is simply unattributed"
