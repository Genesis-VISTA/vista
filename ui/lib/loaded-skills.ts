/**
 * Per-project loaded skills.
 *
 * A project's loaded skills are its `skills` list in the backend, which is
 * exactly what the agent loads into its sandbox and prompt. Loading or
 * unloading a skill rewrites that list, so it applies to every member of the
 * project and the agent picks it up on its next turn.
 */

import { toCreate, updateProject, type Project } from "@/lib/projects";

/** Load (`loaded`) or unload a skill for `project`, saving the project's skill list. */
export async function setSkillLoaded(
  project: Project,
  slug: string,
  loaded: boolean
): Promise<Project> {
  if (project.skills.includes(slug) === loaded) return project;
  const skills = loaded
    ? [...project.skills, slug]
    : project.skills.filter((s) => s !== slug);
  return updateProject(project.name, toCreate({ ...project, skills }));
}
