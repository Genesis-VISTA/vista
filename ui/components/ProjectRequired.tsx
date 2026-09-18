"use client";

import { activateProject, useProjects, type Project } from "@/lib/projects";

type Props = {
  /** What the page would show once a project is chosen, e.g. "datasets". */
  what: string;
};

/**
 * One panel for every page that needs a project and does not have one.
 *
 * Datasets, skills and chat each used to handle this differently — one showed
 * an empty list, one a sentence in a chat bubble, one nothing at all. Choosing
 * here keeps you on the page you opened, because you already said where you
 * wanted to be.
 */
export function ProjectRequired({ what }: Props) {
  const { projects, loading, error } = useProjects();

  return (
    <div className="project-required">
      <h2 className="project-required-title">Pick a project</h2>
      <p className="project-required-body">
        {what} belong to a project. Choose one and this page will fill in.
      </p>

      {error && <div className="error project-required-error">{error}</div>}

      {loading && projects.length === 0 ? (
        <p className="project-required-body">Loading projects…</p>
      ) : projects.length === 0 ? (
        <p className="project-required-body">
          There are no projects yet. Create one from the Projects page.
        </p>
      ) : (
        <div className="project-required-list">
          {projects.map((project: Project) => (
            <button
              key={project.id}
              type="button"
              className="project-required-option"
              onClick={() => activateProject(project)}
            >
              <span className="project-required-option-name">{project.name}</span>
              {project.description && (
                <span className="project-required-option-desc">{project.description}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default ProjectRequired;
