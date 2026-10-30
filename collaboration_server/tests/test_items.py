"""Items, versions, the compare-and-set publish, blobs, and the identity stub."""

import asyncio
import hashlib
import uuid
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from vista_collaboration_server import API_VERSION
from vista_collaboration_server.api.app import _is_loopback, create_app
from vista_collaboration_server.config import CollabSettings
from vista_collaboration_server.services.identity import DISPLAY_NAME_HEADER

from conftest import INSTALL_ID, OTHER_INSTALL_ID, headers

pytestmark = pytest.mark.anyio


async def _create(
    client, kind="skill", name="salt-analysis", manifest=None, blob_ref=None
):
    return await client.post(
        "/items",
        json={
            "kind": kind,
            "name": name,
            "manifest": {"v": 1} if manifest is None else manifest,
            "blob_ref": blob_ref,
        },
    )


async def _item(client, kind="skill", name="salt-analysis", manifest=None) -> dict:
    res = await _create(client, kind, name, manifest)
    assert res.status_code == 201, res.text
    return res.json()


async def _publish(client, item_id, parent, manifest=None, blob_ref=None):
    return await client.post(
        f"/items/{item_id}/versions",
        json={
            "parent_version": parent,
            "manifest": manifest or {"v": parent},
            "blob_ref": blob_ref,
        },
    )


# ---------------------------------------------------------------------------
# /version and defaults
# ---------------------------------------------------------------------------


async def test_version_needs_no_identity(anon):
    res = await anon.get("/version")
    assert res.status_code == 200
    body = res.json()
    assert body["api_version"] == API_VERSION
    assert isinstance(body["server_version"], str) and body["server_version"]


def test_defaults_bind_loopback_on_8002(monkeypatch):
    for name in ("VISTA_COLLAB_HOST", "VISTA_COLLAB_PORT"):
        monkeypatch.delenv(name, raising=False)
    settings = CollabSettings.model_validate({})
    assert (settings.host, settings.port) == ("127.0.0.1", 8002)
    assert _is_loopback(settings.host)
    assert not _is_loopback("0.0.0.0")


def test_settings_read_the_collab_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("VISTA_COLLAB_PORT", "9123")
    monkeypatch.setenv("VISTA_COLLAB_DATA_DIR", str(tmp_path / "elsewhere"))
    settings = CollabSettings.model_validate({})
    assert settings.port == 9123
    assert settings.data_dir == (tmp_path / "elsewhere").resolve()
    assert str(settings.data_dir) in settings.database_url
    assert settings.blobs_dir == settings.data_dir / "blobs"


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


async def test_an_item_is_created_with_its_first_version(client):
    item = await _item(client, manifest={"n": 1})
    assert item["kind"] == "skill"
    assert item["name"] == "salt-analysis"
    assert item["head_version"] == 1
    assert item["created_by"] == str(INSTALL_ID)
    assert item["created_by_name"] == "Ada"
    assert (await client.get(f"/items/{item['id']}")).json() == item
    versions = (await client.get(f"/items/{item['id']}/versions")).json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["parent_version"] is None
    assert versions[0]["manifest"] == {"n": 1}
    assert versions[0]["created_by"] == str(INSTALL_ID)


async def test_an_item_can_be_created_with_an_id_of_the_clients(client, anon):
    chosen = uuid.uuid4()
    body = {"id": str(chosen), "kind": "knowledge-base", "name": "kb", "manifest": {}}
    res = await client.post("/items", json=body)
    assert res.status_code == 201, res.text
    assert res.json()["id"] == str(chosen)
    again = await client.post("/items", json={**body, "manifest": {"n": 2}})
    assert again.status_code == 409
    # The first is untouched, and the server still works after the refusal.
    versions = (await client.get(f"/items/{chosen}/versions")).json()
    assert [v["manifest"] for v in versions] == [{}]
    assert len((await client.get("/items")).json()) == 1
    await _item(client)


async def test_list_items_filters_by_kind(client):
    skill = await _item(client, "skill", "a")
    project = await _item(client, "project", "b")
    assert [i["id"] for i in (await client.get("/items")).json()] == [
        skill["id"],
        project["id"],
    ]
    only = (await client.get("/items", params={"kind": "project"})).json()
    assert [i["id"] for i in only] == [project["id"]]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "Skill", "name": "x", "manifest": {}},
        {"kind": "skill", "name": "   ", "manifest": {}},
        {"kind": "", "name": "x", "manifest": {}},
        {"kind": "skill", "name": "x"},
    ],
)
async def test_create_item_validates(client, body):
    assert (await client.post("/items", json=body)).status_code == 422
    assert (await client.get("/items")).json() == []


async def test_unknown_item_is_404(client):
    missing = uuid.uuid4()
    for path in (
        f"/items/{missing}",
        f"/items/{missing}/versions",
        f"/items/{missing}/versions/1",
    ):
        res = await client.get(path)
        assert res.status_code == 404, path
        assert res.json()["detail"] == "No such item."
    assert (await _publish(client, missing, 1)).status_code == 404
    # An unknown item is the answer even when the blob is unknown too.
    assert (await _publish(client, missing, 1, blob_ref="0" * 64)).status_code == 404


async def test_unknown_version_of_a_known_item_is_404(client):
    item = await _item(client)
    res = await client.get(f"/items/{item['id']}/versions/7")
    assert res.status_code == 404
    assert res.json()["detail"] == "No such version."


# ---------------------------------------------------------------------------
# Versions and the compare-and-set
# ---------------------------------------------------------------------------


async def test_versions_are_numbered_by_the_server(client):
    item = await _item(client, manifest={"n": 1})
    second = await _publish(client, item["id"], 1, {"n": 2})
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    assert second.json()["parent_version"] == 1
    third = await _publish(client, item["id"], 2, {"n": 3})
    assert third.json()["version"] == 3

    assert (await client.get(f"/items/{item['id']}")).json()["head_version"] == 3
    versions = (await client.get(f"/items/{item['id']}/versions")).json()
    assert [(v["version"], v["manifest"]) for v in versions] == [
        (1, {"n": 1}),
        (2, {"n": 2}),
        (3, {"n": 3}),
    ]
    assert (await client.get(f"/items/{item['id']}/versions/2")).json()["manifest"] == {
        "n": 2
    }


async def test_a_stale_parent_is_409_with_the_head(client):
    item = await _item(client)
    await _publish(client, item["id"], 1)
    stale = await _publish(client, item["id"], 1, {"lost": True})
    assert stale.status_code == 409
    assert stale.json()["head_version"] == 2
    assert "version 2" in stale.json()["detail"]
    # Nothing was written.
    versions = (await client.get(f"/items/{item['id']}/versions")).json()
    assert [v["version"] for v in versions] == [1, 2]


async def test_a_parent_ahead_of_the_head_is_409(client):
    item = await _item(client)
    res = await _publish(client, item["id"], 5)
    assert res.status_code == 409
    assert res.json()["head_version"] == 1


@pytest.mark.parametrize("parent", [None, 0, -1])
async def test_parent_version_must_name_a_version(client, parent):
    item = await _item(client)
    res = await _publish(client, item["id"], parent)
    assert res.status_code == 422
    res = await client.post(f"/items/{item['id']}/versions", json={"manifest": {}})
    assert res.status_code == 422


async def test_concurrent_publishes_on_one_head_accept_exactly_one(app, client):
    item = await _item(client)
    results = await asyncio.gather(
        *(_publish(client, item["id"], 1, {"who": n}) for n in range(5))
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409]
    versions = (await client.get(f"/items/{item['id']}/versions")).json()
    assert [v["version"] for v in versions] == [1, 2]


async def test_versions_cannot_be_changed_or_deleted(client):
    item = await _item(client)
    path = f"/items/{item['id']}/versions/1"
    assert (await client.put(path, json={"manifest": {}})).status_code == 405
    assert (await client.patch(path, json={"manifest": {}})).status_code == 405
    assert (await client.delete(path)).status_code == 405


# ---------------------------------------------------------------------------
# Blobs
# ---------------------------------------------------------------------------


async def test_blob_round_trip(client, settings):
    data = b"skill archive bytes"
    up = await client.post("/blobs", files={"file": ("skill.tar.gz", data)})
    assert up.status_code == 201, up.text
    ref = up.json()["blob_ref"]
    assert ref == hashlib.sha256(data).hexdigest()
    assert up.json()["size"] == len(data)
    assert (settings.blobs_dir / ref[:2] / ref).read_bytes() == data

    res = await _create(client, blob_ref=ref)
    assert res.status_code == 201, res.text
    item = res.json()
    blob = await client.get(f"/items/{item['id']}/versions/1/blob")
    assert blob.status_code == 200
    assert blob.content == data

    later = await _publish(client, item["id"], 1, blob_ref=ref)
    assert later.status_code == 201
    assert (await client.get(f"/items/{item['id']}/versions/2/blob")).content == data


async def test_the_same_bytes_are_stored_once(client, settings):
    first = await client.post("/blobs", files={"file": ("a", b"same")})
    second = await client.post("/blobs", files={"file": ("b", b"same")})
    assert first.json()["blob_ref"] == second.json()["blob_ref"]
    stored = [p for p in settings.blobs_dir.rglob("*") if p.is_file()]
    assert len(stored) == 1


async def test_a_blob_over_the_limit_is_413_and_not_kept(client, settings):
    res = await client.post("/blobs", files={"file": ("big", b"x" * 1025)})
    assert res.status_code == 413
    assert [p for p in settings.blobs_dir.rglob("*") if p.is_file()] == []


async def test_an_unknown_blob_is_422_and_writes_nothing(client):
    res = await _create(client, blob_ref="0" * 64)
    assert res.status_code == 422
    assert (await client.get("/items")).json() == []

    item = await _item(client)
    res = await _publish(client, item["id"], 1, blob_ref="0" * 64)
    assert res.status_code == 422
    assert (await client.get(f"/items/{item['id']}")).json()["head_version"] == 1


async def test_a_version_without_a_blob_has_none_to_download(client):
    item = await _item(client)
    assert (await client.get(f"/items/{item['id']}/versions/1/blob")).status_code == 404


# ---------------------------------------------------------------------------
# dev-headers identity
# ---------------------------------------------------------------------------


async def test_writes_need_an_install_id(anon):
    assert (await _create(anon)).status_code == 401
    assert (await anon.post("/blobs", files={"file": ("a", b"a")})).status_code == 401


async def test_reads_need_an_install_id_too(client, anon):
    item = await _item(client)
    assert (await anon.get("/items")).status_code == 401
    assert (await anon.get(f"/items/{item['id']}")).status_code == 401
    assert (await client.get(f"/items/{item['id']}")).status_code == 200


async def test_an_install_id_that_is_not_a_uuid_is_400(anon):
    res = await anon.post(
        "/items",
        json={"kind": "skill", "name": "x", "manifest": {}},
        headers={"X-Vista-Install-Id": "me"},
    )
    assert res.status_code == 400


async def test_a_display_name_is_percent_decoded(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://collab",
        headers={
            **headers(OTHER_INSTALL_ID, name=None),
            DISPLAY_NAME_HEADER: quote("Ångström 安"),
        },
    ) as client:
        item = await _item(client)
    assert item["created_by"] == str(OTHER_INSTALL_ID)
    assert item["created_by_name"] == "Ångström 安"


async def test_a_version_records_its_own_publisher(app, client):
    item = await _item(client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://collab",
        headers=headers(OTHER_INSTALL_ID, "Grace"),
    ) as other:
        res = await _publish(other, item["id"], 1)
    assert res.json()["created_by"] == str(OTHER_INSTALL_ID)
    assert res.json()["created_by_name"] == "Grace"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def test_only_the_servers_own_tables_are_created(app, settings):
    """The server's tables are on their own metadata, not `SQLModel.metadata`."""
    import sqlite3
    from contextlib import closing

    from sqlmodel import SQLModel

    db = settings.data_dir / "collaboration.db"
    # `closing`: a sqlite3 connection's own `with` commits but does not close.
    with closing(sqlite3.connect(db)) as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert names == {"item", "item_version"}
    assert "item" not in SQLModel.metadata.tables


async def test_data_survives_a_restart(settings):
    first = create_app(settings)
    async with first.router.lifespan_context(first):
        async with AsyncClient(
            transport=ASGITransport(app=first), base_url="http://c", headers=headers()
        ) as c:
            item = await _item(c, manifest={"kept": True})
    second = create_app(settings)
    async with second.router.lifespan_context(second):
        async with AsyncClient(
            transport=ASGITransport(app=second), base_url="http://c", headers=headers()
        ) as c:
            res = await c.get(f"/items/{item['id']}/versions/1")
    assert res.json()["manifest"] == {"kept": True}


async def test_a_database_on_nfs_uses_the_rollback_journal(settings):
    nfs = settings.model_copy(update={"sqlite_journal_mode": "DELETE"})
    app = create_app(nfs)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://c", headers=headers()
        ) as c:
            await _item(c)
        async with app.state.engine.connect() as conn:
            mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
    assert mode == "delete"
    assert not (settings.data_dir / "collaboration.db-wal").exists()


def test_explicit_settings_beat_the_environment(monkeypatch, tmp_path):
    """Why the fixtures pass `database_url`: init values win over `VISTA_COLLAB_*`."""
    monkeypatch.setenv("VISTA_COLLAB_DATABASE_URL", "sqlite+aiosqlite:////elsewhere.db")
    explicit = f"sqlite+aiosqlite:///{tmp_path / 'c.db'}"
    assert (
        CollabSettings(data_dir=tmp_path, database_url=explicit).database_url
        == explicit
    )
    assert CollabSettings(data_dir=tmp_path).database_url.endswith("/elsewhere.db")
