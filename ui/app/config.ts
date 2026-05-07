import path from "path";

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
   * Each KB gets a subdirectory: <knowledgeBasesDir>/<slug>/{kb.json, pdfs/, rag_db/}
   * Override with the KNOWLEDGE_BASES_DIR environment variable.
   */
  knowledgeBasesDir: path.resolve(
    process.env.KNOWLEDGE_BASES_DIR ?? path.join(process.cwd(), "../data/knowledge-bases")
  ),
};
