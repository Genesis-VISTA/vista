"""
Text RAG system with citation metadata extraction via Azure OpenAI.

Reads PDFs, extracts text chunks for semantic search, and also extracts
structured citation metadata (title, authors, DOI, etc.) from the first
few pages of each paper using Azure OpenAI.  Both the chunks and the
per-document citation records are stored in ChromaDB.

Environment variables (for Azure OpenAI citation extraction):
    AZURE_OPENAI_API_KEY        (required)
    ENDPOINT_URL                (default: https://aoai-eastus2-aaims.openai.azure.com/)
    DEPLOYMENT_NAME             (default: gpt-5.1-chat)
    AZURE_OPENAI_API_VERSION    (default: 2025-01-01-preview)
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import json
import logging
import os

import fitz  # PyMuPDF
import chromadb
from sentence_transformers import SentenceTransformer
from openai import AzureOpenAI, BadRequestError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Citation extraction prompt
# ---------------------------------------------------------------------------
CITATION_PROMPT = """\
You are a metadata extraction assistant for scientific publications.

Given the text extracted from a scientific paper, extract the following citation
metadata and return it as a single JSON object (no markdown fencing, no extra text):

{{
  "title": "...",
  "authors": ["First Last", ...],
  "abstract": "...",
  "journal": "...",
  "volume": "...",
  "issue": "...",
  "pages": "...",
  "year": "...",
  "doi": "...",
  "keywords": ["...", ...],
  "publisher": "..."
}}

Rules:
- Use null for any field you cannot determine.
- Authors should be a list of strings in "First Last" format.
- Keywords should be a list; if none found return an empty list.
- Return ONLY the JSON object, nothing else.

--- BEGIN EXTRACTED TEXT ---
{text}
--- END EXTRACTED TEXT ---
"""

# Fields we expect back from the LLM
CITATION_FIELDS = [
    "title", "authors", "abstract", "journal", "volume",
    "issue", "pages", "year", "doi", "keywords", "publisher",
]


# ---------------------------------------------------------------------------
# Azure OpenAI helper
# ---------------------------------------------------------------------------
def send_prompt_to_chatgpt(prompt: str, *, max_tokens: int = 1024) -> str:
    """Send a prompt to Azure OpenAI and return the response text."""
    endpoint = os.getenv(
        "ENDPOINT_URL", "https://aoai-eastus2-aaims.openai.azure.com/"
    )
    deployment = os.getenv("DEPLOYMENT_NAME", "gpt-5.1-chat")

    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not set in the environment.")

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
    )
    try:
        completion = client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
            max_completion_tokens=max_tokens,
            stop=None,
            stream=False,
        )
        return completion.choices[0].message.content

    except BadRequestError as e:
        msg = str(e)
        if "content_filter" in msg or "ResponsibleAIPolicyViolation" in msg:
            log.warning("Content filter triggered – returning empty string.")
            return ""
        else:
            log.error("BadRequestError: %s", msg)
            raise


def parse_citation_json(raw: str) -> Optional[dict]:
    """Try to parse the model response as JSON."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("Could not parse citation JSON:\n%s", raw[:300])
        return None


# ---------------------------------------------------------------------------
# TextRAG
# ---------------------------------------------------------------------------
class TextRAG:
    def __init__(
        self,
        pdf_folder: str,
        db_path: str = "./chroma_db",
        text_model: str = "google/embeddinggemma-300m",
        force_reindex: bool = False,
        extract_citations: bool = True,
        citation_max_pages: int = 5,
        citation_max_chars: int = 12_000,
    ):
        """
        Initialize text-only RAG system with optional citation extraction.

        Args:
            pdf_folder:         Path to folder containing PDFs.
            db_path:            Path to store ChromaDB database.
            text_model:         SentenceTransformers model for text embeddings.
            force_reindex:      If True, rebuild database even if it exists.
            extract_citations:  If True, call Azure OpenAI to extract citation
                                metadata for each PDF and store it in a
                                separate ChromaDB collection.
            citation_max_pages: Max front-matter pages to read per PDF for
                                citation extraction.
            citation_max_chars: Max characters of front-matter text to send
                                to the LLM.
        """
        self.pdf_folder = Path(pdf_folder)
        self.db_path = db_path
        self.force_reindex = force_reindex
        self.extract_citations = extract_citations
        self.citation_max_pages = citation_max_pages
        self.citation_max_chars = citation_max_chars

        # Text embedding model
        log.info("Loading text model: %s", text_model)
        self.text_encoder = SentenceTransformer(text_model, device="cpu")

        # ChromaDB client
        self.client = chromadb.PersistentClient(path=db_path)
        self.db_exists = self._check_database_exists()

        # Collections
        self.text_collection = self.client.get_or_create_collection(
            name="text_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        self.citation_collection = self.client.get_or_create_collection(
            name="citations",
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------
    def _check_database_exists(self) -> bool:
        db_path = Path(self.db_path)
        if not db_path.exists():
            return False
        try:
            for collection in self.client.list_collections():
                if collection.count() > 0:
                    log.info(
                        "Existing DB at %s – collection '%s' has %d items",
                        self.db_path, collection.name, collection.count(),
                    )
                    return True
        except Exception as e:
            log.error("Error checking database: %s", e)
        return False

    # ------------------------------------------------------------------
    # PDF text extraction
    # ------------------------------------------------------------------
    def extract_text_from_pdf(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """Extract text chunks from entire PDF with metadata."""
        doc = fitz.open(pdf_path)
        text_chunks = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            chunks = self._chunk_text(text, chunk_size=512, overlap=50)
            for idx, chunk in enumerate(chunks):
                text_chunks.append({
                    "text": chunk,
                    "metadata": {
                        "source": pdf_path.name,
                        "page": page_num,
                        "chunk_id": idx,
                        "type": "text",
                    },
                })
        doc.close()
        return text_chunks

    def _extract_front_matter_text(self, pdf_path: Path) -> str:
        """Extract text from the first few pages for citation extraction."""
        doc = fitz.open(pdf_path)
        parts: list[str] = []
        for page in doc[: self.citation_max_pages]:
            text = page.get_text()
            if text:
                parts.append(text)
        doc.close()
        return "\n\n".join(parts)[: self.citation_max_chars]

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
        words = text.split()
        chunks = []
        for i in range(0, len(words), chunk_size - overlap):
            chunk = " ".join(words[i : i + chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    # ------------------------------------------------------------------
    # Citation extraction via Azure OpenAI
    # ------------------------------------------------------------------
    def _extract_citation(self, pdf_path: Path) -> Optional[dict]:
        """Call Azure OpenAI to pull structured citation metadata."""
        front_text = self._extract_front_matter_text(pdf_path)
        if not front_text.strip():
            log.warning("  No front-matter text for citation extraction.")
            return None

        prompt = CITATION_PROMPT.format(text=front_text)
        try:
            raw = send_prompt_to_chatgpt(prompt, max_tokens=1024)
        except Exception as exc:
            log.error("  Citation API call failed: %s", exc)
            return None

        return parse_citation_json(raw)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def embed_text(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.text_encoder.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()

    # ------------------------------------------------------------------
    # Citation metadata → flat string dict for ChromaDB
    # ------------------------------------------------------------------
    @staticmethod
    def _citation_to_metadata(citation: dict, filename: str) -> dict:
        """
        Convert a citation dict into a flat metadata dict that ChromaDB
        can store (string / int / float values only).
        """
        meta: dict[str, Any] = {"source": filename, "type": "citation"}
        for field in CITATION_FIELDS:
            value = citation.get(field)
            if value is None:
                meta[field] = ""
            elif isinstance(value, list):
                # Store lists as JSON strings
                meta[field] = json.dumps(value, ensure_ascii=False)
            else:
                meta[field] = str(value)
        return meta

    @staticmethod
    def _citation_to_document(citation: dict) -> str:
        """
        Build a searchable text representation of the citation so that
        the citation collection is queryable via semantic search.
        """
        parts: list[str] = []
        if citation.get("title"):
            parts.append(citation["title"])
        if citation.get("authors"):
            parts.append("Authors: " + ", ".join(citation["authors"]))
        if citation.get("abstract"):
            parts.append(citation["abstract"])
        if citation.get("journal"):
            parts.append(f"Journal: {citation['journal']}")
        if citation.get("year"):
            parts.append(f"Year: {citation['year']}")
        if citation.get("doi"):
            parts.append(f"DOI: {citation['doi']}")
        if citation.get("keywords"):
            parts.append("Keywords: " + ", ".join(citation["keywords"]))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def index_single_pdf(self, pdf_path: Path) -> Dict[str, Any]:
        """
        Index a single PDF: chunk its text into the text_chunks collection
        and (if extract_citations is True) extract citation metadata into
        the citations collection.

        Returns a status dict with the citation fields if extraction
        succeeded — useful for callers that want to surface the metadata
        immediately rather than having to query the DB back. The dict has
        the shape:

            {
                "filename": "<basename>",
                "status": "indexed" | "skipped" | "failed",
                "error": "..." | None,
                "chunk_count": <int>,
                "citation": { ...citation fields or None... },
            }

        `skipped` means the PDF was already in the text_chunks collection
        and `force_reindex` was False. The caller can treat skipped as a
        success — the data is there, we just didn't redo the work.

        This method is independent of `index_pdfs()` so the same per-PDF
        logic can be invoked from a server-side incremental indexer
        (e.g. when a user uploads a single paper through the UI).
        """
        result: Dict[str, Any] = {
            "filename": pdf_path.name,
            "status": "failed",
            "error": None,
            "chunk_count": 0,
            "citation": None,
        }

        # Idempotency: if any text chunks for this source already exist
        # in the DB, skip unless we're force-reindexing. We use the
        # citation collection's deterministic id (`<stem>_citation`) as
        # the cheap check. The text collection is harder to probe by
        # source without a query, but if the citation row exists then
        # the upstream indexing run for this paper completed.
        cite_id = f"{pdf_path.stem}_citation"
        if not self.force_reindex:
            try:
                existing = self.citation_collection.get(ids=[cite_id])
                if existing and existing.get("ids"):
                    result["status"] = "skipped"
                    metas = existing.get("metadatas") or []
                    if metas:
                        result["citation"] = metas[0]
                    return result
            except Exception:
                # Any error here means "not present"; fall through to
                # actual indexing.
                pass

        try:
            # --- Text chunks ---
            text_chunks = self.extract_text_from_pdf(pdf_path)
            if text_chunks:
                texts = [c["text"] for c in text_chunks]
                embeddings = self.embed_text(texts)
                ids = [f"{pdf_path.stem}_text_{j}" for j in range(len(text_chunks))]
                metadatas = [c["metadata"] for c in text_chunks]
                # `upsert` so re-indexing the same file replaces rather
                # than errors. Force-reindex callers will hit this path
                # too.
                self.text_collection.upsert(
                    embeddings=embeddings,
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids,
                )
                result["chunk_count"] = len(text_chunks)
                log.info("  ✓ Indexed %d text chunks for %s", len(text_chunks), pdf_path.name)

            # --- Citation metadata ---
            if self.extract_citations:
                citation = self._extract_citation(pdf_path)
                if citation:
                    doc_text = self._citation_to_document(citation)
                    doc_embedding = self.embed_text([doc_text])[0]
                    meta = self._citation_to_metadata(citation, pdf_path.name)

                    self.citation_collection.upsert(
                        embeddings=[doc_embedding],
                        documents=[doc_text],
                        metadatas=[meta],
                        ids=[cite_id],
                    )
                    result["citation"] = meta
                    log.info(
                        "  ✓ Stored citation for %s: %s",
                        pdf_path.name,
                        citation.get("title", "(no title)"),
                    )
                else:
                    log.warning("  ✗ Citation extraction failed for %s", pdf_path.name)

            result["status"] = "indexed"
        except Exception as e:
            log.error("  ✗ Error processing %s: %s", pdf_path.name, e)
            result["error"] = str(e)
            result["status"] = "failed"

        return result

    def index_pdfs(self):
        """Process all PDFs: index text chunks + extract citation metadata."""
        if self.db_exists and not self.force_reindex:
            log.info("Database already exists at %s – skipping indexing.", self.db_path)
            log.info("  Text chunks : %d", self.text_collection.count())
            log.info("  Citations   : %d", self.citation_collection.count())
            log.info("Set force_reindex=True to rebuild.")
            return

        if self.force_reindex and self.db_exists:
            log.info("Force reindex – clearing existing collections...")
            for name in ("text_chunks", "citations"):
                try:
                    self.client.delete_collection(name)
                except Exception:
                    pass
            self.text_collection = self.client.create_collection(
                name="text_chunks", metadata={"hnsw:space": "cosine"}
            )
            self.citation_collection = self.client.create_collection(
                name="citations", metadata={"hnsw:space": "cosine"}
            )

        pdf_files = sorted(self.pdf_folder.glob("**/*.pdf"))
        log.info("Found %d PDF(s) to process", len(pdf_files))

        for i, pdf_path in enumerate(pdf_files, 1):
            log.info("[%d/%d] %s", i, len(pdf_files), pdf_path.name)
            self.index_single_pdf(pdf_path)

        log.info("=" * 50)
        log.info("Indexing complete!")
        log.info("  Text chunks : %d", self.text_collection.count())
        log.info("  Citations   : %d", self.citation_collection.count())

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    def query(self, query: str, n_results: int = 5) -> Dict[str, Any]:
        """Search text chunks collection."""
        query_embedding = self.embed_text([query])[0]
        return self.text_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
        )

    def query_citations(self, query: str, n_results: int = 5) -> Dict[str, Any]:
        """Search the citation metadata collection."""
        query_embedding = self.embed_text([query])[0]
        return self.citation_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
        )

    def get_citation_for_source(self, filename: str) -> Optional[dict]:
        """
        Look up the citation metadata for a specific PDF by filename.

        Returns the metadata dict (with all citation fields) or None.
        """
        results = self.citation_collection.get(
            where={"source": filename},
            include=["metadatas"],
        )
        if results["metadatas"]:
            meta = results["metadatas"][0]
            # Deserialise JSON-encoded list fields
            for field in ("authors", "keywords"):
                if meta.get(field):
                    try:
                        meta[field] = json.loads(meta[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return meta
        return None

    def query_with_citations(
        self, query: str, n_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over text chunks, then attach the citation metadata
        for each source document to the results.
        """
        raw = self.query(query, n_results=n_results)
        enriched: list[dict] = []
        seen_sources: dict[str, Optional[dict]] = {}

        for doc, meta in zip(raw["documents"][0], raw["metadatas"][0]):
            source = meta["source"]
            if source not in seen_sources:
                seen_sources[source] = self.get_citation_for_source(source)
            enriched.append({
                "text": doc,
                "chunk_metadata": meta,
                "citation": seen_sources[source],
            })
        return enriched


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    rag = TextRAG(
        pdf_folder="./pdfs",
        db_path="./knowledge_bases/molten_salts_db",
        extract_citations=True,   # flip to False to skip LLM calls
        force_reindex=False,      # flip to True to rebuild
    )

    rag.index_pdfs()

    # --- Semantic search with citation metadata attached ---
    print("\n" + "=" * 50)
    print("Searching text chunks (with citations)...\n")
    results = rag.query_with_citations(
        "molten salt thermophysical properties", n_results=3
    )
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['text'][:200]}...")
        print(f"   Source : {r['chunk_metadata']['source']}, "
              f"Page {r['chunk_metadata']['page']}")
        if r["citation"]:
            print(f"   Title  : {r['citation'].get('title', 'N/A')}")
            print(f"   Authors: {r['citation'].get('authors', 'N/A')}")
            print(f"   DOI    : {r['citation'].get('doi', 'N/A')}")
        print()

    # --- Search citations directly ---
    print("=" * 50)
    print("Searching citation collection...\n")
    cites = rag.query_citations("thermal conductivity fluoride salts", n_results=3)
    for i, (doc, meta) in enumerate(
        zip(cites["documents"][0], cites["metadatas"][0]), 1
    ):
        print(f"{i}. {meta.get('title', 'N/A')}")
        print(f"   Year: {meta.get('year', 'N/A')}  DOI: {meta.get('doi', 'N/A')}")
        print()
