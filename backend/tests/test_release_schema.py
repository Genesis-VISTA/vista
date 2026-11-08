"""
`db/release_schema.py`: the release workflow's data-compatibility step, which
decides `update.json`'s `data_compatible_from`.
"""

import json
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from vista_backend.db import release_schema


def test_older_lists_earlier_releases_newest_first():
    tags = [
        "v0.2.3",
        "v0.2.4",
        "v0.2.2-rc1",
        "v0.2.2",
        "v0.2.5",
        "v0.3.0",
        "v0.1.1-water4energy_downscale",
    ]
    assert release_schema.older("0.2.5", tags) == [
        "v0.2.4",
        "v0.2.3",
        "v0.2.2",
        "v0.2.2-rc1",
    ]


def test_older_refuses_a_version_no_release_carries():
    with pytest.raises(ValueError):
        release_schema.older("dev", ["v0.2.4"])


@pytest.fixture
def release_db(tmp_path):
    """An empty database as this code creates it."""
    path = tmp_path / "vista-schema.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    return path


def test_check_accepts_this_codes_own_database(release_db, capsys):
    assert release_schema.main(["check", str(release_db)]) == 0
    assert capsys.readouterr().err == ""


def test_check_forgives_what_startup_adds(release_db):
    # A release before a nullable column: the startup column-adder repairs it.
    conn = sqlite3.connect(release_db)
    conn.execute("ALTER TABLE local_settings DROP COLUMN update_checked_at")
    conn.close()
    assert release_schema.problems(release_db) == []


def test_check_refuses_a_dropped_column_and_names_it(release_db, capsys):
    conn = sqlite3.connect(release_db)
    conn.execute("ALTER TABLE project DROP COLUMN modified")
    conn.close()
    assert release_schema.main(["check", str(release_db)]) == 1
    assert "project.modified" in capsys.readouterr().err


def test_check_fails_loudly_on_a_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        release_schema.problems(tmp_path / "nowhere.sqlite")


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        # Additive since 0.2.4, and 0.2.3 opens too; 0.2.2 dropped a column.
        ([("v0.2.4", True), ("v0.2.3", True), ("v0.2.2", False)], "0.2.3"),
        # Every earlier release opens: the oldest one.
        ([("v0.2.4", True), ("v0.2.3", True)], "0.2.3"),
        # The newest earlier release is refused: every earlier install resets.
        ([("v0.2.4", False)], "0.2.5"),
        # The first release has nothing before it.
        ([], "0.2.5"),
    ],
)
def test_verdict(results, expected):
    assert release_schema.verdict("0.2.5", results) == {
        "data_compatible_from": expected
    }


def test_verdict_command_prints_update_json(capsys):
    assert (
        release_schema.main(["verdict", "0.2.5", "v0.2.4=pass", "v0.2.2-rc1=fail"]) == 0
    )
    assert json.loads(capsys.readouterr().out) == {"data_compatible_from": "0.2.4"}
    with pytest.raises(SystemExit):
        release_schema.main(["verdict", "0.2.5", "v0.2.4=maybe"])
