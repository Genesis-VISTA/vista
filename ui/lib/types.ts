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
};

export type SkillDetail = {
  slug: string;
  frontmatter: Record<string, unknown>;
  markdown: string;
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
 * Mirrors the schema build_rag.py asks Azure OpenAI to fill in.
 * All fields except `filename` are optional — extraction can fail or be
 * incomplete, in which case the publication is still listed but with
 * `null` fields.
 */
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
  /** Bytes on disk for the PDF; 0 when the PDF isn't on disk (indexed-only). */
  size: number;
  /** When the publication was added to this KB (ISO timestamp). */
  addedAt: string;
  /**
   * Whether the source PDF for this publication is currently on disk.
   * False for entries reconstructed from a built ChromaDB whose source
   * PDF is no longer in the KB's pdfs/ directory — those are still
   * searchable via rag_search but cannot be opened or re-extracted.
   * Defaults to true for any record without the field set, since older
   * records were always disk-backed.
   */
  hasPdf?: boolean;
  /**
   * Status of this publication in the underlying ChromaDB.
   *
   *   unindexed - On disk but not yet chunked/embedded into chroma.
   *   queued    - Indexing job has been registered but the indexer
   *               subprocess hasn't started touching this row yet.
   *   indexing  - Indexer subprocess is actively working on this row
   *               (or queued behind another row in the same job).
   *   indexed   - Text chunks (and possibly citation row) are in chroma.
   *   failed    - Indexing was attempted and raised; see indexError.
   *
   * The field is optional for backwards compatibility; readers should
   * treat its absence as "indexed" for entries on shared-path KBs
   * (since those PDFs were almost certainly already in the chroma DB
   * the molten-salt corpus was built from) and "unindexed" otherwise.
   */
  indexStatus?:
    | "unindexed"
    | "queued"
    | "indexing"
    | "indexed"
    | "failed";
  /** Last error message from a failed indexing attempt. */
  indexError?: string | null;
  /** ISO timestamp of the last successful indexing. */
  indexedAt?: string | null;
};

/**
 * Build status for the underlying ChromaDB index. The metadata + PDF list
 * is always available; the vector store may be `pending` (PDFs uploaded
 * but not yet embedded), `ready` (rag_search will work), or `stale`
 * (PDFs added/removed since last build).
 */
export type KnowledgeBaseBuildStatus = "pending" | "ready" | "stale" | "building" | "failed";

export type KnowledgeBase = {
  slug: string;
  name: string;
  description: string;
  /** True for the seeded molten-salt KB; user cannot delete the slug. */
  builtin?: boolean;
  publications: Publication[];
  buildStatus: KnowledgeBaseBuildStatus;
  /** ISO timestamp of last successful build, or null. */
  lastBuiltAt?: string | null;
  /**
   * ISO timestamp of the chroma.sqlite3 file at the time we last
   * imported citation metadata into the publications array. Used to
   * decide whether to re-run the enrichment helper. Null means
   * citations have never been imported (or the DB has been rebuilt
   * since).
   */
  lastCitationSyncAt?: string | null;
  createdAt: string;
  updatedAt: string;
  /**
   * Filesystem locations this KB is backed by. Surfaced so the UI can
   * show "this KB shares a corpus with the MCP server's rag_search tool"
   * for built-in KBs that point at a non-default path.
   */
  linkedPaths?: {
    pdfs: string;
    ragDb: string;
  };
  /**
   * True when the KB's source PDF folder and ChromaDB are the shared
   * locations consumed by the Python MCP server's rag_search tool.
   * Implies that uploads here will appear in tool calls after the next
   * `build_rag.py` rebuild.
   */
  sharedWithMcp?: boolean;
};

/** Trimmed shape returned by GET /api/knowledge-bases (list view). */
export type KnowledgeBaseSummary = Omit<KnowledgeBase, "publications"> & {
  publicationCount: number;
};
