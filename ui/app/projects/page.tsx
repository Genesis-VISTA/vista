"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { SkillSummary } from "@/lib/types";
import {
  ACTIVE_PROJECT_KEY,
  BUILTIN_PROJECTS,
  type Project,
  activateProject,
  notifyActiveProjectChanged,
  slugify,
  useActiveProject,
  useUserProjects,
  writeUserProjects,
} from "@/lib/projects";

type ModalState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; project: Project };

export default function ProjectsPage() {
  // useSearchParams forces this page out of static prerender; wrap in
  // Suspense so the rest of the tree can still stream in.
  return (
    <Suspense fallback={<div className="standalone-page" />}>
      <ProjectsPageContent />
    </Suspense>
  );
}

function ProjectsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const userProjects = useUserProjects();
  const activeProject = useActiveProject();
  const activeSlug = activeProject?.slug ?? null;
  const [skillCatalog, setSkillCatalog] = useState<SkillSummary[]>([]);
  // The NavRail's "+ New project" link routes here with `?new=1` to auto-open
  // the create modal. Read it during initial state setup so we don't need a
  // setState-in-effect to flip the modal open after mount.
  const shouldOpenNew = searchParams?.get("new") === "1";
  const [modal, setModal] = useState<ModalState>(
    shouldOpenNew ? { mode: "create" } : { mode: "closed" }
  );

  useEffect(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkillCatalog(Array.isArray(data) ? data : []))
      .catch(() => setSkillCatalog([]));
  }, []);

  // Strip `?new=1` from the URL after we've used it so refreshes don't
  // re-open the modal and the URL stays clean.
  useEffect(() => {
    if (shouldOpenNew) {
      router.replace("/projects");
    }
  }, [shouldOpenNew, router]);

  const allProjects = useMemo(
    () => [...BUILTIN_PROJECTS, ...userProjects],
    [userProjects]
  );

  function open(project: Project) {
    activateProject(project);
    router.push("/");
  }

  function saveProject(next: Project, originalSlug?: string) {
    const current = userProjects;
    const list = originalSlug
      ? current.map((p) => (p.slug === originalSlug ? next : p))
      : [...current, next];
    writeUserProjects(list);
    setModal({ mode: "closed" });
  }

  function deleteProject(project: Project) {
    if (project.builtin) return;
    const ok = window.confirm(`Delete project "${project.title}"? This cannot be undone.`);
    if (!ok) return;
    writeUserProjects(userProjects.filter((p) => p.slug !== project.slug));
    if (activeSlug === project.slug) {
      // The active project just disappeared; clear the pointer (loadedSlugs
      // is left alone so the user doesn't lose their working set silently).
      try {
        window.localStorage.removeItem(ACTIVE_PROJECT_KEY);
      } catch {
        // ignore
      }
      notifyActiveProjectChanged();
    }
  }

  return (
    <div className="standalone-page" style={{ paddingBottom: 0 }}>
      <header style={{ marginBottom: 16 }}>
        <h1 style={{ margin: "0 0 4px", fontSize: 22, fontFamily: "Figtree, sans-serif" }}>
          Projects
        </h1>
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
          Pick a project to load its dataset, skills, and knowledge bases for the chat session.
        </p>
      </header>

      <div className="projects-grid">
        {allProjects.map((project) => (
          <ProjectCard
            key={project.slug}
            project={project}
            active={activeSlug === project.slug}
            onOpen={() => open(project)}
            onEdit={!project.builtin ? () => setModal({ mode: "edit", project }) : undefined}
            onDelete={!project.builtin ? () => deleteProject(project) : undefined}
          />
        ))}
        <button
          type="button"
          className="project-new"
          onClick={() => setModal({ mode: "create" })}
        >
          <div className="project-new-plus" aria-hidden="true">+</div>
          <div className="project-new-label">New project</div>
        </button>
      </div>

      {modal.mode !== "closed" && (
        <ProjectModal
          mode={modal.mode}
          initial={modal.mode === "edit" ? modal.project : undefined}
          skillCatalog={skillCatalog}
          existingSlugs={new Set(allProjects.map((p) => p.slug))}
          onCancel={() => setModal({ mode: "closed" })}
          onSave={(next) =>
            saveProject(next, modal.mode === "edit" ? modal.project.slug : undefined)
          }
        />
      )}
    </div>
  );
}

function ProjectCard({
  project,
  active,
  onOpen,
  onEdit,
  onDelete,
}: {
  project: Project;
  active: boolean;
  onOpen: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  return (
    <div className={`project-card${active ? " active" : ""}`}>
      <div className="project-card-head">
        <div>
          <div className="project-card-title">{project.title}</div>
          {project.builtin && <span className="project-card-badge">Built-in</span>}
          {active && <span className="project-card-badge active">Active</span>}
        </div>
      </div>
      <div className="project-card-desc">{project.description}</div>

      <ChipRow label="Skills" items={project.skills} />
      <ChipRow label="Datasets" items={project.datasets} />
      <ChipRow label="Knowledge Bases" items={project.knowledgeBases} />

      <div className="project-card-actions">
        <button type="button" className="button" onClick={onOpen}>
          {active ? "Reopen" : "Open"}
        </button>
        {onEdit && (
          <button type="button" className="button ghost button-sm" onClick={onEdit}>
            Edit
          </button>
        )}
        {onDelete && (
          <button type="button" className="button ghost button-sm" onClick={onDelete}>
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

function ChipRow({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="project-card-row">
      <div className="project-card-row-label">{label}</div>
      <div className="project-card-chips">
        {items.length === 0 ? (
          <span className="project-card-chip empty">none</span>
        ) : (
          items.map((item) => (
            <span key={item} className="project-card-chip">
              {item}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function ProjectModal({
  mode,
  initial,
  skillCatalog,
  existingSlugs,
  onCancel,
  onSave,
}: {
  mode: "create" | "edit";
  initial?: Project;
  skillCatalog: SkillSummary[];
  existingSlugs: Set<string>;
  onCancel: () => void;
  onSave: (next: Project) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [skills, setSkills] = useState<Set<string>>(new Set(initial?.skills ?? []));
  const [datasetsRaw, setDatasetsRaw] = useState((initial?.datasets ?? []).join(", "));
  const [kbsRaw, setKbsRaw] = useState((initial?.knowledgeBases ?? []).join(", "));
  const [error, setError] = useState("");

  function toggleSkill(slug: string) {
    setSkills((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function commit() {
    const trimmedTitle = title.trim();
    const trimmedDesc = description.trim();
    if (!trimmedTitle) {
      setError("Title is required.");
      return;
    }
    if (!trimmedDesc) {
      setError("Description is required.");
      return;
    }
    const slug = initial?.slug ?? slugify(trimmedTitle);
    if (mode === "create" && existingSlugs.has(slug)) {
      setError(`A project with slug "${slug}" already exists.`);
      return;
    }
    const splitTags = (raw: string) =>
      raw
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
    onSave({
      slug,
      title: trimmedTitle,
      description: trimmedDesc,
      skills: Array.from(skills),
      datasets: splitTags(datasetsRaw),
      knowledgeBases: splitTags(kbsRaw),
    });
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal project-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={mode === "create" ? "Create project" : "Edit project"}
      >
        <div className="panel-header">
          <div className="panel-title">{mode === "create" ? "New project" : "Edit project"}</div>
          <button type="button" className="button ghost" onClick={onCancel}>
            Close
          </button>
        </div>
        <div className="modal-body">
          <label className="project-modal-label">
            Title
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
              maxLength={80}
            />
          </label>

          <label className="project-modal-label">
            Description
            <textarea
              className="input"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={400}
            />
          </label>

          <div className="project-modal-label">
            <div>Skills</div>
            <div className="project-modal-skill-list">
              {skillCatalog.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--muted)" }}>Loading skills...</div>
              ) : (
                skillCatalog.map((s) => (
                  <label key={s.slug} className="project-modal-skill-item">
                    <input
                      type="checkbox"
                      checked={skills.has(s.slug)}
                      onChange={() => toggleSkill(s.slug)}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>{s.name}</div>
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>
                        {s.description.slice(0, 120)}
                        {s.description.length > 120 ? "..." : ""}
                      </div>
                    </div>
                  </label>
                ))
              )}
            </div>
          </div>

          <label className="project-modal-label">
            Datasets <span style={{ color: "var(--muted)", fontWeight: 400 }}>(comma-separated)</span>
            <input
              className="input"
              value={datasetsRaw}
              onChange={(e) => setDatasetsRaw(e.target.value)}
              placeholder="e.g. MSTDB, AlloyDB"
            />
          </label>

          <label className="project-modal-label">
            Knowledge Bases <span style={{ color: "var(--muted)", fontWeight: 400 }}>(comma-separated)</span>
            <input
              className="input"
              value={kbsRaw}
              onChange={(e) => setKbsRaw(e.target.value)}
              placeholder="e.g. Molten Salt Papers"
            />
          </label>

          {error && (
            <div className="error" style={{ fontSize: 12 }}>
              {error}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <button type="button" className="button ghost" onClick={onCancel}>
              Cancel
            </button>
            <button type="button" className="button" onClick={commit}>
              {mode === "create" ? "Create" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
