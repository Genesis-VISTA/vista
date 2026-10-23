"""
File identity in `build_rag`: rows are keyed on the `source` metadata (the full
file name), not on id.

Uses a real Chroma `PersistentClient` in `tmp_path` with the embedder stubbed
to deterministic fixed-dimension vectors (no model download, no network) and
the LLM calls stubbed.
"""

import hashlib
import json
import sys
from pathlib import Path

import chromadb
import fitz
import numpy as np
import pytest
from chromadb.config import Settings as ChromaSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import build_rag  # noqa: E402

pytestmark = pytest.mark.unit

DIM = 8


class _FakeEncoder:
    """Deterministic, non-zero DIM-vectors; counts the texts it was asked for."""

    def __init__(self):
        self.encoded = 0

    def encode(self, texts, prompt=None, convert_to_numpy=True):
        self.encoded += len(texts)
        rows = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            rows.append([1.0 + digest[i] / 255.0 for i in range(DIM)])
        return np.array(rows, dtype="float32")


def _rag(tmp_path, monkeypatch, *, force=False):
    monkeypatch.setattr(
        build_rag, "send_prompt_to_chatgpt", lambda p, **k: "A short description."
    )
    rag = build_rag.TextRAG.__new__(build_rag.TextRAG)
    rag.pdf_folder = tmp_path
    rag.db_path = str(tmp_path / "db")
    rag.force_reindex = force
    rag.extract_citations = True
    rag.llm_credentials = build_rag.LLMCredentials(api_key="sk-test")
    rag.text_encoder = _FakeEncoder()
    rag.client = chromadb.PersistentClient(
        path=rag.db_path, settings=ChromaSettings(anonymized_telemetry=False)
    )
    rag.text_collection = rag.client.get_or_create_collection(
        name="text_chunks", metadata={"hnsw:space": "cosine"}
    )
    rag.citation_collection = rag.client.get_or_create_collection(
        name="citations", metadata={"hnsw:space": "cosine"}
    )
    rag._extract_citation = lambda path: {"title": f"Title of {path.name}"}
    return rag


def _pdf(path: Path, text: str = "Molten salt viscosity is measured.") -> Path:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def _csv(path: Path) -> Path:
    lines = ["salt,temp_k"] + [f"s{i},{300 + i}" for i in range(5)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ids(coll, source: str) -> list[str]:
    return sorted(coll.get(where={"source": source})["ids"])


def _seed_old_style_pdf(rag, filename: str):
    """Rows as the pre-change indexer wrote them: ids keyed on the stem."""
    stem = Path(filename).stem
    vec = [[1.0] * DIM, [2.0] * DIM]
    rag.text_collection.upsert(
        ids=[f"{stem}_text_0", f"{stem}_text_1"],
        embeddings=vec,
        documents=["old chunk 0", "old chunk 1"],
        metadatas=[{"source": filename, "type": "text", "chunk_id": i} for i in (0, 1)],
    )
    rag.citation_collection.upsert(
        ids=[f"{stem}_citation"],
        embeddings=[[1.0] * DIM],
        documents=["old citation"],
        metadatas=[{"source": filename, "type": "citation", "title": "Old"}],
    )


def test_same_stem_pdf_and_csv_coexist(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch)
    pdf = _pdf(tmp_path / "foo.pdf")
    csv = _csv(tmp_path / "foo.csv")

    assert rag.index_single_file(pdf)["status"] == "indexed"
    assert rag.index_single_file(csv)["status"] == "indexed"

    assert _ids(rag.text_collection, "foo.pdf") == ["foo.pdf_text_0"]
    csv_chunks = _ids(rag.text_collection, "foo.csv")
    assert csv_chunks and all(i.startswith("foo.csv_text_") for i in csv_chunks)
    assert _ids(rag.citation_collection, "foo.pdf") == ["foo.pdf_citation"]
    assert _ids(rag.citation_collection, "foo.csv") == ["foo.csv_citation"]


def test_second_index_skips_without_embedding(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch)
    csv = _csv(tmp_path / "foo.csv")
    assert rag.index_single_file(csv)["status"] == "indexed"
    embedded = rag.text_encoder.encoded

    def no_llm(*a, **k):
        raise AssertionError("LLM called on a skipped file")

    monkeypatch.setattr(build_rag, "send_prompt_to_chatgpt", no_llm)
    result = rag.index_single_file(csv)

    assert result["status"] == "skipped"
    assert result["citation_status"] == "skipped"
    assert result["citation"]["source"] == "foo.csv"
    assert rag.text_encoder.encoded == embedded


def test_forced_reindex_replaces_old_style_rows(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch, force=True)
    _seed_old_style_pdf(rag, "foo.pdf")
    assert rag.text_collection.count() == 2

    result = rag.index_single_file(_pdf(tmp_path / "foo.pdf"))

    assert result["status"] == "indexed"
    assert _ids(rag.text_collection, "foo.pdf") == ["foo.pdf_text_0"]
    assert _ids(rag.citation_collection, "foo.pdf") == ["foo.pdf_citation"]
    assert rag.text_collection.count() == 1
    assert rag.citation_collection.count() == 1


def test_old_style_store_skips_old_file_and_embeds_only_new(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch)
    _seed_old_style_pdf(rag, "foo.pdf")

    skipped = rag.index_single_file(_pdf(tmp_path / "foo.pdf"))
    assert skipped["status"] == "skipped"
    assert skipped["citation"]["title"] == "Old"
    assert rag.text_encoder.encoded == 0

    assert rag.index_single_file(_csv(tmp_path / "new.csv"))["status"] == "indexed"
    new_chunks = len(_ids(rag.text_collection, "new.csv"))
    assert rag.text_encoder.encoded >= new_chunks > 0
    # the old rows are untouched
    assert _ids(rag.text_collection, "foo.pdf") == ["foo_text_0", "foo_text_1"]

    before = rag.text_encoder.encoded
    assert rag.index_single_file(_pdf(tmp_path / "foo.pdf"))["status"] == "skipped"
    assert rag.text_encoder.encoded == before


def test_delete_source_removes_rows_from_both_collections(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch)
    rag.index_single_file(_pdf(tmp_path / "foo.pdf"))
    rag.index_single_file(_csv(tmp_path / "foo.csv"))

    removed = rag.delete_source("foo.pdf")

    assert removed == 2  # one chunk + one citation row
    assert _ids(rag.text_collection, "foo.pdf") == []
    assert _ids(rag.citation_collection, "foo.pdf") == []
    assert _ids(rag.text_collection, "foo.csv")  # the other file is untouched
    assert rag.delete_source("missing.pdf") == 0


def test_delete_source_rebuilds_bm25_corpus(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch)
    rag.index_single_file(_pdf(tmp_path / "foo.pdf"))
    rag.index_single_file(_csv(tmp_path / "foo.csv"))
    rag.write_bm25_corpus()
    corpus = Path(rag.db_path) / "bm25_corpus.json"

    def corpus_ids():
        data = json.loads(corpus.read_text(encoding="utf-8"))
        return {d["id"] for d in data["documents"]}

    assert "foo.pdf_text_0" in corpus_ids()
    rag.delete_source("foo.pdf")
    ids = corpus_ids()
    assert "foo.pdf_text_0" not in ids
    assert any(i.startswith("foo.csv_text_") for i in ids)

    rag.delete_source("foo.csv")  # nothing left: the stale corpus goes away
    assert not corpus.exists()


def test_delete_source_without_corpus_file_writes_none(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch)
    rag.index_single_file(_pdf(tmp_path / "foo.pdf"))
    rag.delete_source("foo.pdf")
    assert not (Path(rag.db_path) / "bm25_corpus.json").exists()


@pytest.mark.parametrize("mode", ["failed", "disabled"])
def test_forced_reindex_keeps_citation_when_no_new_one(tmp_path, monkeypatch, mode):
    rag = _rag(tmp_path, monkeypatch, force=True)
    _seed_old_style_pdf(rag, "foo.pdf")
    if mode == "failed":

        def boom(path):
            raise RuntimeError("endpoint down")

        rag._extract_citation = boom
    else:
        rag.extract_citations = False

    result = rag.index_single_file(_pdf(tmp_path / "foo.pdf"))

    assert result["status"] == "indexed"
    assert _ids(rag.text_collection, "foo.pdf") == ["foo.pdf_text_0"]  # replaced
    assert _ids(rag.citation_collection, "foo.pdf") == ["foo_citation"]  # kept


def test_forced_reindex_with_zero_chunks_drops_old_text_rows(tmp_path, monkeypatch):
    rag = _rag(tmp_path, monkeypatch, force=True)
    rag.index_single_file(_csv(tmp_path / "foo.csv"))
    assert _ids(rag.text_collection, "foo.csv")
    (tmp_path / "foo.csv").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        build_rag, "extractor_for", lambda path: lambda p, description=None: []
    )

    result = rag.index_single_file(tmp_path / "foo.csv")

    assert result["status"] == "indexed"
    assert result["chunk_count"] == 0
    assert _ids(rag.text_collection, "foo.csv") == []
