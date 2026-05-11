/**
 * Server-side filesystem store for Knowledge Bases.
 *
 * Layout for user-created KBs:
 *   <knowledgeBasesDir>/
 *     <slug>/
 *       kb.json          # KnowledgeBase metadata + publications array
 *       pdfs/<file>.pdf  # source PDFs
 *       rag_db/          # ChromaDB built by build_rag.py
 *
 * Built-in seeds may *override* the pdfs/ and chroma paths. The
 * `molten-salt-papers` seed pins both of those to the canonical shared
 * locations consumed by the Python MCP server's rag_search tool —
 * `<repo>/pdfs` for source PDFs and
 * `<repo>/knowledge_bases/molten_salts_db` for the ChromaDB by default
 * — so adding a PDF in the UI lands in the same corpus the MCP server
 * queries (after the next `build_rag.py` rebuild).
 *
 * The KB metadata file (`kb.json`) for a seed with shared paths still
 * lives under `<knowledgeBasesDir>/<slug>/` — only the corpus is shared.
 *
 * For shared-path seeds, the on-disk PDF directory is treated as the
 * source of truth: `readKbJson` walks it on every read, adding stub
 * Publication entries for any PDFs not already recorded in `kb.json` and
 * dropping entries whose backing file has disappeared. This means a PDF
 * dropped into `<repo>/pdfs/` outside the UI (e.g. by `git pull` or
 * `scp`) will show up in the explorer too.
 *
 * This module is intentionally Node-only (uses `node:fs/promises` and
 * `path`); it must never be imported from a "use client" file.
 */

import { mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { config } from "@/app/config";
import type {
  IndexProgress,
  KnowledgeBase,
  KnowledgeBaseSummary,
  Publication,
} from "@/lib/types";

const KB_FILE = "kb.json";
const PDF_SUBDIR = "pdfs";
const RAG_DB_SUBDIR = "rag_db";

/** Files larger than this are rejected at upload time. */
export const MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024;

/* ---------------------------------------------------------------------- */
/*  Built-in seeds                                                        */
/* ---------------------------------------------------------------------- */

/**
 * Seed shape: an entry in BUILTIN_SEEDS describes a KB that should exist
 * on first run. If `pdfsPath` / `ragDbPath` are present, the KB's corpus
 * lives at those absolute paths instead of under <knowledgeBasesDir>/<slug>/.
 *
 * `sharedWithMcp: true` is a UI hint signaling that the configured paths
 * are shared with the Python MCP server's rag_search tool.
 */
type SeedConfig = {
  slug: string;
  name: string;
  description: string;
  pdfsPath?: string;
  ragDbPath?: string;
  sharedWithMcp?: boolean;
};

/**
 * KBs that should exist on first run.
 *
 * Molten Salt Papers is pinned to the same source-PDF folder and
 * ChromaDB the MCP server's rag_search tool reads from, so the UI and
 * the tool see the same corpus. Both paths come from `config.ts` and
 * respect the same env vars the Python side honors:
 * `VISTA_MCP_MOLTEN_SALTS_DB_PATH` (with `VISTA_MCP_RAG_DB_PATH` as a
 * legacy alias) for the ChromaDB, and `VISTA_MCP_RAG_PDFS_PATH` for the
 * source PDFs.
 */
function builtinSeeds(): ReadonlyArray<SeedConfig> {
  return [
    {
      slug: "molten-salt-papers",
      name: "Molten Salt Papers",
      description:
        "Peer-reviewed publications on molten salt thermophysical properties, "
        + "phase behavior, and tritium breeding. Shares its PDF folder and "
        + "ChromaDB index with the MCP server's rag_search tool.",
      pdfsPath: config.ragPdfsPath,
      ragDbPath: config.ragDbPath,
      sharedWithMcp: true,
    },
  ];
}

const SEED_BY_SLUG = new Map<string, SeedConfig>(
  builtinSeeds().map((s) => [s.slug, s])
);

/* ---------------------------------------------------------------------- */
/*  Slug + filename hygiene                                               */
/* ---------------------------------------------------------------------- */

export function slugifyKbName(name: string): string {
  const base = name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || `kb-${Date.now()}`;
}

/** Strict slug validator: lowercase alphanumerics + dashes, 1–80 chars. */
export function isValidSlug(slug: string): boolean {
  return /^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$/.test(slug);
}

export function sanitizePdfFilename(name: string): string {
  const base = path.basename(name).replace(/[^a-zA-Z0-9._-]/g, "_");
  // Force a .pdf extension if missing — uploads are gated to PDF only.
  if (!/\.pdf$/i.test(base)) return `${base || "upload"}.pdf`;
  return base || "upload.pdf";
}

export function ensureUniqueFilename(existing: Set<string>, name: string): string {
  if (!existing.has(name)) return name;
  const ext = path.extname(name);
  const stem = path.basename(name, ext);
  let i = 1;
  while (existing.has(`${stem}-${i}${ext}`)) i += 1;
  return `${stem}-${i}${ext}`;
}

/* ---------------------------------------------------------------------- */
/*  Path helpers                                                          */
/* ---------------------------------------------------------------------- */

/** Directory holding kb.json (always under knowledgeBasesDir, even for seeds). */
function kbDir(slug: string): string {
  if (!isValidSlug(slug)) {
    throw new Error(`Invalid KB slug: ${slug}`);
  }
  return path.join(config.knowledgeBasesDir, slug);
}

function kbJsonPath(slug: string): string {
  return path.join(kbDir(slug), KB_FILE);
}

/**
 * Resolve the directory containing source PDFs for a KB.
 * For seeds with a `pdfsPath` override (e.g. molten-salt-papers), this
 * returns the shared location; otherwise it returns the per-KB subdir.
 */
export function kbPdfDir(slug: string): string {
  const seed = SEED_BY_SLUG.get(slug);
  if (seed?.pdfsPath) return seed.pdfsPath;
  return path.join(kbDir(slug), PDF_SUBDIR);
}

/**
 * Resolve the directory containing the built ChromaDB for a KB.
 * For seeds with a `ragDbPath` override, this returns the shared location.
 */
export function kbRagDbDir(slug: string): string {
  const seed = SEED_BY_SLUG.get(slug);
  if (seed?.ragDbPath) return seed.ragDbPath;
  return path.join(kbDir(slug), RAG_DB_SUBDIR);
}

export function kbPdfPath(slug: string, filename: string): string {
  // Prevent path traversal: filename must not contain separators.
  const safe = sanitizePdfFilename(filename);
  return path.join(kbPdfDir(slug), safe);
}

/* ---------------------------------------------------------------------- */
/*  Build status detection                                                */
/* ---------------------------------------------------------------------- */

/**
 * A persisted ChromaDB shows up as a directory containing a
 * `chroma.sqlite3` file. We use that as a cheap "is the index built?"
 * probe — good enough to flip the UI from "Not yet built" to "Ready"
 * without spawning a Python process.
 */
async function detectBuildArtifacts(
  ragDbDir: string
): Promise<{ exists: boolean; lastBuiltAt: string | null }> {
  try {
    const sqlite = path.join(ragDbDir, "chroma.sqlite3");
    const s = await stat(sqlite);
    if (!s.isFile()) return { exists: false, lastBuiltAt: null };
    return { exists: true, lastBuiltAt: s.mtime.toISOString() };
  } catch {
    return { exists: false, lastBuiltAt: null };
  }
}

/**
 * Read the indexer's progress file if one exists. The Python indexer
 * writes `<ragDbDir>/.indexing.progress.json` after every phase
 * boundary (loading model → per paper → done) and clears it on exit.
 *
 * Returns null when no file is present, when it's malformed, or when
 * its `started_at` looks suspiciously stale (older than 2 hours — the
 * lock would normally clear, but a SIGKILL or kernel panic could
 * leave a phantom file). The shape this returns matches the
 * IndexProgress type the UI expects.
 */
const STALE_PROGRESS_SECONDS = 2 * 60 * 60;

async function readIndexProgress(
  ragDbDir: string
): Promise<IndexProgress | null> {
  try {
    const raw = await readFile(
      path.join(ragDbDir, ".indexing.progress.json"),
      "utf-8"
    );
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const phase = parsed.phase;
    const subPhaseRaw = parsed.sub_phase ?? parsed.subPhase;
    const processed = parsed.processed;
    const total = parsed.total;
    const current = parsed.current;
    const startedAt = parsed.started_at ?? parsed.startedAt;
    if (
      (phase !== "loading_model" && phase !== "indexing" && phase !== "done")
      || typeof processed !== "number"
      || typeof total !== "number"
      || (current !== null && typeof current !== "string")
      || typeof startedAt !== "number"
    ) {
      return null;
    }
    if (Date.now() / 1000 - startedAt > STALE_PROGRESS_SECONDS) {
      return null;
    }
    // Validate sub_phase loosely — if the script emits a value we
    // don't recognize, fall through to undefined rather than failing.
    let subPhase: IndexProgress["subPhase"];
    if (
      subPhaseRaw === "starting"
      || subPhaseRaw === "citation"
      || subPhaseRaw === "chunks"
      || subPhaseRaw === "done"
    ) {
      subPhase = subPhaseRaw;
    }
    return {
      phase,
      subPhase,
      processed,
      total,
      current,
      startedAt,
    };
  } catch {
    return null;
  }
}

/* ---------------------------------------------------------------------- */
/*  Citation enrichment from ChromaDB                                     */
/* ---------------------------------------------------------------------- */

/**
 * Citation fields emitted by `dump_citations.py`. Mirrors
 * `build_rag.py:CITATION_FIELDS`.
 */
type DumpedCitation = {
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
};

type CitationDump = Record<string, DumpedCitation>;

/**
 * Path to the Python helper that dumps citations from a ChromaDB.
 * Resolved relative to `process.cwd()` (the `ui/` directory at runtime).
 */
const DUMP_CITATIONS_SCRIPT = path.resolve(
  path.join(process.cwd(), "../mcp-server/scripts/dump_citations.py")
);
const DUMP_CITATIONS_CWD = path.resolve(
  path.join(process.cwd(), "../mcp-server")
);

/**
 * Cap on how long we'll wait for the helper. The first invocation can
 * be slow (chromadb cold import + DB open); subsequent ones are fast.
 */
const DUMP_CITATIONS_TIMEOUT_MS = 30_000;

/**
 * In-flight enrichment promises keyed by ragDbDir. Prevents two
 * concurrent /api/knowledge-bases/<slug> reads from both spawning the
 * Python helper for the same DB.
 */
const inFlightDumps = new Map<string, Promise<CitationDump | null>>();

/**
 * Spawn the Python helper to read citation metadata out of a built
 * ChromaDB. Returns `null` on any failure (helper missing, chromadb
 * not installed, timeout, malformed JSON) — callers should treat null
 * as "no enrichment available right now" and proceed with the
 * unenriched publications.
 *
 * Uses `uv run --directory <mcp-server>` so the script picks up the
 * same Python environment the MCP server runs in (and therefore the
 * same chromadb version that built the DB). Falls back to plain
 * `python3` if `uv` is not on PATH.
 */
async function spawnCitationDump(ragDbDir: string): Promise<CitationDump | null> {
  return new Promise<CitationDump | null>((resolve) => {
    // Use `uv run` to pick up the same Python environment the MCP
    // server runs in (and therefore the same chromadb version that
    // built the DB). Project convention: the rest of launch.sh assumes
    // uv is on PATH.
    const cmd = "uv";
    const args = [
      "run",
      "--directory",
      DUMP_CITATIONS_CWD,
      "python",
      DUMP_CITATIONS_SCRIPT,
      "--db-path",
      ragDbDir,
    ];

    let stdout = "";
    let stderr = "";
    let settled = false;

    let child;
    try {
      child = spawn(cmd, args, { cwd: DUMP_CITATIONS_CWD });
    } catch (err) {
      console.warn(
        `[knowledge-bases] failed to spawn citation dump (${cmd}):`,
        err
      );
      resolve(null);
      return;
    }

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        child.kill("SIGKILL");
      } catch {
        /* ignore */
      }
      console.warn(
        `[knowledge-bases] citation dump timed out after ${DUMP_CITATIONS_TIMEOUT_MS}ms (${ragDbDir})`
      );
      resolve(null);
    }, DUMP_CITATIONS_TIMEOUT_MS);

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      console.warn(`[knowledge-bases] citation dump error:`, err);
      resolve(null);
    });
    child.on("close", (code: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      // Exit code 2 = no citations collection; that's a clean empty
      // result, not an error.
      if (code !== 0 && code !== 2) {
        console.warn(
          `[knowledge-bases] citation dump exited with code ${code}: ${stderr.trim()}`
        );
        resolve(null);
        return;
      }
      const trimmed = stdout.trim();
      if (!trimmed) {
        resolve({});
        return;
      }
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          resolve(parsed as CitationDump);
        } else {
          console.warn(
            `[knowledge-bases] citation dump returned non-object JSON`
          );
          resolve(null);
        }
      } catch (err) {
        console.warn(`[knowledge-bases] failed to parse citation dump JSON:`, err);
        resolve(null);
      }
    });
  });
}

async function getCitationDump(ragDbDir: string): Promise<CitationDump | null> {
  const inflight = inFlightDumps.get(ragDbDir);
  if (inflight) return inflight;
  const promise = spawnCitationDump(ragDbDir).finally(() => {
    inFlightDumps.delete(ragDbDir);
  });
  inFlightDumps.set(ragDbDir, promise);
  return promise;
}

/**
 * Merge citation metadata into the publications list.
 *
 * Two-way reconciliation:
 *  1. Existing publications get their null/empty fields filled in from
 *     the dump. Already-set values are preserved (so a manual edit
 *     wouldn't be clobbered by a re-extraction that came back empty).
 *  2. Citation entries whose filename is NOT in the publications list
 *     get synthesized as new Publication records with `hasPdf: false`.
 *     This is the case when the ChromaDB was built against a PDF
 *     corpus that isn't (or is no longer) in the KB's pdfs/ directory
 *     — common when the user has the rag_db checked into git LFS but
 *     never had the source PDFs locally. These rows are still useful
 *     because the MCP server's rag_search tool can return them; they
 *     just can't be opened from the UI.
 */
function mergeCitationsIntoPublications(
  publications: Publication[],
  dump: CitationDump
): { publications: Publication[]; changed: boolean } {
  let changed = false;
  const byFilename = new Map(publications.map((p) => [p.filename, p]));

  const fields: (keyof DumpedCitation)[] = [
    "title", "authors", "abstract", "journal", "volume",
    "issue", "pages", "year", "doi", "keywords", "publisher",
  ];

  // Pass 1: fill in existing publications.
  const next: Publication[] = publications.map((pub) => {
    const cite = dump[pub.filename];
    if (!cite) return pub;
    let pubChanged = false;
    const merged: Publication = { ...pub };
    for (const field of fields) {
      const incoming = cite[field];
      if (incoming === undefined || incoming === null) continue;
      if (Array.isArray(incoming) && incoming.length === 0) continue;
      if (typeof incoming === "string" && incoming.trim() === "") continue;
      const current = merged[field];
      const isCurrentEmpty =
        current === null
        || current === undefined
        || (Array.isArray(current) && current.length === 0)
        || (typeof current === "string" && current.trim() === "");
      if (isCurrentEmpty) {
        (merged as Record<string, unknown>)[field] = incoming;
        pubChanged = true;
      }
    }
    if (pubChanged) changed = true;
    return merged;
  });

  // Pass 2: synthesize publications for citation entries we haven't
  // seen yet (indexed-only — source PDF not on disk).
  for (const [filename, cite] of Object.entries(dump)) {
    if (byFilename.has(filename)) continue;
    const synthesized: Publication = {
      filename,
      title: cite.title ?? null,
      authors: cite.authors ?? null,
      abstract: cite.abstract ?? null,
      journal: cite.journal ?? null,
      volume: cite.volume ?? null,
      issue: cite.issue ?? null,
      pages: cite.pages ?? null,
      year: cite.year ?? null,
      doi: cite.doi ?? null,
      keywords: cite.keywords ?? null,
      publisher: cite.publisher ?? null,
      size: 0,
      // We don't know when this PDF was originally added; use the
      // current time as a stable-ish placeholder. The user can sort by
      // year or title to surface meaningful order.
      addedAt: new Date().toISOString(),
      hasPdf: false,
    };
    next.push(synthesized);
    changed = true;
  }

  return { publications: next, changed };
}

/* ---------------------------------------------------------------------- */
/*  Incremental indexer (subprocess)                                      */
/* ---------------------------------------------------------------------- */

/**
 * Per-publication result emitted by `index_publications.py`.
 * Mirrors the shape produced by `TextRAG.index_single_pdf`.
 */
type IndexResult = {
  filename: string;
  status: "indexed" | "skipped" | "failed";
  error: string | null;
  chunk_count: number;
  citation: Record<string, unknown> | null;
};

const INDEX_PUBLICATIONS_SCRIPT = path.resolve(
  path.join(process.cwd(), "../mcp-server/scripts/index_publications.py")
);

/**
 * Cap on how long we'll wait for an indexer subprocess. Indexing N
 * papers is roughly O(N seconds) for text chunks plus an Azure OpenAI
 * round-trip per paper for citations (~5-30s each). We give it a
 * generous ceiling so a 50-paper drop-in import doesn't get killed,
 * but still bound the wait so a hung process doesn't leak.
 */
const INDEX_TIMEOUT_MS = 30 * 60 * 1000;  // 30 minutes

/**
 * Track in-flight indexing jobs per (slug). Used so a second upload
 * arriving while an earlier one is still indexing doesn't lose the
 * status update — both finish their work and the second batch's
 * results get persisted alongside the first.
 *
 * The Python script's own fcntl lock is a lower-level safety net: it
 * prevents two indexer processes from racing on a single Chroma DB
 * even if the Node side spawned them concurrently. The Map below is
 * an upper-level convenience to detect ongoing work in API routes.
 */
const activeIndexJobs = new Map<string, Promise<IndexResult[] | null>>();

/**
 * Spawn the indexer for a specific list of filenames. Resolves with the
 * per-PDF results once the subprocess exits, or null on a fatal spawn
 * failure (script missing, uv missing, timeout, malformed output).
 * Errors and warnings are logged; the caller decides what to do with
 * a null return.
 */
/**
 * Outcome of a spawnIndexer call. Either an array of per-PDF results
 * (which themselves may include `failed` entries for individual PDFs),
 * or an `error` describing why the subprocess as a whole couldn't run.
 * The error is propagated into each targeted publication's `indexError`
 * field so users can see the actionable message in the UI without
 * having to read the server logs.
 */
type IndexerOutcome =
  | { kind: "results"; results: IndexResult[] }
  | { kind: "error"; error: string };

async function spawnIndexer(
  ragDbDir: string,
  pdfsDir: string,
  filenames: string[],
  opts: { extractCitations: boolean }
): Promise<IndexerOutcome> {
  return new Promise<IndexerOutcome>((resolve) => {
    // Use `uv run` so the script picks up the mcp-server's Python env
    // (same chromadb / sentence-transformers / fitz versions that
    // built the DB).
    const cmd = "uv";
    const args: string[] = [
      "run",
      "--directory",
      DUMP_CITATIONS_CWD,
      "python",
      INDEX_PUBLICATIONS_SCRIPT,
      "--db-path",
      ragDbDir,
      "--pdfs-dir",
      pdfsDir,
    ];
    if (!opts.extractCitations) args.push("--no-citations");
    for (const f of filenames) {
      args.push("--filename", f);
    }

    let stdout = "";
    let stderr = "";
    // Buffer for line-by-line stderr forwarding — we tag each line so
    // it's distinguishable from Next.js's own log output in the terminal.
    let stderrLineBuffer = "";
    let settled = false;

    let child;
    try {
      child = spawn(cmd, args, { cwd: DUMP_CITATIONS_CWD });
    } catch (err) {
      const msg =
        err instanceof Error && err.message
          ? `Failed to spawn indexer: ${err.message}. `
            + "Is `uv` installed and on PATH?"
          : "Failed to spawn indexer. Is `uv` installed and on PATH?";
      console.warn(`[knowledge-bases] ${msg}`, err);
      resolve({ kind: "error", error: msg });
      return;
    }

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        child.kill("SIGKILL");
      } catch {
        /* ignore */
      }
      const msg =
        `Indexer timed out after ${INDEX_TIMEOUT_MS / 60000} minutes.`;
      console.warn(`[knowledge-bases] ${msg} (${ragDbDir})`);
      resolve({ kind: "error", error: msg });
    }, INDEX_TIMEOUT_MS);

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      stderr += text;
      // Forward complete lines to the parent's stderr so the user can
      // watch the indexer's progress in their terminal in real time.
      // Buffer the trailing partial line until the next chunk so we
      // don't split log messages mid-line.
      stderrLineBuffer += text;
      let nl: number;
      while ((nl = stderrLineBuffer.indexOf("\n")) !== -1) {
        const line = stderrLineBuffer.slice(0, nl);
        stderrLineBuffer = stderrLineBuffer.slice(nl + 1);
        if (line.length > 0) {
          process.stderr.write(`[indexer] ${line}\n`);
        }
      }
    });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const msg =
        err instanceof Error && err.message
          ? `Indexer subprocess failed: ${err.message}. Is \`uv\` on PATH?`
          : "Indexer subprocess failed. Is `uv` on PATH?";
      console.warn(`[knowledge-bases] ${msg}`, err);
      resolve({ kind: "error", error: msg });
    });
    child.on("close", (code: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      // Flush any trailing partial stderr line that didn't end in \n.
      if (stderrLineBuffer.length > 0) {
        process.stderr.write(`[indexer] ${stderrLineBuffer}\n`);
        stderrLineBuffer = "";
      }
      if (code !== 0) {
        // Surface the script's stderr to the user. The script writes
        // actionable messages there (e.g. "Missing Python dependency...
        // Try: cd <repo>/mcp-server && uv sync"), so propagate them
        // verbatim — truncated to keep kb.json reasonable.
        const trimmed = stderr.trim();
        const msg = trimmed
          ? truncate(trimmed, 800)
          : `Indexer exited with code ${code} but no error message.`;
        console.warn(
          `[knowledge-bases] indexer exited with code ${code}: ${trimmed}`
        );
        resolve({ kind: "error", error: msg });
        return;
      }
      const trimmedOut = stdout.trim();
      if (!trimmedOut) {
        resolve({ kind: "results", results: [] });
        return;
      }
      try {
        const parsed = JSON.parse(trimmedOut);
        if (
          parsed
          && typeof parsed === "object"
          && Array.isArray(parsed.results)
        ) {
          resolve({ kind: "results", results: parsed.results as IndexResult[] });
        } else {
          const msg = "Indexer returned unexpected JSON shape.";
          console.warn(`[knowledge-bases] ${msg}`);
          resolve({ kind: "error", error: msg });
        }
      } catch (err) {
        const msg = "Could not parse indexer output as JSON.";
        console.warn(
          `[knowledge-bases] ${msg}`,
          err,
          stdout.slice(0, 500)
        );
        resolve({ kind: "error", error: msg });
      }
    });
  });
}

function truncate(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 1) + "…";
}

/**
 * Apply the indexer's per-PDF results back into the KB's publications
 * array. For each result we update `indexStatus`, `indexError`, and
 * `indexedAt`, and merge any citation fields the indexer extracted
 * inline (so the user doesn't have to wait for the next citation-dump
 * cycle to see titles).
 */
function applyIndexResults(
  publications: Publication[],
  results: IndexResult[]
): Publication[] {
  const byFilename = new Map(results.map((r) => [r.filename, r]));
  const now = new Date().toISOString();
  return publications.map((pub) => {
    const r = byFilename.get(pub.filename);
    if (!r) return pub;
    const next: Publication = { ...pub };
    if (r.status === "indexed" || r.status === "skipped") {
      next.indexStatus = "indexed";
      next.indexError = null;
      next.indexedAt = r.status === "indexed" ? now : pub.indexedAt ?? now;
      // Inline citation merge: mirrors mergeCitationsIntoPublications
      // but for one publication. Only fill in empty fields.
      if (r.citation && typeof r.citation === "object") {
        const c = r.citation as Record<string, unknown>;
        const fields = [
          "title", "authors", "abstract", "journal", "volume",
          "issue", "pages", "year", "doi", "keywords", "publisher",
        ] as const;
        for (const field of fields) {
          let incoming = c[field] as unknown;
          // The indexer hands us ChromaDB metadata — list-valued fields
          // come back as JSON-encoded strings (per build_rag.py's
          // _citation_to_metadata). Decode them.
          if ((field === "authors" || field === "keywords")
              && typeof incoming === "string") {
            try {
              incoming = JSON.parse(incoming);
            } catch {
              incoming = null;
            }
          }
          if (incoming === undefined || incoming === null) continue;
          if (Array.isArray(incoming) && incoming.length === 0) continue;
          if (typeof incoming === "string" && incoming.trim() === "") continue;
          const current = next[field];
          const isCurrentEmpty =
            current === null
            || current === undefined
            || (Array.isArray(current) && current.length === 0)
            || (typeof current === "string" && current.trim() === "");
          if (isCurrentEmpty) {
            (next as Record<string, unknown>)[field] = incoming;
          }
        }
      }
    } else {
      next.indexStatus = "failed";
      next.indexError = r.error || "Indexing failed.";
    }
    return next;
  });
}

/**
 * Kick off background indexing for the given filenames in a KB. The
 * caller should already have written `indexStatus: "queued"` (and
 * persisted) before calling this. We:
 *
 *   1. Re-read kb.json (in case other writes happened between the
 *      caller's persist and now), flip the targeted rows to "indexing",
 *      and persist.
 *   2. Spawn the Python indexer.
 *   3. On completion, re-read kb.json (it may have changed again —
 *      e.g. a second upload added more rows), apply results, persist.
 *
 * This function does NOT await the spawn — it returns immediately and
 * the caller's API response can go out while indexing continues. The
 * UI's existing 5s poll loop picks up status updates.
 *
 * If the spawn fails entirely (returns null), all targeted rows get
 * flipped to "failed" with a generic error message so the UI doesn't
 * leave them stuck on "indexing" forever.
 */
function kickOffIndexing(
  slug: string,
  filenames: string[],
  opts: { extractCitations: boolean }
): void {
  if (filenames.length === 0) return;

  // Coalesce: if we already have an in-flight job for this slug, the
  // later upload's filenames will be picked up by the next read of
  // kb.json (since indexStatus is persisted there). Don't spawn a
  // second process; the Python lock would serialize them anyway, and
  // we'd duplicate work.
  // (We still need to update statuses for the new filenames though.)
  const existing = activeIndexJobs.get(slug);

  const job = (async () => {
    // Step 1: flip queued -> indexing.
    try {
      const fresh = await readKbJsonRaw(slug);
      if (fresh) {
        const targeted = new Set(filenames);
        const updated = fresh.publications.map((p) =>
          targeted.has(p.filename)
            ? { ...p, indexStatus: "indexing" as const, indexError: null }
            : p
        );
        await writeKbJson({ ...fresh, publications: updated });
      }
    } catch (err) {
      console.warn(`[knowledge-bases] failed to mark indexing on ${slug}:`, err);
    }

    // Step 2: wait for any prior job on the same KB to settle, then
    // spawn ours. (The Python flock would serialize them anyway, but
    // this avoids holding two subprocesses open simultaneously.)
    if (existing) {
      try { await existing; } catch { /* ignore */ }
    }

    // Resolve the KB's pdfs and rag_db dirs without re-reading kb.json
    // — these are derived from the seed registry / per-KB layout and
    // don't depend on persisted state.
    const pdfDir = kbPdfDir(slug);
    const ragDbDir = kbRagDbDir(slug);

    let outcome: IndexerOutcome;
    try {
      outcome = await spawnIndexer(ragDbDir, pdfDir, filenames, opts);
    } catch (err) {
      console.warn(`[knowledge-bases] indexer threw for ${slug}:`, err);
      outcome = {
        kind: "error",
        error:
          err instanceof Error && err.message
            ? `Indexer threw: ${err.message}`
            : "Indexer threw an unknown error.",
      };
    }

    // Step 3: apply results (or mark all targeted as failed if the
    // spawn itself failed, propagating the actionable error message).
    try {
      const fresh = await readKbJsonRaw(slug);
      if (!fresh) return null;

      let publications: Publication[];
      if (outcome.kind === "error") {
        const targeted = new Set(filenames);
        publications = fresh.publications.map((p) =>
          targeted.has(p.filename)
            ? {
                ...p,
                indexStatus: "failed" as const,
                indexError: outcome.error,
                indexedAt: p.indexedAt ?? null,
              }
            : p
        );
        await writeKbJson({ ...fresh, publications });
        return null;
      }

      publications = applyIndexResults(fresh.publications, outcome.results);

      // If anything succeeded, the build status should reflect that
      // there is now (more) content in the chroma DB. We don't promote
      // pending → ready here because detectBuildArtifacts handles that
      // on the next read; we just bump lastBuiltAt so the citation
      // re-sync triggers.
      const anyIndexed = outcome.results.some((r) =>
        r.status === "indexed" || r.status === "skipped"
      );
      const next: KnowledgeBase = anyIndexed
        ? {
            ...fresh,
            publications,
            // Force a fresh citation sync next read by clearing the
            // last-sync timestamp. The dump_citations dump will pick
            // up any rows the indexer just added.
            lastCitationSyncAt: null,
            buildStatus:
              fresh.buildStatus === "pending" || fresh.buildStatus === "failed"
                ? "ready"
                : fresh.buildStatus === "stale"
                ? "ready"  // we just freshened it
                : fresh.buildStatus,
            lastBuiltAt: new Date().toISOString(),
          }
        : { ...fresh, publications };
      await writeKbJson(next);
      return outcome.results;
    } catch (err) {
      console.warn(
        `[knowledge-bases] failed to persist indexer results for ${slug}:`,
        err
      );
      return null;
    }
  })().finally(() => {
    if (activeIndexJobs.get(slug) === job) {
      activeIndexJobs.delete(slug);
    }
  });

  activeIndexJobs.set(slug, job);
}

/* ---------------------------------------------------------------------- */
/*  Reconciliation: walk the on-disk PDF dir and merge with kb.json       */
/* ---------------------------------------------------------------------- */

/**
 * For shared-path seeds, the canonical truth about which PDFs exist is
 * the on-disk directory, not kb.json — outside actors (build_rag.py, a
 * dev dropping files in via scp, git pull) can put files there too.
 *
 * This walks the directory, drops kb.json entries whose file has gone
 * missing, and adds stub entries for files we haven't seen before.
 *
 * Returns the reconciled publication list and a flag indicating whether
 * kb.json should be rewritten to persist the changes.
 */
async function reconcilePublicationsWithDisk(
  pdfDir: string,
  recorded: Publication[]
): Promise<{ publications: Publication[]; changed: boolean }> {
  let onDisk: { name: string; size: number; mtime: Date }[] = [];
  try {
    const names = await readdir(pdfDir);
    onDisk = (
      await Promise.all(
        names.map(async (name) => {
          if (!/\.pdf$/i.test(name)) return null;
          try {
            const s = await stat(path.join(pdfDir, name));
            if (!s.isFile()) return null;
            return { name, size: s.size, mtime: s.mtime };
          } catch {
            return null;
          }
        })
      )
    ).filter((x): x is { name: string; size: number; mtime: Date } => x !== null);
  } catch {
    // PDF dir doesn't exist — treat as empty.
    onDisk = [];
  }

  const onDiskByName = new Map(onDisk.map((f) => [f.name, f]));
  const recordedByName = new Map(recorded.map((p) => [p.filename, p]));

  let changed = false;
  const merged: Publication[] = [];

  // Keep recorded entries whose file is still present, refreshing size
  // and ensuring hasPdf is set to true. Indexed-only entries (entries
  // synthesized from the citations collection whose source PDF was
  // never on disk) are preserved untouched — they were never claimed
  // to be disk-backed in the first place.
  for (const pub of recorded) {
    const file = onDiskByName.get(pub.filename);
    if (!file) {
      if (pub.hasPdf === false) {
        merged.push(pub);
      } else {
        changed = true;
      }
      continue;
    }
    const wantsHasPdf = pub.hasPdf !== true;
    if (file.size !== pub.size || wantsHasPdf) {
      merged.push({ ...pub, size: file.size, hasPdf: true });
      changed = true;
    } else {
      merged.push(pub);
    }
  }

  // Add stub entries for files on disk we haven't seen yet.
  for (const file of onDisk) {
    if (recordedByName.has(file.name)) continue;
    merged.push({
      filename: file.name,
      title: null,
      authors: null,
      abstract: null,
      journal: null,
      volume: null,
      issue: null,
      pages: null,
      year: null,
      doi: null,
      keywords: null,
      publisher: null,
      size: file.size,
      addedAt: file.mtime.toISOString(),
      hasPdf: true,
    });
    changed = true;
  }

  return { publications: merged, changed };
}

/* ---------------------------------------------------------------------- */
/*  Read / write kb.json                                                  */
/* ---------------------------------------------------------------------- */

/**
 * Read kb.json + reconcile transient state (on-disk PDF inventory,
 * Chroma build status). Always returns a full KnowledgeBase reflecting
 * what's actually on disk, not just what was last persisted.
 */
async function readKbJson(slug: string): Promise<KnowledgeBase | null> {
  let parsed: KnowledgeBase;
  try {
    const raw = await readFile(kbJsonPath(slug), "utf-8");
    parsed = JSON.parse(raw) as KnowledgeBase;
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || parsed.slug !== slug) return null;

  // Defensive defaults for older records missing newer fields.
  const recorded: Publication[] = Array.isArray(parsed.publications)
    ? parsed.publications
    : [];
  const seed = SEED_BY_SLUG.get(slug);

  // Reconcile publications with disk. Always do this for shared-path
  // seeds; for plain KBs it's a no-op when kb.json and disk agree, but
  // it also lets us self-heal if a file goes missing.
  const pdfDir = kbPdfDir(slug);
  const reconciled = await reconcilePublicationsWithDisk(pdfDir, recorded);
  let publications = reconciled.publications;
  let changed = reconciled.changed;

  // Detect whether the underlying ChromaDB has been built.
  const ragDbDir = kbRagDbDir(slug);
  const build = await detectBuildArtifacts(ragDbDir);

  const recordedStatus = parsed.buildStatus ?? "pending";
  let buildStatus = recordedStatus;
  let lastBuiltAt: string | null = parsed.lastBuiltAt ?? null;
  let lastCitationSyncAt: string | null = parsed.lastCitationSyncAt ?? null;

  if (build.exists) {
    // The DB is on disk. If we previously thought it was pending /
    // failed, promote to ready. If new PDFs were added since the build
    // (kb.json says "stale"), keep that — the user explicitly told us.
    if (buildStatus === "pending" || buildStatus === "failed") {
      buildStatus = "ready";
    }
    if (!lastBuiltAt) lastBuiltAt = build.lastBuiltAt;
  } else if (recordedStatus === "ready" || recordedStatus === "stale") {
    // We thought it was built but the artifacts are gone.
    buildStatus = "pending";
    lastBuiltAt = null;
    lastCitationSyncAt = null;
  }

  // Citation enrichment: when the DB is built and either we've never
  // synced citations or the DB is newer than our last sync, trigger an
  // enrichment. The dump can synthesize publications for indexed files
  // that aren't on disk in pdfs/ — common when the rag_db was checked
  // in via git LFS but the source PDFs weren't.
  //
  // Budget strategy: if we have NO publications yet (cold start with a
  // built index), we wait longer for the dump because returning [] now
  // would force the user to refresh manually. If we already have some
  // publications, we use a shorter budget — partial data is better than
  // a slow page.
  if (build.exists && build.lastBuiltAt) {
    const needsSync =
      !lastCitationSyncAt
      || new Date(build.lastBuiltAt).getTime()
         > new Date(lastCitationSyncAt).getTime();
    if (needsSync) {
      const dumpPromise = getCitationDump(ragDbDir);
      const budgetMs = publications.length === 0 ? 8000 : 1500;
      const racy = await Promise.race<CitationDump | null | "timeout">([
        dumpPromise,
        new Promise<"timeout">((resolve) =>
          setTimeout(() => resolve("timeout"), budgetMs)
        ),
      ]);
      if (racy && racy !== "timeout") {
        const merged = mergeCitationsIntoPublications(publications, racy);
        if (merged.changed) {
          publications = merged.publications;
          changed = true;
        }
        lastCitationSyncAt = build.lastBuiltAt;
      } else if (racy === "timeout") {
        // Detach the still-pending promise so its eventual result gets
        // persisted. A fire-and-forget wrapper re-reads the (possibly
        // updated) kb.json after the dump completes, merges, and writes
        // back. Errors are swallowed.
        void dumpPromise.then(async (dump) => {
          if (!dump) return;
          try {
            const fresh = await readKbJsonRaw(slug);
            if (!fresh) return;
            const m = mergeCitationsIntoPublications(fresh.publications, dump);
            if (!m.changed) return;
            const updated: KnowledgeBase = {
              ...fresh,
              publications: m.publications,
              lastCitationSyncAt: build.lastBuiltAt,
            };
            await writeKbJson(updated);
          } catch (err) {
            console.warn(
              `[knowledge-bases] background citation merge failed:`,
              err
            );
          }
        });
      }
    }
  }

  const kb: KnowledgeBase = {
    ...parsed,
    publications,
    buildStatus,
    lastBuiltAt,
    lastCitationSyncAt,
    builtin: parsed.builtin ?? !!seed,
    linkedPaths: seed
      ? { pdfs: pdfDir, ragDb: ragDbDir }
      : parsed.linkedPaths,
    sharedWithMcp: seed?.sharedWithMcp ?? parsed.sharedWithMcp,
  };

  if (changed) {
    // Persist the reconciled state so subsequent reads are cheaper and
    // so a `kb.json`-only consumer (e.g. the chat route, eventually)
    // sees an up-to-date publication list.
    // NOTE: we persist BEFORE attaching indexProgress below, since
    // progress is transient run state that doesn't belong in kb.json.
    await writeKbJson(kb);
  }

  // Attach live indexer progress (read fresh from disk every time;
  // never persisted into kb.json). Done after the optional persist so
  // the in-memory return value carries it but the on-disk file does
  // not.
  const progress = await readIndexProgress(ragDbDir);
  if (progress) {
    kb.indexProgress = progress;
  }

  return kb;
}

/**
 * Read kb.json without any reconciliation, enrichment, or write-back.
 * Used by the background citation merge to fetch the latest persisted
 * state before merging in a slow-arriving citation dump.
 */
async function readKbJsonRaw(slug: string): Promise<KnowledgeBase | null> {
  try {
    const raw = await readFile(kbJsonPath(slug), "utf-8");
    const parsed = JSON.parse(raw) as KnowledgeBase;
    if (!parsed || typeof parsed !== "object" || parsed.slug !== slug) {
      return null;
    }
    return {
      ...parsed,
      publications: Array.isArray(parsed.publications) ? parsed.publications : [],
    };
  } catch {
    return null;
  }
}

async function writeKbJson(kb: KnowledgeBase): Promise<void> {
  // The kb.json itself always lives under knowledgeBasesDir/<slug>/, even
  // for seeds whose pdfs/ and rag_db/ point elsewhere.
  await mkdir(kbDir(kb.slug), { recursive: true });
  // Make sure the (possibly shared) PDF dir exists so subsequent uploads
  // don't trip over a missing parent.
  await mkdir(kbPdfDir(kb.slug), { recursive: true });
  // Strip transient run state before serializing. `indexProgress` is
  // re-read from disk on every fetch and must not be persisted.
  const { indexProgress: _omit, ...rest } = kb;
  void _omit;
  const next: KnowledgeBase = { ...rest, updatedAt: new Date().toISOString() };
  await writeFile(kbJsonPath(kb.slug), JSON.stringify(next, null, 2), "utf-8");
}

/* ---------------------------------------------------------------------- */
/*  First-run seeding                                                     */
/* ---------------------------------------------------------------------- */

/**
 * Create any built-in KBs that don't yet exist on disk. Idempotent —
 * existing seeds are left untouched. We re-run this on every list/get
 * call (it's a single readFile probe per seed).
 */
async function ensureSeeds(): Promise<void> {
  await mkdir(config.knowledgeBasesDir, { recursive: true });
  for (const seed of builtinSeeds()) {
    // Use raw-ish read here to avoid the reconciliation pass triggering
    // before we've written kb.json.
    let alreadyExists = false;
    try {
      await stat(kbJsonPath(seed.slug));
      alreadyExists = true;
    } catch {
      alreadyExists = false;
    }
    if (alreadyExists) continue;

    const now = new Date().toISOString();
    const kb: KnowledgeBase = {
      slug: seed.slug,
      name: seed.name,
      description: seed.description,
      builtin: true,
      publications: [],
      buildStatus: "pending",
      lastBuiltAt: null,
      createdAt: now,
      updatedAt: now,
      linkedPaths: { pdfs: kbPdfDir(seed.slug), ragDb: kbRagDbDir(seed.slug) },
      sharedWithMcp: !!seed.sharedWithMcp,
    };
    await writeKbJson(kb);
  }
}

/* ---------------------------------------------------------------------- */
/*  Public API                                                            */
/* ---------------------------------------------------------------------- */

export async function listKnowledgeBases(): Promise<KnowledgeBaseSummary[]> {
  await ensureSeeds();
  let entries: string[] = [];
  try {
    entries = await readdir(config.knowledgeBasesDir);
  } catch {
    return [];
  }

  const result: KnowledgeBaseSummary[] = [];
  for (const entry of entries) {
    if (!isValidSlug(entry)) continue;
    let isDir = false;
    try {
      isDir = (await stat(path.join(config.knowledgeBasesDir, entry))).isDirectory();
    } catch {
      continue;
    }
    if (!isDir) continue;
    const kb = await readKbJson(entry);
    if (!kb) continue;
    const { publications, ...rest } = kb;
    result.push({ ...rest, publicationCount: publications.length });
  }

  // Built-ins first, then by createdAt descending.
  result.sort((a, b) => {
    if (!!a.builtin !== !!b.builtin) return a.builtin ? -1 : 1;
    return b.createdAt.localeCompare(a.createdAt);
  });
  return result;
}

export async function getKnowledgeBase(slug: string): Promise<KnowledgeBase | null> {
  if (!isValidSlug(slug)) return null;
  await ensureSeeds();
  return readKbJson(slug);
}

/** Throws if the slug is already in use. */
export async function createKnowledgeBase(input: {
  name: string;
  description: string;
  slug?: string;
}): Promise<KnowledgeBase> {
  await ensureSeeds();
  const name = input.name.trim();
  if (!name) throw new Error("Name is required.");
  const slug = input.slug && isValidSlug(input.slug) ? input.slug : slugifyKbName(name);
  if (!isValidSlug(slug)) throw new Error(`Invalid slug: ${slug}`);
  if (SEED_BY_SLUG.has(slug)) {
    throw new Error(
      `Slug "${slug}" is reserved for a built-in knowledge base.`
    );
  }
  const existingPath = kbJsonPath(slug);
  try {
    await stat(existingPath);
    throw new Error(`A knowledge base with slug "${slug}" already exists.`);
  } catch (err) {
    // Re-throw the "already exists" error; swallow the ENOENT.
    if (err instanceof Error && err.message.startsWith("A knowledge base")) {
      throw err;
    }
  }

  const now = new Date().toISOString();
  const kb: KnowledgeBase = {
    slug,
    name,
    description: input.description.trim(),
    builtin: false,
    publications: [],
    buildStatus: "pending",
    lastBuiltAt: null,
    createdAt: now,
    updatedAt: now,
  };
  await writeKbJson(kb);
  return kb;
}

export async function updateKnowledgeBase(
  slug: string,
  patch: { name?: string; description?: string }
): Promise<KnowledgeBase> {
  const kb = await readKbJson(slug);
  if (!kb) throw new Error(`Knowledge base not found: ${slug}`);
  const next: KnowledgeBase = {
    ...kb,
    name: patch.name !== undefined ? patch.name.trim() || kb.name : kb.name,
    description:
      patch.description !== undefined ? patch.description.trim() : kb.description,
  };
  await writeKbJson(next);
  return next;
}

export async function deleteKnowledgeBase(slug: string): Promise<void> {
  const kb = await readKbJson(slug);
  if (!kb) return;
  if (kb.builtin) {
    throw new Error(`Cannot delete built-in knowledge base: ${slug}`);
  }
  // Only delete the kb's own subdirectory under knowledgeBasesDir. For
  // shared-path seeds this is moot (they can't be deleted) but the guard
  // above catches that — and as belt-and-braces we also refuse to delete
  // anything outside knowledgeBasesDir.
  const target = kbDir(slug);
  const root = path.resolve(config.knowledgeBasesDir);
  if (!path.resolve(target).startsWith(root + path.sep)) {
    throw new Error(`Refusing to delete path outside knowledgeBasesDir: ${target}`);
  }
  await rm(target, { recursive: true, force: true });
}

/**
 * Add one or more PDFs to a KB. PDFs are written to the KB's PDF
 * directory (which may be a shared location for built-in seeds) and
 * recorded in `kb.json`. Citation metadata starts null — extraction
 * happens when build_rag.py is run against the corpus.
 *
 * Returns the updated KB. Throws if the KB does not exist or any file
 * is not a PDF / exceeds the size limit.
 */
export async function addPublications(
  slug: string,
  files: { name: string; bytes: Buffer }[]
): Promise<KnowledgeBase> {
  // ensureSeeds first so the seeded KBs (e.g. molten-salt-papers)
  // exist on disk even if the user hits this endpoint directly without
  // having loaded the list page first.
  await ensureSeeds();
  const kb = await readKbJson(slug);
  if (!kb) throw new Error(`Knowledge base not found: ${slug}`);
  if (files.length === 0) return kb;

  const pdfDir = kbPdfDir(slug);
  await mkdir(pdfDir, { recursive: true });

  const existingNames = new Set<string>();
  try {
    const onDisk = await readdir(pdfDir);
    onDisk.forEach((n) => existingNames.add(n));
  } catch {
    /* directory may not exist yet — that's fine, we just created it */
  }

  const added: Publication[] = [];
  const now = new Date().toISOString();
  for (const file of files) {
    if (file.bytes.length > MAX_PDF_SIZE_BYTES) {
      throw new Error(
        `File '${file.name}' exceeds the ${MAX_PDF_SIZE_BYTES / (1024 * 1024)}MB PDF size limit.`
      );
    }
    if (!/\.pdf$/i.test(file.name)) {
      throw new Error(`File '${file.name}' is not a PDF.`);
    }
    const safeName = ensureUniqueFilename(existingNames, sanitizePdfFilename(file.name));
    existingNames.add(safeName);
    const target = kbPdfPath(slug, safeName);
    await writeFile(target, file.bytes);
    added.push({
      filename: safeName,
      title: null,
      authors: null,
      abstract: null,
      journal: null,
      volume: null,
      issue: null,
      pages: null,
      year: null,
      doi: null,
      keywords: null,
      publisher: null,
      size: file.bytes.length,
      addedAt: now,
      hasPdf: true,
      // Queue immediately so the UI shows "indexing" rather than
      // "unindexed" — the kickOffIndexing() call below flips this to
      // "indexing" once the subprocess actually starts.
      indexStatus: "queued",
      indexError: null,
    });
  }

  // Adding new PDFs invalidates any prior build.
  const next: KnowledgeBase = {
    ...kb,
    publications: [...kb.publications, ...added],
    buildStatus: kb.buildStatus === "ready" ? "stale" : kb.buildStatus,
  };
  await writeKbJson(next);

  // Spawn the indexer in the background. It will: (a) flip queued ->
  // indexing on these rows, (b) chunk + embed each PDF into the KB's
  // chroma DB, (c) flip indexing -> indexed (or failed) and persist.
  // The UI's poll loop picks up status changes within ~5s.
  //
  // Citation extraction needs LLM credentials. Enable it if EITHER
  // schema is set: Azure-style (AZURE_OPENAI_*) or generic OpenAI-
  // compatible (OPENAI_API_KEY). The Python side's _resolve_llm_config
  // mirrors the same logic — keep these in sync. If neither is set we
  // still index text chunks (semantic search still works; titles just
  // won't auto-populate).
  const hasAzureCreds =
    !!process.env.AZURE_OPENAI_ENDPOINT
    && !!process.env.AZURE_OPENAI_API_KEY
    && !!process.env.AZURE_OPENAI_DEPLOYMENT_NAME;
  const hasOpenAICreds = !!process.env.OPENAI_API_KEY;
  // Legacy fallback: ENDPOINT_URL + DEPLOYMENT_NAME + AZURE_OPENAI_API_KEY
  // also worked in older setups; honor it here so we don't surprise
  // anyone who hasn't migrated their .env yet.
  const hasLegacyAzureCreds =
    !!process.env.ENDPOINT_URL
    && !!process.env.DEPLOYMENT_NAME
    && !!process.env.AZURE_OPENAI_API_KEY;
  const extractCitations = hasAzureCreds || hasOpenAICreds || hasLegacyAzureCreds;
  kickOffIndexing(
    slug,
    added.map((p) => p.filename),
    { extractCitations }
  );

  return next;
}

export async function removePublication(
  slug: string,
  filename: string
): Promise<KnowledgeBase> {
  const kb = await readKbJson(slug);
  if (!kb) throw new Error(`Knowledge base not found: ${slug}`);
  const safe = sanitizePdfFilename(filename);
  const remaining = kb.publications.filter((p) => p.filename !== safe);
  try {
    await rm(kbPdfPath(slug, safe), { force: true });
  } catch {
    /* best-effort */
  }
  const next: KnowledgeBase = {
    ...kb,
    publications: remaining,
    buildStatus: kb.buildStatus === "ready" ? "stale" : kb.buildStatus,
  };
  await writeKbJson(next);
  return next;
}

export async function getPublicationFile(
  slug: string,
  filename: string
): Promise<{ path: string; size: number } | null> {
  if (!isValidSlug(slug)) return null;
  const safe = sanitizePdfFilename(filename);
  const filePath = kbPdfPath(slug, safe);
  try {
    const s = await stat(filePath);
    if (!s.isFile()) return null;
    return { path: filePath, size: s.size };
  } catch {
    return null;
  }
}
