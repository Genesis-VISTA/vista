"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { activateProject, useActiveProject, useProjects, type Project } from "@/lib/projects";

/**
 * The project crumb in the shared header, as a switcher.
 *
 * Before this there was exactly one way to change project in the whole app,
 * and it lived on a page you had to navigate to first, which is most of what
 * made a project feel like a mode you enter and leave. Switching here does not
 * navigate: you changed which project, not what you were doing.
 */
export function ProjectSwitcher() {
  const router = useRouter();
  const activeProject = useActiveProject();
  const { projects, loading } = useProjects();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function choose(project: Project) {
    setOpen(false);
    buttonRef.current?.focus();
    if (project.name === activeProject?.name) return;
    activateProject(project);
  }

  const label = activeProject?.name ?? (loading ? "Loading…" : "No project");

  return (
    <div className="project-switcher" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="project-switcher-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={activeProject ? `Project: ${activeProject.name}. Switch project` : "Choose a project"}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="project-switcher-name">{label}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="project-switcher-menu" role="listbox" aria-label="Projects">
          {projects.length === 0 && (
            <div className="project-switcher-empty">
              {loading ? "Loading projects…" : "No projects yet."}
            </div>
          )}
          {projects.map((project) => {
            const isActive = project.name === activeProject?.name;
            return (
              <button
                key={project.id}
                type="button"
                role="option"
                aria-selected={isActive}
                className="project-switcher-option"
                data-active={isActive ? "true" : "false"}
                onClick={() => choose(project)}
              >
                <span className="project-switcher-dot" aria-hidden="true" />
                <span className="project-switcher-option-name">{project.name}</span>
              </button>
            );
          })}
          <button
            type="button"
            className="project-switcher-manage"
            onClick={() => {
              setOpen(false);
              router.push("/projects");
            }}
          >
            Manage projects…
          </button>
        </div>
      )}
    </div>
  );
}

export default ProjectSwitcher;
