import path from "path";

export const config = {
  /**
   * Directory to search for skills.
   * Override with the SKILLS_DIR environment variable.
   */
  skillsDir: path.resolve(process.env.SKILLS_DIR ?? path.join(process.cwd(), "../skills")),
};
