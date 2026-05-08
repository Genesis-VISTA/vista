import path from "path";
import fs from "node:fs";

/**
 * Load environment variables from the repo-root `.env` file (one
 * directory above `ui/`) into `process.env`, but only for variables
 * not already set. This lets the Knowledge Bases page honor the same
 * `VISTA_MCP_MOLTEN_SALTS_DB_PATH` / `VISTA_MCP_RAG_PDFS_PATH` settings
 * (and the legacy `VISTA_MCP_RAG_DB_PATH` alias) that the Python MCP
 * server's pydantic-settings loader reads from `<repo>/.env` — without
 * those vars also being in `ui/.env.local`.
 *
 * Best-effort and silent: if the file is missing, malformed, or
 * unreadable, we just skip. We don't bring in a dependency like
 * `dotenv` for a 20-line parser.
 */
function loadRepoRootEnv(): void {
  const repoEnv = path.resolve(path.join(process.cwd(), "../.env"));
  let contents: string;
  try {
    contents = fs.readFileSync(repoEnv, "utf-8");
  } catch {
    return;
  }
  for (const rawLine of contents.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (!key || key in process.env) continue;
    let value = line.slice(eq + 1).trim();
    // Strip matching surrounding quotes.
    if (
      (value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

loadRepoRootEnv();

export const config = {
  /**
   * Directory to search for skills.
   * Override with the SKILLS_DIR environment variable.
   */
  skillsDir: path.resolve(process.env.SKILLS_DIR ?? path.join(process.cwd(), "../skills")),

  /**
   * Directory where uploaded files are stored.
   * Override with the UPLOADS_DIR environment variable.
   */
  uploadsDir: path.resolve(process.env.UPLOADS_DIR ?? path.join(process.cwd(), "../data/uploads")),

  /**
   * Directory where Knowledge Base metadata + their PDF corpora are stored.
   * Each user-created KB gets a subdirectory:
   *   <knowledgeBasesDir>/<slug>/{kb.json, pdfs/, rag_db/}
   *
   * Default `<repo>/knowledge_bases` — the same root as the seeded
   * `molten_salts_db` corpus. Each user-created KB sits as a sibling
   * directory next to it (e.g. `<repo>/knowledge_bases/my-papers/`).
   *
   * Built-in KBs (e.g. Molten Salt Papers) may instead point at shared
   * paths via the seed configuration in `lib/knowledge-bases-server.ts`
   * — for the molten-salt seed, those paths are `<repo>/pdfs` (source
   * PDFs) and `<repo>/knowledge_bases/molten_salts_db` (chroma).
   *
   * Override with the KNOWLEDGE_BASES_DIR environment variable.
   */
  knowledgeBasesDir: path.resolve(
    process.env.KNOWLEDGE_BASES_DIR ?? path.join(process.cwd(), "../knowledge_bases")
  ),

  /**
   * Path to the shared ChromaDB database that the Python MCP server's
   * rag_search tool reads from. The seeded "Molten Salt Papers" KB is
   * pinned to this location so adding a PDF in the UI lands in the same
   * corpus the MCP server queries.
   *
   * Configured by `VISTA_MCP_MOLTEN_SALTS_DB_PATH` in the repo-root .env
   * (also auto-loaded above). The legacy name `VISTA_MCP_RAG_DB_PATH` is
   * still honored as a fallback so older .env files keep working.
   *
   * Default `../knowledge_bases/molten_salts_db` — i.e.
   * `<repo>/knowledge_bases/molten_salts_db`, which is what
   * `mcp-server/.../rag_mcp.py` resolves to when launched from
   * `mcp-server/` (cwd `<repo>/mcp-server`).
   */
  ragDbPath: path.resolve(
    process.env.VISTA_MCP_MOLTEN_SALTS_DB_PATH
      ?? process.env.VISTA_MCP_RAG_DB_PATH
      ?? path.join(process.cwd(), "../knowledge_bases/molten_salts_db")
  ),

  /**
   * Path to the source PDF folder consumed by `build_rag.py` to populate
   * the shared ChromaDB. The seeded "Molten Salt Papers" KB writes new
   * uploads here so the next index rebuild picks them up.
   *
   * Override with VISTA_MCP_RAG_PDFS_PATH. Default `../pdfs` — matching
   * the `pdf_folder="./pdfs"` convention in `build_rag.py`.
   */
  ragPdfsPath: path.resolve(
    process.env.VISTA_MCP_RAG_PDFS_PATH ?? path.join(process.cwd(), "../pdfs")
  ),
};
