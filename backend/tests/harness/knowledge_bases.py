"""
Knowledge bases on disk for the collaboration tests: a real ChromaDB store with
a few chunks (embedded by hand, so no model is loaded), PDFs beside it, and a
knowledge base published by another installation.
"""

import uuid
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from vista_backend.db.schemas import KnowledgeBaseTable
from vista_backend.services.collaboration import CollaborationClient
from vista_backend.services.knowledge_base_collaboration import (
    KB_KIND,
    KnowledgeBaseManifest,
    pack_knowledge_base,
)
from vista_backend.utils.indexer import StoreFormat

FORMAT = StoreFormat(
    embedding_model="test/embedder",
    embedding_revision="0" * 40,
    chromadb="1.5.9",
)
""" What `indexer.store_format` answers in these tests (see `same_format`). """


def same_format(monkeypatch, fmt: StoreFormat = FORMAT) -> None:
    """Make this installation index with `fmt`, without importing `build_rag`."""
    from vista_backend.utils import indexer

    async def store_format() -> StoreFormat:
        return fmt

    monkeypatch.setattr(indexer, "store_format", store_format)


def make_store(db_path: Path, sources: dict[str, list[str]]) -> None:
    """A store at `db_path` holding, for each PDF name, its chunks and a citation."""
    db_path.mkdir(parents=True, exist_ok=True)
    with chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    ) as client:
        text = client.get_or_create_collection(
            "text_chunks", metadata={"hnsw:space": "cosine"}
        )
        citations = client.get_or_create_collection(
            "citations", metadata={"hnsw:space": "cosine"}
        )
        for n, (source, chunks) in enumerate(sources.items()):
            stem = Path(source).stem
            if chunks:
                text.add(
                    ids=[f"{stem}_text_{i}" for i in range(len(chunks))],
                    documents=chunks,
                    metadatas=[{"source": source, "page": 0} for _ in chunks],
                    embeddings=[[1.0, float(n), float(i)] for i in range(len(chunks))],
                )
            citations.add(
                ids=[f"{stem}_citation"],
                documents=[f"Title of {source}"],
                metadatas=[{"source": source, "title": f"Title of {source}"}],
                embeddings=[[0.0, 1.0, float(n)]],
            )


def store_sources(db_path: Path) -> dict[str, int]:
    """How many chunks the store holds per PDF."""
    counts: dict[str, int] = {}
    with chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    ) as client:
        got = client.get_collection("text_chunks").get(include=["metadatas"])
        for meta in got["metadatas"] or []:
            source = str(meta["source"])
            counts[source] = counts.get(source, 0) + 1
    return counts


def write_pdf(path: Path, body: bytes = b"%PDF-1.4 test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def kb_row(kb_dir: Path, slug: str, **fields) -> KnowledgeBaseTable:
    """A knowledge base row on `kb_dir/{pdfs,rag_db}`, indexed and never published."""
    return KnowledgeBaseTable(
        slug=slug,
        name=fields.pop("name", f"The {slug} papers"),
        pdfs_dir=str(kb_dir / "pdfs"),
        rag_db_path=str(kb_dir / "rag_db"),
        publications=fields.pop(
            "publications",
            [{"filename": "a.pdf", "size": 13, "index_status": "indexed"}],
        ),
        build_status="ready",
        created_at="2026-10-01T00:00:00+00:00",
        updated_at="2026-10-01T00:00:00+00:00",
        **fields,
    )


def make_kb(kb_dir: Path, sources: dict[str, list[str]] | None = None) -> None:
    """
    PDFs and a store under `kb_dir`, as the backend lays a knowledge base out.
    With no sources, an empty knowledge base: folders and no store.
    """
    sources = {"a.pdf": ["molten fluoride salt"]} if sources is None else sources
    for name in sources:
        write_pdf(kb_dir / "pdfs" / name)
    (kb_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    (kb_dir / "rag_db").mkdir(parents=True, exist_ok=True)
    if sources:
        make_store(kb_dir / "rag_db", sources)


def kb_manifest(slug: str, archive: Path, **fields) -> dict:
    return KnowledgeBaseManifest(
        slug=slug,
        name=fields.pop("name", f"Their {slug}"),
        description=fields.pop("description", "Their papers."),
        publications=fields.pop(
            "publications",
            [{"filename": "a.pdf", "size": 13, "index_status": "indexed"}],
        ),
        files=fields.pop("files", 1),
        size=fields.pop("size", 13),
        archive_size=archive.stat().st_size,
        embedding_model=fields.pop("embedding_model", FORMAT.embedding_model),
        embedding_revision=fields.pop("embedding_revision", FORMAT.embedding_revision),
        chromadb=fields.pop("chromadb", FORMAT.chromadb),
        **fields,
    ).model_dump(mode="json")


async def publish_kb_elsewhere(
    client: CollaborationClient,
    work: Path,
    slug: str,
    *,
    sources: dict[str, list[str]] | None = None,
    item_id: uuid.UUID | None = None,
    parent: int | None = None,
    **fields,
) -> uuid.UUID:
    """
    Publish a knowledge base from another installation: version 1 of a new
    item, or the next on top of `parent`. Returns the item id.
    """
    kb_dir = work / uuid.uuid4().hex
    make_kb(kb_dir, sources)
    archive = work / f"{uuid.uuid4().hex}.tar"
    store = kb_dir / "rag_db" if sources != {} else None
    pack_knowledge_base(kb_dir / "pdfs", store, archive)
    if store is None:
        fields.setdefault("has_index", False)
        fields.setdefault("files", 0)
        fields.setdefault("publications", [])
    ref = await client.upload_large_blob(archive)
    manifest = kb_manifest(slug, archive, **fields)
    if item_id is None:
        item = await client.create_item(KB_KIND, slug, manifest=manifest, blob_ref=ref)
        return item.id
    assert parent is not None
    await client.publish_version(
        item_id, parent_version=parent, manifest=manifest, blob_ref=ref
    )
    return item_id
