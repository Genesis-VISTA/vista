"""The agent's `fetch_kb_file` core: copy a knowledge-base file into the workspace."""

import pytest
from pydantic_ai.exceptions import ModelRetry

from vista_backend.agents.kb_fetch import copy_kb_file, kb_prompt_block
from vista_backend.db.schemas import KnowledgeBaseTable

pytestmark = pytest.mark.anyio

CSV = b"salt,mp\nFLiBe,732\nFLiNaK,727\n"


@pytest.fixture
async def kb(session, tmp_path):
    files = tmp_path / "kbs" / "salts" / "pdfs"
    files.mkdir(parents=True)
    (files / "salts.csv").write_bytes(CSV)
    (tmp_path / "kbs" / "secret.csv").write_bytes(b"x")
    row = KnowledgeBaseTable(
        slug="salts",
        name="Salts",
        pdfs_dir=str(files),
        rag_db_path=str(tmp_path / "rag"),
    )
    session.add(row)
    await session.commit()
    return row


@pytest.fixture
def work(tmp_path):
    return tmp_path / "data" / "work"


async def test_copies_attached_file_byte_identical(session, kb, work):
    dest, size = await copy_kb_file(
        session,
        attached=["salts"],
        kb_slug="salts",
        filename="salts.csv",
        work_dir=work,
    )
    assert dest == work / "kb" / "salts" / "salts.csv"
    assert dest.read_bytes() == CSV
    assert size == len(CSV)


async def test_unattached_slug_lists_attached(session, kb, work):
    with pytest.raises(ModelRetry, match="salts"):
        await copy_kb_file(
            session,
            attached=["salts"],
            kb_slug="other",
            filename="salts.csv",
            work_dir=work,
        )
    assert not work.exists()


async def test_no_attached_kbs(session, kb, work):
    with pytest.raises(ModelRetry, match="no knowledge bases"):
        await copy_kb_file(
            session, attached=[], kb_slug="salts", filename="salts.csv", work_dir=work
        )


@pytest.mark.parametrize(
    "name", ["missing.csv", "../secret.csv", "../../kbs/secret.csv"]
)
async def test_missing_or_escaping_file_fails(session, kb, work, name):
    with pytest.raises(ModelRetry):
        await copy_kb_file(
            session, attached=["salts"], kb_slug="salts", filename=name, work_dir=work
        )
    assert not work.exists()


def test_prompt_block_mentions_tool_only_with_kbs():
    assert "fetch_kb_file" in kb_prompt_block(["salts"])
    assert "  - salts" in kb_prompt_block(["salts"])
    assert "fetch_kb_file" not in kb_prompt_block([])
