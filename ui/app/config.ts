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
};
