"""
The retrieval encoder must need no account and no network.

`google/embeddinggemma-300m` is gated: without an accepted licence and an
`HF_TOKEN` the RAG server raises `GatedRepoError` in its lifespan and the whole
MCP process fails to boot. `microsoft/harrier-oss-v1-270m` (MIT, ungated,
640-dimension) replaces it so a researcher's first run needs no HuggingFace
account at all.

The two model-name tests are hermetic. The rest are marked `live` because they
load the real 270M-parameter model, which `ci-local.sh`'s hermetic filter
excludes from PR CI. Prime the cache first with:

    HF_HOME=data/huggingface uv run python -c \
      "from sentence_transformers import SentenceTransformer as S; \
       S('microsoft/harrier-oss-v1-270m')"
"""

import os
from pathlib import Path

import pytest

from vista_mcp_server.config import settings

# Both encoders must name the same model or a Chroma collection built by one is
# unqueryable by the other.
EXPECTED_MODEL = "microsoft/harrier-oss-v1-270m"
EXPECTED_DIM = 640


def test_query_encoder_names_the_ungated_model() -> None:
    assert settings.rag_model == EXPECTED_MODEL


def test_indexing_encoder_names_the_same_model() -> None:
    """
    Guards the one drift this design accepts: two hardcoded copies of the model
    name, in two services, that no import connects. `build_rag.py` lives at the
    repo root and is not importable from here, so it is asserted by reading it.
    """
    build_rag = Path(__file__).resolve().parents[3] / "build_rag.py"
    assert build_rag.is_file(), f"expected build_rag.py at {build_rag}"
    assert f'text_model: str = "{EXPECTED_MODEL}"' in build_rag.read_text(), (
        f"build_rag.py's text_model default has drifted from "
        f"settings.rag_model ({settings.rag_model!r})"
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
        yield SentenceTransformer(settings.rag_model, device="cpu")
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

    query_vector = offline_encoder.encode(["What is FLiBe's melting point?"])[0]
    result = collection.query(
        query_embeddings=[query_vector.tolist()], n_results=2
    )
    assert result["ids"][0][0] == "near", result
    assert result["documents"][0][0] == near
