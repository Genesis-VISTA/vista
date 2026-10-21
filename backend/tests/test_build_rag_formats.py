"""
Per-format extraction and the description call in `build_rag`.

The extractors are plain functions of a path, so most tests need neither the
embedding model nor Chroma. The `index_single_file` tests build a TextRAG
through `__new__` with fake collections and a fake embedder: no network and
no model download.
"""

import sys
from pathlib import Path

import fitz
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import build_rag  # noqa: E402

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------- table


def test_table_suffixes_and_case_insensitive_lookup():
    assert set(build_rag.ACCEPTED_SUFFIXES) == {".pdf", ".csv", ".tsv", ".txt", ".md"}
    for name in ("a.PDF", "b.Csv", "c.TSV", "d.TxT", "e.MD"):
        assert build_rag.extractor_for(Path(name)) is not None
    assert build_rag.extractor_for(Path("x.xlsx")) is None


def test_pdf_extracts_with_page_and_kind(tmp_path):
    pdf = tmp_path / "paper.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Molten salt viscosity is measured.")
    doc.save(pdf)
    doc.close()

    passages = build_rag.extractor_for(pdf)(pdf)
    assert passages
    meta = passages[0]["metadata"]
    assert meta["source"] == "paper.pdf"
    assert meta["page"] == 0
    assert meta["chunk_id"] == 0
    assert meta["kind"] == "pdf"
    assert "viscosity" in passages[0]["text"]


# ----------------------------------------------------------------------- CSV


def _write_csv(path: Path, n_rows: int) -> Path:
    lines = ["salt,temp_k,density"]
    lines += [f"salt{i},{300 + i},{1.5 + i / 1000}" for i in range(1, n_rows + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_csv_summary_and_windows(tmp_path):
    path = _write_csv(tmp_path / "salts.csv", 120)
    passages = build_rag.extractor_for(path)(path)

    summary, *windows = passages
    assert summary["metadata"] == {
        "source": "salts.csv",
        "type": "text",
        "kind": "csv",
        "rows": "summary",
        "chunk_id": 0,
        "row_count": 120,
    }
    assert "salt (text)" in summary["text"]
    assert "temp_k (int)" in summary["text"]
    assert "density (float)" in summary["text"]
    assert "Data rows: 120" in summary["text"]

    assert [w["metadata"]["rows"] for w in windows] == ["1-50", "51-100", "101-120"]
    for w in windows:
        assert w["text"].startswith("salt,temp_k,density\n")
        assert w["metadata"]["kind"] == "csv"
        assert w["metadata"]["source"] == "salts.csv"
    assert "salt120," in windows[-1]["text"]


def test_huge_csv_is_capped_but_counted(tmp_path):
    path = _write_csv(tmp_path / "big.csv", 50_000)
    summary, *windows = build_rag.extractor_for(path)(path)

    assert len(windows) == build_rag.CSV_MAX_WINDOWS == 200
    assert windows[-1]["metadata"]["rows"] == "9951-10000"
    assert summary["metadata"]["row_count"] == 50_000
    assert "Data rows: 50000" in summary["text"]
    assert "only the first 10000 rows are indexed" in summary["text"]
    assert "50000 data rows in total" in summary["text"]


def test_bom_csv_with_quoted_comma_and_tsv(tmp_path):
    csv_path = tmp_path / "q.csv"
    csv_path.write_bytes(
        b'\xef\xbb\xbfname,note\nNaCl,"melts, then boils"\nKCl,plain\n'
    )
    summary, window = build_rag.extractor_for(csv_path)(csv_path)
    assert "﻿" not in summary["text"]
    assert "Columns (2): name (text), note (text)" in summary["text"]
    assert window["text"].startswith("name,note\n")
    assert '"melts, then boils"' in window["text"]

    tsv_path = tmp_path / "t.tsv"
    tsv_path.write_text("a\tb\tc\n1\t2.5\tx\n", encoding="utf-8")
    summary, window = build_rag.extractor_for(tsv_path)(tsv_path)
    assert summary["metadata"]["kind"] == "tsv"
    assert "Columns (3): a (int), b (float), c (text)" in summary["text"]
    assert window["text"].startswith("a\tb\tc\n")


def test_wide_csv_summary_is_bounded(tmp_path):
    path = tmp_path / "wide.csv"
    cols = [f"c{i}" for i in range(100)]
    path.write_text(
        ",".join(cols) + "\n" + ",".join(["word " * 3] * 100) + "\n", encoding="utf-8"
    )
    summary, window = build_rag.extractor_for(path)(path)
    assert "and 70 more columns" in summary["text"]
    assert "c29 (text)" in summary["text"] and "c30 (text)" not in summary["text"]
    assert len(summary["text"].split()) <= build_rag.TEXT_CHUNK_WORDS + 40
    assert len(window["text"].split()) <= build_rag.TEXT_CHUNK_WORDS


# ------------------------------------------------------------------ TXT / MD


def test_markdown_sentence_lands_in_a_passage(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(
        "# Notes\n\nThe eutectic point of the mixture is 727 K.\n", encoding="utf-8"
    )
    passages = build_rag.extractor_for(path)(path)
    assert any("eutectic point of the mixture" in p["text"] for p in passages)
    assert passages[0]["metadata"] == {
        "source": "notes.md",
        "chunk_id": 0,
        "type": "text",
        "kind": "markdown",
    }

    txt = tmp_path / "plain.TXT"
    txt.write_text("hello world", encoding="utf-8")
    assert build_rag.extractor_for(txt)(txt)[0]["metadata"]["kind"] == "text"


# ------------------------------------------------------ description and index


class _FakeCollection:
    def __init__(self):
        self.upserts: list[dict] = []

    def get(self, **_kwargs):
        return {"ids": []}

    def delete(self, **_kwargs):
        pass

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


def _rag(*, extract_citations=True, credentials=None):
    rag = build_rag.TextRAG.__new__(build_rag.TextRAG)
    rag.force_reindex = False
    rag.extract_citations = extract_citations
    rag.llm_credentials = credentials
    rag.text_collection = _FakeCollection()
    rag.citation_collection = _FakeCollection()
    rag.embed_text_batched = lambda texts, **_k: [[0.0] for _ in texts]
    rag.embed_text = lambda texts, **_k: [[0.0] for _ in texts]
    return rag


def test_description_lands_in_summary_and_citation_row(tmp_path, monkeypatch):
    path = _write_csv(tmp_path / "salts.csv", 10)
    prompts: list[str] = []

    def fake_send(prompt, **kwargs):
        prompts.append(prompt)
        return "Densities of salts at several temperatures."

    monkeypatch.setattr(build_rag, "send_prompt_to_chatgpt", fake_send)
    creds = build_rag.LLMCredentials(api_key="sk-test")
    rag = _rag(credentials=creds)

    result = rag.index_single_file(path)

    assert result["status"] == "indexed"
    assert result["citation_status"] == "extracted"
    assert len(prompts) == 1 and "Columns (3)" in prompts[0]
    texts = rag.text_collection.upserts[0]["documents"]
    assert "Description: Densities of salts" in texts[0]
    row = rag.citation_collection.upserts[0]
    meta = row["metadatas"][0]
    assert meta["type"] == "citation"
    assert meta["record"] == "dataset"
    assert meta["kind"] == "csv"
    assert meta["row_count"] == 10
    assert meta["title"] == "salts.csv"
    assert meta["abstract"] == "Densities of salts at several temperatures."
    assert meta["authors"] == "" and meta["doi"] == ""
    assert row["ids"] == ["salts.csv_citation"]
    assert rag.text_collection.upserts[0]["ids"][0] == "salts.csv_text_0"


def test_text_file_description_is_a_note(tmp_path, monkeypatch):
    path = tmp_path / "n.md"
    path.write_text("some notes about salts", encoding="utf-8")
    monkeypatch.setattr(build_rag, "send_prompt_to_chatgpt", lambda p, **k: "A note.")
    rag = _rag(credentials=build_rag.LLMCredentials(api_key="sk-test"))

    result = rag.index_single_file(path)

    assert result["citation_status"] == "extracted"
    assert rag.citation_collection.upserts[0]["metadatas"][0]["record"] == "note"
    assert rag.citation_collection.upserts[0]["metadatas"][0]["kind"] == "markdown"


def test_disabled_credentials_make_no_call(tmp_path, monkeypatch):
    path = _write_csv(tmp_path / "salts.csv", 10)
    calls: list[str] = []
    monkeypatch.setattr(
        build_rag, "send_prompt_to_chatgpt", lambda p, **k: calls.append(p) or "x"
    )
    rag = _rag(credentials=build_rag.LLMCredentials(disabled=True))

    result = rag.index_single_file(path)

    assert calls == []
    assert result["status"] == "indexed"
    assert result["citation_status"] == "disabled"
    assert rag.citation_collection.upserts == []
    assert "Description:" not in rag.text_collection.upserts[0]["documents"][0]


def test_failed_description_does_not_block_indexing(tmp_path, monkeypatch):
    path = _write_csv(tmp_path / "salts.csv", 10)

    def boom(prompt, **kwargs):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(build_rag, "send_prompt_to_chatgpt", boom)
    rag = _rag(credentials=build_rag.LLMCredentials(api_key="sk-test"))

    result = rag.index_single_file(path)

    assert result["status"] == "indexed"
    assert result["citation_status"] == "failed"
    assert result["chunk_count"] > 0
    assert rag.citation_collection.upserts == []


def test_unsupported_suffix_fails_cleanly(tmp_path):
    path = tmp_path / "x.xlsx"
    path.write_bytes(b"x")
    result = _rag().index_single_file(path)
    assert result["status"] == "failed"
    assert "Unsupported" in result["error"]
