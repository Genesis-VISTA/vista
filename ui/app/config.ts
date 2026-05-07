import path from "path";
import fs from "node:fs";

/**
 * Load environment variables from the repo-root `.env` file (one
 * directory above `ui/`) into `process.env`, but only for variables
 * not already set. This lets the Knowledge Base Explorer honor the
 * same `VISTA_MCP_RAG_DB_PATH` / `VISTA_MCP_RAG_PDFS_PATH` settings
 * that the Python MCP server's pydantic-settings loader reads from
 * `<repo>/.env` — without those vars also being in `ui/.env.local`.
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
   * Built-in KBs (e.g. Molten Salt Papers) may instead point at a shared
   * pdfs/ and rag_db/ pair via the seed configuration in
   * `lib/knowledge-bases-server.ts`.
   * Override with the KNOWLEDGE_BASES_DIR environment variable.
   */
  knowledgeBasesDir: path.resolve(
    process.env.KNOWLEDGE_BASES_DIR ?? path.join(process.cwd(), "../data/knowledge-bases")
  ),

  /**
   * Path to the shared ChromaDB database that the Python MCP server's
   * rag_search tool reads from. The seeded "Molten Salt Papers" KB is
   * pinned to this location so adding a PDF in the UI lands in the same
   * corpus the MCP server queries.
   *
   * Mirrors the MCP server's `VISTA_MCP_RAG_DB_PATH` setting so a single
   * env var in `.env` configures both sides. Default `../rag_db` — i.e.
   * `<repo>/rag_db`, which is what `mcp-server/.../rag_mcp.py` resolves
   * to when launched from `mcp-server/` (cwd `<repo>/mcp-server`).
   */
  ragDbPath: path.resolve(
    process.env.VISTA_MCP_RAG_DB_PATH ?? path.join(process.cwd(), "../rag_db")
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
