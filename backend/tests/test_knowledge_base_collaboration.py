"""
Knowledge bases on the collaboration server: publishing one (its PDFs and a
snapshot of its index, next version, unchanged, refused while indexing,
conflicts), the import list, and importing one (new, renamed, refused for
another embedding model, updated in place without writing over the store the
MCP server reads, local changes), against a real server mounted in-process
(`collab_server` in conftest). Another installation is a second client.

Also the prerequisites: deleting a PDF deletes its rows from the index and
rewrites the BM25 corpus, and deleting a knowledge base removes its folder.
"""

import asyncio
import json
import tarfile
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

from harness import api_client
from harness.knowledge_bases import (
    FORMAT,
    kb_row,
    make_kb,
    make_store,
    publish_kb_elsewhere,
    same_format,
    store_sources,
    write_pdf,
)
from vista_backend.config import settings
from vista_backend.db.seed import MOLTEN_SALT_KB_ID, seed_item_id
from vista_backend.db.schemas import KnowledgeBaseTable, LocalSettingsUpdate
from vista_backend.services import local_settings
from vista_backend.services.collaboration import CollaborationClient
from vista_backend.services.knowledge_base import CURRENT_STORE_FILE
from vista_backend.services.knowledge_base_collaboration import (
    KB_KIND,
    ArchiveError,
    incompatibility,
    unpack_knowledge_base,
)
from vista_backend.utils import indexer
from vista_backend.utils.indexer import StoreFormat

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

URL = "http://collab.test:8002"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "collaboration_server_url", None)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    same_format(monkeypatch)


async def _use_server(session, url: str | None = URL):
    await local_settings.update(
        session,
        LocalSettingsUpdate.model_validate(
            {"collaboration_server_url": url, "display_name": "Ada"}
        ),
    )


def _other_install() -> CollaborationClient:
    return CollaborationClient(URL, install_id=uuid.uuid4(), display_name="Bea")


async def _seed_kb(session, slug: str = "salt-papers", sources=None, **fields):
    """A knowledge base here, indexed, in the canonical layout; committed."""
    kb_dir = settings.knowledge_bases_dir / slug
    make_kb(kb_dir, sources)
    session.add(kb_row(kb_dir, slug, **fields))
    await session.commit()
    return kb_dir


async def _kb(session, slug: str) -> KnowledgeBaseTable | None:
    session.expire_all()
    return (
        await session.exec(
            select(KnowledgeBaseTable).where(KnowledgeBaseTable.slug == slug)
        )
    ).first()


# ---------------------------------------------------------------------------
# What a knowledge base records
# ---------------------------------------------------------------------------


async def test_a_new_knowledge_base_is_modified_and_a_real_change_marks_it(
    collab_server, session
):
    await _use_server(session)
    await _seed_kb(session)
    with api_client(session) as (client, _):
        created = (await client.get("/knowledge-bases/salt-papers")).json()
        assert (created["modified"], created["remote_id"]) == (True, None)
        await client.post("/knowledge-bases/salt-papers/publish", json={})
        same = await client.put(
            "/knowledge-bases/salt-papers", json={"name": created["name"]}
        )
        await session.commit()
        assert same.json()["modified"] is False
        renamed = await client.put(
            "/knowledge-bases/salt-papers", json={"name": "Salt papers, 2026"}
        )
    assert renamed.json()["modified"] is True


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


async def test_publishing_sends_the_pdfs_and_a_snapshot_of_the_index(
    collab_server, session, tmp_path
):
    await _use_server(session)
    await _seed_kb(
        session, sources={"a.pdf": ["molten fluoride salt"], "b.pdf": ["chloride"]}
    )
    with api_client(session) as (client, _):
        res = await client.post("/knowledge-bases/salt-papers/publish", json={})
        again = await client.post("/knowledge-bases/salt-papers/publish", json={})
    assert res.status_code == 200, res.text
    result = res.json()
    assert result["status"] == "published"
    kb = result["knowledge_base"]
    assert (kb["origin_server"], kb["remote_version"], kb["modified"]) == (
        URL,
        1,
        False,
    )
    assert again.json()["status"] == "unchanged"

    async with _other_install() as other:
        item = await other.get_item(uuid.UUID(kb["remote_id"]))
        version = await other.get_version(item.id, 1)
        archive = tmp_path / "theirs.tar"
        await other.download_blob(item.id, 1, archive, blob_ref=version.blob_ref or "")
    assert (item.kind, item.name, item.head_version) == (
        "knowledge-base",
        "salt-papers",
        1,
    )
    manifest = version.manifest
    assert {k: manifest[k] for k in ("format", "slug", "name", "files", "size")} == {
        "format": 1,
        "slug": "salt-papers",
        "name": "The salt-papers papers",
        "files": 2,
        "size": 26,
    }
    assert (
        manifest["embedding_model"],
        manifest["embedding_revision"],
        manifest["chromadb"],
    ) == (FORMAT.embedding_model, FORMAT.embedding_revision, FORMAT.chromadb)
    assert [p["filename"] for p in manifest["publications"]] == ["a.pdf"]
    assert manifest["archive_size"] == archive.stat().st_size
    assert kb["id"] not in json.dumps(manifest)

    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert {"pdfs/a.pdf", "pdfs/b.pdf", "rag_db/chroma.sqlite3"} <= names
    unpacked = tmp_path / "unpacked"
    unpack_knowledge_base(archive, unpacked, has_index=True)
    assert store_sources(unpacked / "rag_db") == {"a.pdf": 1, "b.pdf": 1}


async def test_publishing_is_refused_while_indexing(collab_server, session):
    await _use_server(session)
    kb_dir = await _seed_kb(session)
    indexer.register_pending(str(kb_dir / "rag_db"), total=1)
    try:
        with api_client(session) as (client, _):
            res = await client.post("/knowledge-bases/salt-papers/publish", json={})
    finally:
        indexer.clear_progress(str(kb_dir / "rag_db"))
    assert res.status_code == 409
    assert "is indexing" in res.json()["detail"]
    assert (await _kb(session, "salt-papers")).remote_id is None


async def test_a_stale_head_is_a_conflict_with_the_choices(
    collab_server, session, tmp_path
):
    await _use_server(session)
    await _seed_kb(session)
    with api_client(session) as (client, _):
        first = (
            await client.post("/knowledge-bases/salt-papers/publish", json={})
        ).json()
        item_id = uuid.UUID(first["knowledge_base"]["remote_id"])
        async with _other_install() as other:
            await publish_kb_elsewhere(
                other, tmp_path, "salt-papers", item_id=item_id, parent=1
            )
        await client.put("/knowledge-bases/salt-papers", json={"name": "Mine"})
        await session.commit()
        stale = await client.post("/knowledge-bases/salt-papers/publish", json={})
        on_top = await client.post(
            "/knowledge-bases/salt-papers/publish", json={"on_top_of": 2}
        )
    assert stale.status_code == 409
    assert (stale.json()["code"], stale.json()["head_version"]) == (
        "version_conflict",
        2,
    )
    assert on_top.status_code == 200, on_top.text
    assert on_top.json()["knowledge_base"]["remote_version"] == 3


# ---------------------------------------------------------------------------
# Listing and importing
# ---------------------------------------------------------------------------


async def test_the_list_shows_copies_and_taken_slugs(collab_server, session, tmp_path):
    await _use_server(session)
    await _seed_kb(session)
    async with _other_install() as other:
        theirs = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
        fresh = await publish_kb_elsewhere(other, tmp_path, "nobel-papers")
    with api_client(session) as (client, _):
        listed = (await client.get("/collaboration/knowledge-bases")).json()
    by_id = {kb["id"]: kb for kb in listed}
    assert by_id[str(theirs)]["name_taken"] is True
    assert by_id[str(fresh)]["name_taken"] is False
    assert by_id[str(fresh)]["manifest"]["name"] == "Their nobel-papers"


async def test_importing_a_knowledge_base_makes_it_searchable_here(
    collab_server, session, tmp_path
):
    await _use_server(session)
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(
            other,
            tmp_path,
            "salt-papers",
            sources={"a.pdf": ["molten fluoride salt", "eutectic"]},
        )
    with api_client(session) as (client, _):
        res = await client.post(
            f"/collaboration/knowledge-bases/{item_id}/import", json={}
        )
        again = await client.post(
            f"/collaboration/knowledge-bases/{item_id}/import", json={}
        )
        got = await client.get("/knowledge-bases/salt-papers/publications/a.pdf")
    assert res.status_code == 200, res.text
    assert again.json()["status"] == "unchanged"
    kb = res.json()["knowledge_base"]
    assert res.json()["status"] == "imported"
    assert (kb["name"], kb["description"], kb["build_status"]) == (
        "Their salt-papers",
        "Their papers.",
        "ready",
    )
    assert (kb["origin_server"], kb["remote_id"], kb["remote_version"]) == (
        URL,
        str(item_id),
        1,
    )
    assert kb["modified"] is False
    assert [p["filename"] for p in kb["publications"]] == ["a.pdf"]
    assert got.status_code == 200

    # A new folder, which CURRENT names for the MCP server.
    kb_dir = settings.knowledge_bases_dir / "salt-papers"
    store = (kb_dir / CURRENT_STORE_FILE).read_text(encoding="utf-8")
    assert store.startswith("store-")
    assert Path(kb["rag_db_path"]) == kb_dir / store / "rag_db"
    assert store_sources(Path(kb["rag_db_path"])) == {"a.pdf": 2}
    assert list((settings.knowledge_bases_dir / ".incoming").iterdir()) == []


async def test_a_taken_slug_asks_for_another(collab_server, session, tmp_path):
    await _use_server(session)
    await _seed_kb(session)
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    url = f"/collaboration/knowledge-bases/{item_id}/import"
    with api_client(session) as (client, _):
        taken = await client.post(url, json={})
        bad = await client.post(url, json={"rename_to": "Salt Papers"})
        renamed = await client.post(url, json={"rename_to": "salt-papers-bea"})
    assert taken.status_code == 409
    assert (taken.json()["code"], taken.json()["name"]) == ("name_taken", "salt-papers")
    assert bad.status_code == 400
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["knowledge_base"]["slug"] == "salt-papers-bea"
    # The one here is untouched.
    assert (await _kb(session, "salt-papers")).remote_id is None


async def test_an_index_from_another_model_is_refused_before_downloading(
    collab_server, session, tmp_path, monkeypatch
):
    await _use_server(session)
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(
            other, tmp_path, "salt-papers", embedding_model="other/embedder"
        )
    downloads = []
    monkeypatch.setattr(
        CollaborationClient,
        "download_blob",
        lambda *a, **k: downloads.append(a),
    )
    with api_client(session) as (client, _):
        res = await client.post(
            f"/collaboration/knowledge-bases/{item_id}/import", json={}
        )
    assert res.status_code == 409
    assert "other/embedder" in res.json()["detail"]
    assert downloads == []
    assert await _kb(session, "salt-papers") is None


def test_which_indexes_can_be_searched_here():
    from vista_backend.services.knowledge_base_collaboration import (
        KnowledgeBaseManifest,
    )

    def manifest(**fields):
        return KnowledgeBaseManifest(
            slug="s",
            name="S",
            archive_size=1,
            embedding_model=fields.get("model", FORMAT.embedding_model),
            embedding_revision=fields.get("revision", FORMAT.embedding_revision),
            chromadb=fields.get("chromadb", FORMAT.chromadb),
        )

    here = StoreFormat(
        embedding_model=FORMAT.embedding_model,
        embedding_revision=FORMAT.embedding_revision,
        chromadb="1.6.0",
    )
    assert incompatibility(manifest(), here) is None
    assert incompatibility(manifest(chromadb="1.5.9rc1"), here) is None
    assert incompatibility(manifest(revision="1" * 40), here)
    assert incompatibility(manifest(chromadb="1.7.0"), here)  # newer than here
    assert incompatibility(manifest(chromadb="0.6.3"), here)  # another major
    assert incompatibility(manifest(chromadb="unknown"), here)


async def test_an_update_lands_beside_the_store_and_switches_to_it(
    collab_server, session, tmp_path
):
    """The MCP server may be reading the old store: it is never written over."""
    await _use_server(session)
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    url = f"/collaboration/knowledge-bases/{item_id}/import"
    with api_client(session) as (client, _):
        first = (await client.post(url, json={})).json()["knowledge_base"]
        async with _other_install() as other:
            await publish_kb_elsewhere(
                other,
                tmp_path,
                "salt-papers",
                item_id=item_id,
                parent=1,
                sources={"a.pdf": ["one"], "c.pdf": ["two", "three"]},
                name="Their salt papers, v2",
            )
        res = await client.post(url, json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "updated"
    kb = res.json()["knowledge_base"]
    assert (kb["id"], kb["slug"], kb["name"], kb["remote_version"]) == (
        first["id"],
        "salt-papers",
        "Their salt papers, v2",
        2,
    )
    kb_dir = settings.knowledge_bases_dir / "salt-papers"
    new_store = Path(kb["rag_db_path"]).parent
    assert new_store != Path(first["rag_db_path"]).parent
    assert (kb_dir / CURRENT_STORE_FILE).read_text(encoding="utf-8") == new_store.name
    assert store_sources(Path(kb["rag_db_path"])) == {"a.pdf": 1, "c.pdf": 2}
    # The old one is removed once nothing here has it open.
    assert not Path(first["rag_db_path"]).parent.exists()


async def test_local_changes_are_replaced_only_when_asked(
    collab_server, session, tmp_path
):
    await _use_server(session)
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    url = f"/collaboration/knowledge-bases/{item_id}/import"
    with api_client(session) as (client, _):
        await client.post(url, json={})
        await client.put("/knowledge-bases/salt-papers", json={"name": "Mine"})
        await session.commit()
        async with _other_install() as other:
            await publish_kb_elsewhere(
                other, tmp_path, "salt-papers", item_id=item_id, parent=1
            )
        refused = await client.post(url, json={})
        replaced = await client.post(url, json={"replace_modified": True})
    assert refused.status_code == 409
    assert refused.json()["code"] == "modified"
    assert replaced.status_code == 200, replaced.text
    kb = replaced.json()["knowledge_base"]
    assert (kb["name"], kb["remote_version"], kb["modified"]) == (
        "Their salt-papers",
        2,
        False,
    )


def _archive(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    import io

    archive = tmp_path / f"{uuid.uuid4().hex}.tar"
    with tarfile.open(archive, "w") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive


@pytest.mark.parametrize(
    "entries",
    [
        {"pdfs/../../outside.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"/etc/passwd.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"pdfs/script.sh": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"elsewhere/a.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"pdfs/a.pdf": b"x"},
        # A Windows drive: `Path("out") / "C:/x"` is `C:/x` there.
        {"rag_db/C:/Windows/evil.dll": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"pdfs/C:evil.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"pdfs\\..\\evil.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"pdfs//a.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
        {"./pdfs/a.pdf": b"x", "rag_db/chroma.sqlite3": b"x"},
    ],
)
def test_an_archive_holds_only_pdfs_and_an_index(tmp_path, entries):
    archive = _archive(tmp_path, entries)
    with pytest.raises(ArchiveError):
        unpack_knowledge_base(archive, tmp_path / "out", has_index=True)
    assert not (tmp_path / "outside.pdf").exists()


def test_an_archive_without_an_index_is_an_empty_knowledge_base(tmp_path):
    archive = _archive(tmp_path, {"pdfs/a.pdf": b"x"})
    unpack_knowledge_base(archive, tmp_path / "out", has_index=False)
    assert (tmp_path / "out" / "pdfs" / "a.pdf").is_file()
    assert list((tmp_path / "out" / "rag_db").iterdir()) == []


# ---------------------------------------------------------------------------
# Prerequisites: deleting a PDF, deleting a knowledge base, snapshots
# ---------------------------------------------------------------------------


async def test_deleting_a_pdf_deletes_its_rows_and_rewrites_the_bm25_corpus(
    session,
):
    kb_dir = await _seed_kb(
        session,
        sources={"a.pdf": ["molten fluoride salt"], "b.pdf": ["chloride", "nitrate"]},
        publications=[
            {"filename": "a.pdf", "size": 13, "index_status": "indexed"},
            {"filename": "b.pdf", "size": 13, "index_status": "indexed"},
        ],
    )
    with api_client(session) as (client, _):
        res = await client.delete("/knowledge-bases/salt-papers/publications/b.pdf")
        await session.commit()
    assert res.status_code == 204, res.text
    assert store_sources(kb_dir / "rag_db") == {"a.pdf": 1}
    assert not (kb_dir / "pdfs" / "b.pdf").exists()
    corpus = json.loads(
        (kb_dir / "rag_db" / "bm25_corpus.json").read_text(encoding="utf-8")
    )
    assert [d["text"] for d in corpus["documents"]] == ["molten fluoride salt"]
    kb = await _kb(session, "salt-papers")
    assert [p["filename"] for p in kb.publications] == ["a.pdf"]
    assert kb.modified is True


async def test_deleting_a_pdf_is_refused_while_indexing(session):
    kb_dir = await _seed_kb(session)
    indexer.register_pending(str(kb_dir / "rag_db"), total=1)
    try:
        with api_client(session) as (client, _):
            res = await client.delete("/knowledge-bases/salt-papers/publications/a.pdf")
    finally:
        indexer.clear_progress(str(kb_dir / "rag_db"))
    assert res.status_code == 409
    assert store_sources(kb_dir / "rag_db") == {"a.pdf": 1}


async def test_deleting_a_knowledge_base_removes_its_folder(session):
    kb_dir = await _seed_kb(session)
    (kb_dir / CURRENT_STORE_FILE).write_text("store-abc", encoding="utf-8")
    write_pdf(kb_dir / "store-abc" / "pdfs" / "left.pdf")
    with api_client(session) as (client, _):
        res = await client.delete("/knowledge-bases/salt-papers")
        await session.commit()
    assert res.status_code == 204
    assert not kb_dir.exists()


async def test_a_snapshot_is_a_store_of_its_own(tmp_path):
    store = tmp_path / "rag_db"
    make_store(store, {"a.pdf": ["one", "two"]})
    (store / "bm25_corpus.json").write_text("{}", encoding="utf-8")

    await indexer.snapshot_store(str(store), tmp_path / "copy")

    assert store_sources(tmp_path / "copy") == {"a.pdf": 2}
    assert (tmp_path / "copy" / "bm25_corpus.json").read_text(encoding="utf-8") == "{}"


# ---------------------------------------------------------------------------
# Review round 1
# ---------------------------------------------------------------------------


async def test_an_empty_knowledge_base_is_published_and_imported_without_an_index(
    collab_server, session, tmp_path
):
    """One created and never given a PDF has no store; it is still shareable."""
    await _use_server(session)
    await _seed_kb(session, sources={}, publications=[])
    with api_client(session) as (client, _):
        res = await client.post("/knowledge-bases/salt-papers/publish", json={})
    assert res.status_code == 200, res.text
    item_id = uuid.UUID(res.json()["knowledge_base"]["remote_id"])
    async with _other_install() as other:
        manifest = (await other.get_version(item_id, 1)).manifest
    assert (manifest["has_index"], manifest["files"]) == (False, 0)

    with api_client(session) as (client, _):
        got = await client.post(
            f"/collaboration/knowledge-bases/{item_id}/import",
            json={"rename_to": "salt-papers-bea"},
        )
    assert got.status_code == 200, got.text
    rag_db = Path(got.json()["knowledge_base"]["rag_db_path"])
    assert rag_db.is_dir() and list(rag_db.iterdir()) == []


async def test_two_imports_of_one_knowledge_base_do_not_interleave(
    collab_server, engine, session, tmp_path
):
    """The second waits for the first, then finds it imported: one store, named by CURRENT."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.services.knowledge_base_collaboration import (
        import_knowledge_base,
    )

    await _use_server(session)
    await session.commit()  # the two imports have sessions of their own
    local_row = await local_settings.get_public(session)
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")

    async def one():
        async with AsyncSession(engine) as s:
            return await import_knowledge_base(s, local_row, item_id)

    results = await asyncio.gather(one(), one(), return_exceptions=True)
    statuses = sorted(
        r.status if not isinstance(r, Exception) else type(r).__name__ for r in results
    )
    assert statuses in (["imported", "unchanged"], ["HTTPException", "imported"])
    kb = await _kb(session, "salt-papers")
    kb_dir = settings.knowledge_bases_dir / "salt-papers"
    current = (kb_dir / CURRENT_STORE_FILE).read_text(encoding="utf-8")
    assert Path(kb.rag_db_path) == kb_dir / current / "rag_db"
    assert store_sources(Path(kb.rag_db_path)) == {"a.pdf": 1}
    assert sorted(p.name for p in kb_dir.glob("store-*")) == [current]


async def test_a_project_import_checks_its_knowledge_bases_are_idle_first(
    collab_server, session, tmp_path
):
    from vista_backend.services.project_collaboration import KnowledgeBaseRef

    await _use_server(session)
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
        await publish_kb_elsewhere(
            other, tmp_path, "salt-papers", item_id=kb_id, parent=1
        )
        skill = await other.create_item("skill", "unused", manifest={})
        project = await other.create_item(
            "project",
            "salt-lab",
            manifest={
                "format": 2,
                "name": "salt-lab",
                "knowledge_bases": [
                    KnowledgeBaseRef(
                        slug="salt-papers", remote_id=kb_id, version=2
                    ).model_dump(mode="json")
                ],
            },
        )
    del skill
    with api_client(session) as (client, _):
        await client.post(
            f"/collaboration/knowledge-bases/{kb_id}/import", json={"version": 1}
        )
        kb = await _kb(session, "salt-papers")
        indexer.register_pending(kb.rag_db_path, total=1)
        try:
            res = await client.post(
                f"/collaboration/projects/{project.id}/import", json={}
            )
        finally:
            indexer.clear_progress(kb.rag_db_path)
    assert res.status_code == 409
    assert "is indexing" in res.json()["detail"]
    assert (await _kb(session, "salt-papers")).remote_version == 1


async def test_a_project_import_checks_the_disk_first(
    collab_server, session, tmp_path, monkeypatch
):
    import shutil
    from collections import namedtuple

    from vista_backend.services.project_collaboration import KnowledgeBaseRef

    await _use_server(session)
    async with _other_install() as other:
        kb_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
        project = await other.create_item(
            "project",
            "salt-lab",
            manifest={
                "format": 2,
                "name": "salt-lab",
                "knowledge_bases": [
                    KnowledgeBaseRef(
                        slug="salt-papers", remote_id=kb_id, version=1
                    ).model_dump(mode="json")
                ],
            },
        )
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(1, 1, 1024))
    with api_client(session) as (client, _):
        res = await client.post(f"/collaboration/projects/{project.id}/import", json={})
    assert res.status_code == 507
    assert await _kb(session, "salt-papers") is None


async def test_a_snapshot_reads_a_store_whatever_its_path(tmp_path):
    """The read-only URI is built with `as_uri`, so `#`, `%` and `?` are a path."""
    store = tmp_path / "odd #1 %20 ?x" / "rag_db"
    make_store(store, {"a.pdf": ["one"]})
    await indexer.snapshot_store(str(store), tmp_path / "copy")
    assert store_sources(tmp_path / "copy") == {"a.pdf": 1}


async def test_listing_knowledge_bases_does_not_rewrite_their_stores(session):
    """
    Reading citations opens the store, which writes `chroma.sqlite3`; the
    cache is keyed by the file as the read left it, so the next listing reads
    nothing and the MCP server sees no change.
    """
    from vista_backend.api import knowledge_bases as kb_api

    kb_dir = await _seed_kb(session)
    sqlite_file = kb_dir / "rag_db" / "chroma.sqlite3"
    kb_api._CHROMA_CITATIONS_CACHE.clear()
    first = kb_api._read_chroma_citations(str(kb_dir / "rag_db"))
    after_first = sqlite_file.stat().st_mtime_ns
    second = kb_api._read_chroma_citations(str(kb_dir / "rag_db"))
    assert second is first
    assert sqlite_file.stat().st_mtime_ns == after_first


async def test_a_knowledge_base_made_again_ignores_what_a_deletion_left(session):
    kb_dir = settings.knowledge_bases_dir / "salt-papers"
    make_store(kb_dir / "store-dead" / "rag_db", {"old.pdf": ["old"]})
    (kb_dir / CURRENT_STORE_FILE).write_text("store-dead", encoding="utf-8")
    with api_client(session) as (client, _):
        res = await client.post(
            "/knowledge-bases", json={"slug": "salt-papers", "name": "Salt"}
        )
    assert res.status_code == 201, res.text
    assert not (kb_dir / CURRENT_STORE_FILE).exists()
    assert not (kb_dir / "store-dead").exists()


# ---------------------------------------------------------------------------
# Review round 2
# ---------------------------------------------------------------------------


async def test_a_knowledge_base_being_imported_or_published_is_not_deleted(session):
    from vista_backend.services.knowledge_base import kb_lock

    kb_dir = await _seed_kb(session)
    async with kb_lock("salt-papers"):
        with api_client(session) as (client, _):
            res = await client.delete("/knowledge-bases/salt-papers")
    assert res.status_code == 409
    assert "being published or imported" in res.json()["detail"]
    assert (kb_dir / "rag_db" / "chroma.sqlite3").is_file()
    assert await _kb(session, "salt-papers") is not None


async def test_publishing_checks_the_disk_before_packing(
    collab_server, session, monkeypatch
):
    import shutil
    from collections import namedtuple

    await _use_server(session)
    await _seed_kb(session)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(1, 1, 1024))
    with api_client(session) as (client, _):
        res = await client.post("/knowledge-bases/salt-papers/publish", json={})
    assert res.status_code == 507
    assert res.json()["detail"].startswith("Publishing The salt-papers papers needs")
    assert (await _kb(session, "salt-papers")).remote_id is None


# ---------------------------------------------------------------------------
# Review round 3
# ---------------------------------------------------------------------------


def test_a_windows_drive_path_is_not_a_knowledge_base_path():
    """What `PureWindowsPath` would make of it is why `:` is refused."""
    from pathlib import PureWindowsPath

    from vista_backend.services.knowledge_base_collaboration import _member_path

    assert PureWindowsPath("import").joinpath("rag_db", "C:/x").is_absolute()
    member = tarfile.TarInfo("rag_db/C:/x")
    with pytest.raises(ArchiveError):
        _member_path(member)


def test_a_member_landing_outside_is_refused_however_it_got_there(
    tmp_path, monkeypatch
):
    """The containment check after joining, whatever the name check let through."""
    from pathlib import PurePosixPath

    from vista_backend.services import knowledge_base_collaboration as kbc

    monkeypatch.setattr(kbc, "_member_path", lambda m: PurePosixPath("rag_db/../../x"))
    archive = _archive(tmp_path, {"rag_db/chroma.sqlite3": b"x"})
    with pytest.raises(ArchiveError, match="outside"):
        unpack_knowledge_base(archive, tmp_path / "out" / "here", has_index=True)
    assert not (tmp_path / "out" / "x").exists()


async def test_a_new_import_leaves_untracked_data_in_its_folder(
    collab_server, session, tmp_path
):
    """No row describes them, so they are not this knowledge base's to remove."""
    await _use_server(session)
    kb_dir = settings.knowledge_bases_dir / "salt-papers"
    make_kb(kb_dir)  # PDFs and a store, but no row
    async with _other_install() as other:
        item_id = await publish_kb_elsewhere(other, tmp_path, "salt-papers")
    with api_client(session) as (client, _):
        res = await client.post(
            f"/collaboration/knowledge-bases/{item_id}/import", json={}
        )
    assert res.status_code == 200, res.text
    assert (kb_dir / "pdfs" / "a.pdf").is_file()
    assert (kb_dir / "rag_db" / "chroma.sqlite3").is_file()
    assert Path(res.json()["knowledge_base"]["rag_db_path"]).parent.name.startswith(
        "store-"
    )


# ---------------------------------------------------------------------------
# Seeded knowledge bases: one item on the server, however many installations
# ---------------------------------------------------------------------------

SEED = "molten-salt-papers"


async def _seed_seeded(session) -> KnowledgeBaseTable:
    """The molten-salt seed as every installation seeds it: unchanged, never published."""
    kb_dir = settings.knowledge_bases_dir / SEED
    make_kb(kb_dir)
    row = kb_row(kb_dir, SEED, id=MOLTEN_SALT_KB_ID, modified=False)
    row.seed_item = seed_item_id(row.id, row.name, row.description, kb_dir / "pdfs")
    session.add(row)
    await session.commit()
    got = await _kb(session, SEED)
    assert got is not None
    return got


async def _forget_publishing(session) -> None:
    """Make this installation one that never published or imported the seed."""
    row = await _kb(session, SEED)
    assert row is not None
    row.origin_server = row.remote_id = row.remote_version = None
    session.add(row)
    await session.commit()


async def test_the_first_to_publish_a_seed_creates_its_item(collab_server, session):
    await _use_server(session)
    seed = (await _seed_seeded(session)).seed_item
    assert seed is not None
    with api_client(session) as (client, _):
        res = await client.post(f"/knowledge-bases/{SEED}/publish", json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "published"
    assert res.json()["knowledge_base"]["remote_id"] == str(seed)
    async with _other_install() as other:
        assert (await other.get_item(seed)).name == SEED


async def test_a_seed_published_elsewhere_is_linked_without_uploading(
    collab_server, session
):
    await _use_server(session)
    seed = (await _seed_seeded(session)).seed_item
    assert seed is not None
    async with _other_install() as other:
        await other.create_item(KB_KIND, SEED, manifest={}, item_id=seed)
    with api_client(session) as (client, _):
        res = await client.post(f"/knowledge-bases/{SEED}/publish", json={})
    assert res.status_code == 200, res.text
    kb = res.json()["knowledge_base"]
    assert res.json()["status"] == "unchanged"
    assert (kb["origin_server"], kb["remote_id"], kb["remote_version"]) == (
        URL,
        str(seed),
        1,
    )
    assert kb["modified"] is False
    async with _other_install() as other:
        assert [v.version for v in await other.list_versions(seed)] == [1]
        assert len(await other.list_items(KB_KIND)) == 1


async def test_importing_a_seed_links_the_copy_here_without_downloading(
    collab_server, session
):
    await _use_server(session)
    await _seed_seeded(session)
    with api_client(session) as (client, _):
        published = await client.post(f"/knowledge-bases/{SEED}/publish", json={})
    seed = published.json()["knowledge_base"]["remote_id"]
    await _forget_publishing(session)
    with api_client(session) as (client, _):
        listed = (await client.get("/collaboration/knowledge-bases")).json()
        res = await client.post(
            f"/collaboration/knowledge-bases/{seed}/import", json={}
        )
    (entry,) = listed
    assert entry["local"] == {"name": SEED, "remote_version": 1, "modified": False}
    assert entry["name_taken"] is False
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "unchanged"
    kb = res.json()["knowledge_base"]
    assert (kb["slug"], kb["remote_id"], kb["modified"]) == (SEED, seed, False)
    # Nothing was unpacked: the seed's own store is the one searched.
    kb_dir = settings.knowledge_bases_dir / SEED
    assert not (kb_dir / CURRENT_STORE_FILE).exists()
    assert list(kb_dir.glob("store-*")) == []


@pytest.mark.parametrize("linked_first", [True, False])
async def test_a_changed_seed_is_published_as_an_item_of_its_own(
    collab_server, session, linked_first
):
    await _use_server(session)
    seed = (await _seed_seeded(session)).seed_item
    with api_client(session) as (client, _):
        if linked_first:
            await client.post(f"/knowledge-bases/{SEED}/publish", json={})
        await client.put(f"/knowledge-bases/{SEED}", json={"name": "Mine"})
        res = await client.post(f"/knowledge-bases/{SEED}/publish", json={})
        again = await client.put(f"/knowledge-bases/{SEED}", json={"name": "Mine 2"})
        next_one = await client.post(f"/knowledge-bases/{SEED}/publish", json={})
    assert again.status_code == 200, again.text
    assert res.status_code == 200, res.text
    kb = res.json()["knowledge_base"]
    assert res.json()["status"] == "published"
    assert kb["remote_id"] != str(seed)
    assert kb["remote_version"] == 1
    # Linked to its own item from then on: the next change is its version 2.
    assert next_one.json()["knowledge_base"]["remote_id"] == kb["remote_id"]
    assert next_one.json()["knowledge_base"]["remote_version"] == 2
    async with _other_install() as other:
        if linked_first:
            assert [v.version for v in await other.list_versions(seed)] == [1]
        else:
            assert seed not in {i.id for i in await other.list_items(KB_KIND)}


def test_a_seed_item_is_derived_from_what_was_seeded(tmp_path):
    pdfs = tmp_path / "pdfs"
    write_pdf(pdfs / "a.pdf")
    seed = seed_item_id(MOLTEN_SALT_KB_ID, "Papers", None, pdfs)
    assert seed_item_id(MOLTEN_SALT_KB_ID, "Papers", None, pdfs) == seed
    assert seed_item_id(uuid.uuid4(), "Papers", None, pdfs) != seed
    assert seed_item_id(MOLTEN_SALT_KB_ID, "Other", None, pdfs) != seed
    write_pdf(pdfs / "b.pdf")
    assert seed_item_id(MOLTEN_SALT_KB_ID, "Papers", None, pdfs) != seed
