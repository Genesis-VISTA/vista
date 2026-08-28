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
