"""
PDF discovery in the seed's `_build_knowledge_base`.

vista-data's `molten-salt-papers` keeps its PDFs in subdirectories, so the
seed walks the tree and hands relative paths to the indexer. The indexer
itself is faked here — `test_indexer_paths.py` covers what it does with
those paths.
"""

from pathlib import Path

import pytest

from vista_backend.db.seed import _build_knowledge_base
from vista_backend.utils import indexer

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
def indexer_calls(monkeypatch):
    calls: list[dict] = []

    async def _fake_index_publications(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(indexer, "index_publications", _fake_index_publications)
    return calls


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n")
    return path


async def test_nested_pdfs_are_passed_as_relative_paths(tmp_path, indexer_calls):
    kb_dir = tmp_path / "molten-salt-papers"
    pdfs = kb_dir / "pdfs"
    _pdf(pdfs / "top.pdf")
    _pdf(pdfs / "thermo/nested.pdf")
    _pdf(pdfs / "thermo/2024/deep.pdf")
    (pdfs / "thermo/README.md").write_text("not a paper")

    await _build_knowledge_base(kb_dir)

    (call,) = indexer_calls
    assert call["filenames"] == [
        "thermo/2024/deep.pdf",
        "thermo/nested.pdf",
        "top.pdf",
    ]
    assert call["pdfs_dir"] == str(pdfs)
    assert call["rag_db_path"] == str(kb_dir / "rag_db")


async def test_existing_chroma_db_short_circuits_the_build(tmp_path, indexer_calls):
    kb_dir = tmp_path / "molten-salt-papers"
    _pdf(kb_dir / "pdfs/thermo/nested.pdf")
    rag_db = kb_dir / "rag_db"
    rag_db.mkdir(parents=True)
    (rag_db / "chroma.sqlite3").touch()

    await _build_knowledge_base(kb_dir)

    assert indexer_calls == []


async def test_nested_publication_opens_by_basename(tmp_path, monkeypatch):
    """
    A citation from a corpus organised into subdirectories has to open.

    Retrieval reports each chunk's source as a bare filename, while the bundled
    molten-salt corpus keeps every paper under a per-topic folder -- so a
    basename-only lookup against the top of the folder finds nothing and every
    citation is a dead link.
    """
    from pathlib import Path

    from fastapi import HTTPException

    from vista_backend.api import knowledge_bases as kb_api

    pdfs = tmp_path / "pdfs"
    (pdfs / "lit-MS" / "Sub-MS-breed").mkdir(parents=True)
    nested = pdfs / "lit-MS" / "Sub-MS-breed" / "paper.pdf"
    nested.write_bytes(b"%PDF-1.4 nested")

    class _KB:
        pdfs_dir = str(pdfs)

    async def _get_kb(_session, _slug):
        return _KB()

    monkeypatch.setattr(kb_api.kb_service, "get_kb", _get_kb)

    response = await kb_api.download_publication("any-slug", "paper.pdf", None)
    assert Path(response.path) == nested

    # A name that is nowhere in the tree is still a 404, and a path separator is
    # still rejected outright.
    with pytest.raises(HTTPException) as absent:
        await kb_api.download_publication("any-slug", "absent.pdf", None)
    assert absent.value.status_code == 404
    with pytest.raises(HTTPException) as traversal:
        await kb_api.download_publication("any-slug", "../escape.pdf", None)
    assert traversal.value.status_code == 400
