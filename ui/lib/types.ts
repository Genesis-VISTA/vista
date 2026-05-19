export type UiPayload = { kind: "html"; html: string } | { kind: "none" };

export type Artifact = {
  type: string;
  name: string;
  url?: string;
};

export type ExecutionResult = {
  ok: boolean;
  stdout: string;
  stderr: string;
  data?: any;
  artifacts: Artifact[];
  meta: Record<string, any>;
  ui?: UiPayload;
};

export type SkillSummary = {
  slug: string;
  name: string;
  description: string;
  path: string;
  metadata?: Record<string, string | string[]>;
  tags?: string[];
  author?: string | null;
  repoUrl?: string | null;
  isPublic?: boolean;
};

export type SkillDetail = {
  slug: string;
  frontmatter: Record<string, unknown>;
  markdown: string;
  author?: string | null;
  repoUrl?: string | null;
  isPublic?: boolean;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  result?: ExecutionResult;
  /**
   * Streamed mid-campaign agent update (e.g. a per-trial Trial Report
   * during an alloy-design optimization). Rendered collapsed by default;
   * only the latest one in the conversation expands automatically.
   */
  intermediate?: boolean;
};

/* ---------------------------------------------------------------------- */
/*  Knowledge Base types                                                  */
/* ---------------------------------------------------------------------- */

/**
 * Citation metadata extracted from a single PDF at index time.
 * Mirrors the schema build_rag.py asks the LLM to fill in. All fields
 * except `filename` are optional — extraction can fail or be incomplete,
 * in which case the publication is still listed but with `null` fields.
 *
 * Wire shape uses snake_case to match the FastAPI backend (Publication
 * is serialized straight from the Pydantic model).
 */
export type PublicationIndexStatus =
  | "unindexed"
  | "queued"
  | "indexing"
  | "indexed"
  | "failed";

export type PublicationCitationStatus =
  | "pending"
  | "extracted"
  | "skipped"
  | "failed"
  | "disabled";

export type Publication = {
  filename: string;
  title?: string | null;
  authors?: string[] | null;
  abstract?: string | null;
  journal?: string | null;
  volume?: string | null;
  issue?: string | null;
  pages?: string | null;
  year?: string | null;
  doi?: string | null;
  keywords?: string[] | null;
  publisher?: string | null;
  /** Bytes on disk for the PDF; 0 when the PDF isn't on disk. */
  size: number;
  /** ISO timestamp when this publication was added to the KB. */
  added_at: string;
  /**
   * Whether the source PDF for this publication is currently on disk.
   * False for entries reconstructed from a built ChromaDB whose source
   * PDF is no longer in the KB's pdfs/ directory — those are still
   * searchable via rag_search but cannot be opened or re-extracted.
   */
  has_pdf: boolean;
  index_status: PublicationIndexStatus;
  index_error?: string | null;
  indexed_at?: string | null;
  /**
   * Outcome of the citation-metadata extraction step. Tracked
   * independently of `index_status`: a PDF can be fully indexed
   * (chunks searchable in chroma) while citation extraction was
   * disabled (no LLM credentials), failed (API error or unparseable
   * response), or skipped (idempotent reuse from a prior run).
   */
  citation_status?: PublicationCitationStatus | null;
  /** Error message from the citation LLM call when status='failed'. */
  citation_error?: string | null;
};

export type KnowledgeBaseBuildStatus = "pending" | "ready" | "stale" | "failed";

export type IndexProgress = {
  phase: "loading_model" | "indexing" | "done";
  /**
   * Finer-grained phase for the current paper. The set the indexer
   * emits today:
   *   - "starting"          — about to begin work on this paper
   *   - "citation"          — slow LLM call extracting title/authors/etc.
   *   - "extracting_text"   — PyMuPDF read + chunking (fast)
   *   - "embedding_chunks"  — sentence-transformers encode (slow on CPU;
   *                            chunk_processed/chunk_total set during this)
   *   - "upserting_chunks"  — chromadb write (fast)
   *   - "done"              — this paper finished
   * UI uses this to label the bar so users can tell whether an apparent
   * hang is just the model thinking.
   */
  sub_phase?:
    | "starting"
    | "citation"
    | "extracting_text"
    | "embedding_chunks"
    | "upserting_chunks"
    | "done"
    /** Legacy alias the older indexer emitted; treat same as embedding_chunks. */
    | "chunks"
    | null;
  processed: number;
  total: number;
  current?: string | null;
  /**
   * Chunk-level sub-progress within `current`. Set during the
   * "embedding_chunks" phase; absent otherwise.
   */
  chunk_processed?: number | null;
  chunk_total?: number | null;
  /** Unix epoch seconds. */
  started_at: number;
};

export type KnowledgeBase = {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  builtin: boolean;
  pdfs_dir: string;
  rag_db_path: string;
  shared_with_mcp: boolean;
  publications: Publication[];
  build_status: KnowledgeBaseBuildStatus;
  last_built_at?: string | null;
  created_at: string;
  updated_at: string;
  /** Live indexer progress, or null when no run is in flight. */
  index_progress?: IndexProgress | null;
};

/** Trimmed shape returned by `GET /api/knowledge-bases`. */
export type KnowledgeBaseSummary = KnowledgeBase;
