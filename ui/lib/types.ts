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
  /** Bytes on disk for the PDF. */
  size: number;
  /** When the PDF was added to this KB (ISO timestamp). */
  addedAt: string;
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
  createdAt: string;
  updatedAt: string;
};

/** Trimmed shape returned by GET /api/knowledge-bases (list view). */
export type KnowledgeBaseSummary = Omit<KnowledgeBase, "publications"> & {
  publicationCount: number;
};
