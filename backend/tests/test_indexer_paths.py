"""
Path handling in `indexer.index_publications`.

Filenames are relative to `pdfs_dir` and may be nested (vista-data's
`molten-salt-papers` stores PDFs in per-topic subdirectories), so the
indexer resolves each entry under `pdfs_dir` and drops anything that
escapes it. TextRAG is faked — these tests are about which paths reach
the embedding step, not about chunking or chroma.
"""

from pathlib import Path

import pytest

from vista_backend.utils import indexer

pytestmark = [pytest.mark.anyio, pytest.mark.unit]


class FakeTextRAG:
    """Records the PDF paths handed to it instead of embedding them."""

    def __init__(self, *, pdf_folder, db_path, extract_citations, force_reindex):
        self.pdf_folder = pdf_folder
        self.db_path = db_path
        self.extract_citations = extract_citations
        self.force_reindex = force_reindex
        self.indexed: list[Path] = []

    def index_single_pdf(self, pdf_path, progress_cb=None):
        self.indexed.append(Path(pdf_path))
        return {
            "filename": Path(pdf_path).name,
            "status": "indexed",
            "error": None,
            "chunk_count": 1,
            "citation": None,
            "citation_status": "disabled",
        }


@pytest.fixture
def fake_rag(monkeypatch):
    """Swap in FakeTextRAG and expose the instance the indexer builds."""
    built: list[FakeTextRAG] = []

    class _Cls(FakeTextRAG):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            built.append(self)

    indexer._text_rag_instances.clear()
    monkeypatch.setattr(indexer, "_get_text_rag_cls", lambda: _Cls)
    yield built
    indexer._text_rag_instances.clear()


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n")
    return path


async def _index(tmp_path: Path, filenames: list[str]) -> list[dict]:
    return await indexer.index_publications(
        rag_db_path=str(tmp_path / "rag_db"),
        pdfs_dir=str(tmp_path / "pdfs"),
        filenames=filenames,
        extract_citations=False,
    )


async def test_nested_pdfs_are_indexed(tmp_path, fake_rag):
    """The regression: PDFs in subdirectories must reach TextRAG."""
    pdfs = tmp_path / "pdfs"
    _pdf(pdfs / "top.pdf")
    _pdf(pdfs / "thermo/nested.pdf")
    _pdf(pdfs / "thermo/2024/deep.pdf")

    results = await _index(
        tmp_path, ["top.pdf", "thermo/nested.pdf", "thermo/2024/deep.pdf"]
    )

    assert [r["status"] for r in results] == ["indexed"] * 3
    assert fake_rag[0].indexed == [
        pdfs / "top.pdf",
        pdfs / "thermo/nested.pdf",
        pdfs / "thermo/2024/deep.pdf",
    ]


async def test_traversal_entries_are_dropped(tmp_path, fake_rag):
    pdfs = tmp_path / "pdfs"
    _pdf(pdfs / "good.pdf")
    _pdf(tmp_path / "outside.pdf")

    results = await _index(
        tmp_path,
        ["../outside.pdf", "thermo/../../outside.pdf", "", ".", "..", "good.pdf"],
    )

    assert [r["filename"] for r in results] == ["good.pdf"]
    assert fake_rag[0].indexed == [pdfs / "good.pdf"]


async def test_absolute_paths_are_rejected(tmp_path, fake_rag):
    _pdf(tmp_path / "pdfs" / "good.pdf")
    outside = _pdf(tmp_path / "outside.pdf")

    assert await _index(tmp_path, [str(outside)]) == []
    assert fake_rag == [], "TextRAG should not be built when nothing survives cleaning"


async def test_symlinks_out_of_pdfs_dir_are_rejected(tmp_path, fake_rag):
    pdfs = tmp_path / "pdfs"
    _pdf(pdfs / "good.pdf")
    outside = _pdf(tmp_path / "outside.pdf")
    try:
        (pdfs / "escape.pdf").symlink_to(outside)
    except OSError, NotImplementedError:
        pytest.skip("filesystem does not support symlinks")

    results = await _index(tmp_path, ["escape.pdf", "good.pdf"])

    assert [r["filename"] for r in results] == ["good.pdf"]
    assert fake_rag[0].indexed == [pdfs / "good.pdf"]


async def test_missing_nested_pdf_is_reported_as_failed(tmp_path, fake_rag):
    pdfs = tmp_path / "pdfs"
    _pdf(pdfs / "thermo/present.pdf")

    results = await _index(tmp_path, ["thermo/absent.pdf", "thermo/present.pdf"])

    missing, present = results
    assert missing["status"] == "failed"
    assert missing["filename"] == "thermo/absent.pdf"
    assert "File not found" in missing["error"]
    assert present["status"] == "indexed"
    assert fake_rag[0].indexed == [pdfs / "thermo/present.pdf"]
