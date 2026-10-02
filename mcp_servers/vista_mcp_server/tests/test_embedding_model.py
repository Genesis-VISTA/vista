"""
The retrieval encoder must need no account and no network.

`google/embeddinggemma-300m` is gated: without an accepted licence and an
`HF_TOKEN` the RAG server raises `GatedRepoError` in its lifespan and the whole
MCP process fails to boot. `microsoft/harrier-oss-v1-270m` (MIT, ungated,
640-dimension) replaces it so a researcher's first run needs no HuggingFace
account at all.

Harrier is also instruction-tuned, so the tests below cover the second half of
using it correctly: queries carry an `Instruct: ...\\nQuery: ` prefix and
documents carry none.

The model-name and instruction tests are hermetic. The rest are marked `live`
because they load the real 270M-parameter model, which `ci-local.sh`'s hermetic
filter excludes from PR CI. Prime the cache first with:

    HF_HOME=data/huggingface uv run python -c \
      "from sentence_transformers import SentenceTransformer as S; \
       S('microsoft/harrier-oss-v1-270m')"
"""

import ast
import os
from pathlib import Path

import pytest

from vista_mcp_server import rag_mcp
from vista_mcp_server.config import settings

# Both encoders must name the same model or a Chroma collection built by one is
# unqueryable by the other.
EXPECTED_MODEL = "microsoft/harrier-oss-v1-270m"
EXPECTED_DIM = 640

BUILD_RAG = Path(__file__).resolve().parents[3] / "build_rag.py"


def _build_rag_module() -> ast.Module:
    assert BUILD_RAG.is_file(), f"expected build_rag.py at {BUILD_RAG}"
    return ast.parse(BUILD_RAG.read_text(encoding="utf-8"))


def _build_rag_constant(name: str) -> str:
    """
    Read a module-level string constant out of `build_rag.py` without
    importing it. Adjacent string literals are folded by the parser, so a
    wrapped constant still comes back as one value.
    """
    for node in ast.walk(_build_rag_module()):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"build_rag.py has no module-level {name}")


def _build_rag_function(name: str) -> ast.FunctionDef:
    for node in ast.walk(_build_rag_module()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"build_rag.py has no function {name}")


def test_query_encoder_names_the_ungated_model() -> None:
    assert settings.rag_model == EXPECTED_MODEL


def test_indexing_encoder_names_the_same_model() -> None:
    """
    Guards the one drift this design accepts: two hardcoded copies of the model
    name, in two services, that no import connects. `build_rag.py` lives at the
    repo root and is not importable from here, so it is asserted by reading it.
    """
    assert BUILD_RAG.is_file(), f"expected build_rag.py at {BUILD_RAG}"
    assert f'text_model: str = "{EXPECTED_MODEL}"' in BUILD_RAG.read_text(
        encoding="utf-8"
    ), (
        f"build_rag.py's text_model default has drifted from "
        f"settings.rag_model ({settings.rag_model!r})"
    )


def test_both_encoders_pin_the_same_revision() -> None:
    """
    The same two copies, for the weights' commit. A store embedded with one
    snapshot and queried with another returns vectors that no longer line up,
    with no error to say so.
    """
    revision = settings.rag_model_revision
    assert len(revision) == 40 and all(c in "0123456789abcdef" for c in revision), (
        f"rag_model_revision must be a full commit hash, got {revision!r}"
    )
    assert f'text_model_revision: str = "{revision}"' in BUILD_RAG.read_text(
        encoding="utf-8"
    ), (
        f"build_rag.py's text_model_revision default has drifted from "
        f"settings.rag_model_revision ({revision!r})"
    )


# ---------------------------------------------------------------------------
# Query-side instruction
#
# The encoder is instruction-tuned: its card's FAQ says a query with no
# `Instruct:` prefix "will see a performance degradation", and its
# `config_sentence_transformers.json` leaves `default_prompt_name` null, so
# nothing is prepended unless a caller asks. Documents take no instruction,
# which is why these assertions are all about the query path.
# ---------------------------------------------------------------------------


def test_query_prompt_uses_the_trained_instruct_format() -> None:
    prompt = rag_mcp.query_prompt()
    assert prompt.startswith("Instruct: ")
    assert prompt.endswith("\nQuery: ")
    assert settings.rag_query_instruction.strip() in prompt


def test_query_embedding_passes_the_instruction(monkeypatch) -> None:
    """`_embed` must hand the prompt to the encoder, not just compute one."""
    import numpy as np

    calls: list[dict] = []

    class _FakeEncoder:
        def encode(self, texts, **kwargs):
            calls.append({"texts": texts, **kwargs})
            return np.zeros((1, EXPECTED_DIM), dtype=np.float32)

    monkeypatch.setattr(rag_mcp, "_encoder", _FakeEncoder())
    rag_mcp._embed("melting point of FLiBe")

    assert len(calls) == 1
    assert calls[0]["prompt"] == rag_mcp.query_prompt()
    assert calls[0]["texts"] == ["melting point of FLiBe"]


def test_indexing_service_uses_the_same_instruction() -> None:
    """
    Second copy of the model-name drift problem: `build_rag.py`'s query helpers
    must prefix with the same sentence the MCP server does, or the two disagree
    about what a query vector means.
    """
    assert _build_rag_constant("QUERY_INSTRUCTION") == settings.rag_query_instruction


def test_indexing_encodes_documents_without_an_instruction() -> None:
    """
    The document side must stay bare. If it ever gains a prompt, every stored
    vector changes meaning and the whole corpus needs reindexing.
    """
    embed_text = _build_rag_function("embed_text")
    prompt_defaults = [
        default
        for arg, default in zip(embed_text.args.kwonlyargs, embed_text.args.kw_defaults)
        if arg.arg == "prompt"
    ]
    assert prompt_defaults, "build_rag.embed_text lost its `prompt` keyword"
    assert ast.literal_eval(prompt_defaults[0]) is None, (
        "build_rag.embed_text now prefixes by default, which would send an "
        "instruction down the indexing path"
    )

    batched = _build_rag_function("embed_text_batched")
    passes_prompt = any(
        kw.arg == "prompt"
        for node in ast.walk(batched)
        if isinstance(node, ast.Call)
        for kw in node.keywords
    )
    assert not passes_prompt, (
        "build_rag.embed_text_batched encodes indexing chunks and must pass no prompt"
    )


@pytest.fixture(scope="module")
def offline_encoder():
    """
    Load the encoder the way a packaged install does: no credential, and the
    hub hard-blocked so a cache miss fails loudly instead of downloading.
    """
    from sentence_transformers import SentenceTransformer

    keys = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HUB_OFFLINE")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.pop("HF_TOKEN", None)
    os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        yield SentenceTransformer(
            settings.rag_model, revision=settings.rag_model_revision, device="cpu"
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.mark.live
def test_loads_with_no_credential_and_no_network(offline_encoder) -> None:
    """Task 3.2: `HF_TOKEN` unset and `HF_HUB_OFFLINE=1` against a primed cache."""
    assert offline_encoder.get_embedding_dimension() == EXPECTED_DIM


@pytest.mark.live
def test_vectors_are_the_expected_dimension(offline_encoder) -> None:
    """
    Task 3.3. A Chroma collection locks to the dimension of its first insert,
    so this figure is what makes a prebuilt store and a live query compatible.
    """
    vectors = offline_encoder.encode(["a molten salt", "an unrelated sentence"])
    assert vectors.shape == (2, EXPECTED_DIM)


@pytest.mark.live
def test_index_and_query_round_trip(offline_encoder, tmp_path) -> None:
    """
    Task 3.3, end to end: index two documents through Chroma exactly as
    `build_rag` does and assert a query retrieves the nearer one first.
    """
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    near = "The melting point of FLiBe is about 459 degrees Celsius."
    far = "Sourdough starter needs regular feeding to stay active."

    client = chromadb.PersistentClient(
        path=str(tmp_path / "rag_db"),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name="text_chunks", metadata={"hnsw:space": "cosine"}
    )
    # Explicit `embeddings=` -- never Chroma's default embedding function,
    # which would download an ONNX model from S3 and produce 384-dim vectors.
    collection.add(
        embeddings=[v.tolist() for v in offline_encoder.encode([near, far])],
        documents=[near, far],
        ids=["near", "far"],
    )

    # Documents above go in bare, the query gets the instruction -- the split
    # `rag_mcp._embed` and `build_rag.embed_text` implement.
    query_vector = offline_encoder.encode(
        ["What is FLiBe's melting point?"], prompt=rag_mcp.query_prompt()
    )[0]
    result = collection.query(query_embeddings=[query_vector.tolist()], n_results=2)
    assert result["ids"][0][0] == "near", result
    assert result["documents"][0][0] == near


@pytest.mark.live
def test_instruction_reaches_the_model(offline_encoder) -> None:
    """
    The prefix has to change the vector. A typo in the prompt plumbing that
    silently dropped it would leave every other test here passing.
    """
    query = "What is FLiBe's melting point?"
    bare = offline_encoder.encode([query])[0]
    instructed = offline_encoder.encode([query], prompt=rag_mcp.query_prompt())[0]
    assert bare.shape == instructed.shape == (EXPECTED_DIM,)
    assert not (bare == instructed).all(), (
        "the instruction prefix did not change the query vector, so "
        "sentence-transformers is not applying it"
    )
