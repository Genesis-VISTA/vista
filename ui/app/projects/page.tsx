"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { KnowledgeBaseSummary, SkillSummary } from "@/lib/types";
import { AppTopBar } from "@/components/AppTopBar";
import { relativeTime, useProjectStats } from "@/lib/project-stats";
import { DEFAULT_DESTINATION, takePendingDestination } from "@/lib/pending-destination";
import {
  type Project,
  type UserPublic,
  activateProject,
  addProjectMember,
  createProject,
  deleteProject,
  listProjectMembers,
  notifyActiveProjectChanged,
  toCreate,
  updateProject,
  useActiveProject,
  useProjects,
  writeActiveProjectName,
} from "@/lib/projects";

/** Project draft used by the create/edit modal (no `id` until persisted). */
type ProjectDraft = Omit<Project, "id">;

type ModalState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; project: Project }
  | { mode: "members"; project: Project };

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
  const { projects, loading, error, refresh } = useProjects();
  const activeProject = useActiveProject();
  const activeName = activeProject?.name ?? null;
  const [skillCatalog, setSkillCatalog] = useState<SkillSummary[]>([]);
  const [kbCatalog, setKbCatalog] = useState<KnowledgeBaseSummary[]>([]);
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
    fetch("/api/knowledge-bases")
      .then((res) => res.json())
      .then((data) => setKbCatalog(Array.isArray(data) ? data : []))
      .catch(() => setKbCatalog([]));
  }, []);

  // Strip `?new=1` from the URL after we've used it so refreshes don't
  // re-open the modal and the URL stays clean.
  useEffect(() => {
    if (shouldOpenNew) {
      router.replace("/projects");
    }
  }, [shouldOpenNew, router]);

  function open(project: Project) {
    activateProject(project);
    // Back to whatever sent them here, or to chat if they started on this page.
    router.push(takePendingDestination() ?? DEFAULT_DESTINATION);
  }

  async function saveProject(draft: ProjectDraft, editing?: Project) {
    if (editing) {
      await updateProject(editing.name, toCreate(draft));
      // The active-project pointer stores the name; if we just renamed the
      // active project, repoint it so it doesn't dangle.
      if (editing.name !== draft.name && activeName === editing.name) {
        writeActiveProjectName(draft.name);
        notifyActiveProjectChanged();
      }
    } else {
      await createProject(toCreate(draft));
    }
    setModal({ mode: "closed" });
  }

  async function removeProject(project: Project) {
    const ok = window.confirm(
      `Delete project "${project.name}"? This cannot be undone.`
    );
    if (!ok) return;
    await deleteProject(project.name);
    if (activeName === project.name) {
      writeActiveProjectName(null);
      notifyActiveProjectChanged();
    }
  }

  return (
    <div className="app-page">
      <AppTopBar title="Projects" showProject={false} />
      <div className="app-page-body">
      <p className="page-lede">
        Pick a project to scope the chat session with its system prompt, skills, and tools.
      </p>

      {error && (
        <div className="error" style={{ marginBottom: 12, fontSize: 13 }}>
          {error}{" "}
          <button type="button" className="button ghost button-xs" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      )}

      <div className="projects-panel">
        {!loading && projects.length === 0 ? (
          <div className="projects-empty">
            <h2 className="projects-empty-title">No projects yet</h2>
            <p className="projects-empty-body">
              A project is a working context: its own system prompt, the skills and
              tools the agent may use, and the conversations and datasets that
              belong to it. Chat starts here, so this is the first thing to make.
            </p>
            <button
              type="button"
              className="button"
              onClick={() => setModal({ mode: "create" })}
            >
              Create a project
            </button>
          </div>
        ) : (
        <div className="projects-grid">
          {loading && projects.length === 0 && (
            <div className="chat-bubble">Loading projects…</div>
          )}
          {projects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              active={activeName === project.name}
              onOpen={() => open(project)}
              onEdit={() => setModal({ mode: "edit", project })}
              onMembers={() => setModal({ mode: "members", project })}
              onDelete={() => void removeProject(project)}
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
        )}
      </div>
      </div>

      {(modal.mode === "create" || modal.mode === "edit") && (
        <ProjectModal
          mode={modal.mode}
          initial={modal.mode === "edit" ? modal.project : undefined}
          skillCatalog={skillCatalog}
          kbCatalog={kbCatalog}
          existingNames={new Set(projects.map((p) => p.name))}
          onCancel={() => setModal({ mode: "closed" })}
          onSave={(draft) =>
            saveProject(draft, modal.mode === "edit" ? modal.project : undefined)
          }
        />
      )}

      {modal.mode === "members" && (
        <MembersModal
          project={modal.project}
          onClose={() => setModal({ mode: "closed" })}
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
  onMembers,
  onDelete,
}: {
  project: Project;
  active: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onMembers: () => void;
  onDelete: () => void;
}) {
  const stats = useProjectStats(project.name);

  return (
    <div className={`project-card${active ? " active" : ""}`}>
      <div className="project-card-head">
        <div>
          <div className="project-card-title">{project.name}</div>
          {active && <span className="project-card-badge active">Active</span>}
        </div>
      </div>
      {stats && (
        <div className="project-card-stats">
          <span>
            {stats.conversations} {stats.conversations === 1 ? "conversation" : "conversations"}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            {stats.datasets} {stats.datasets === 1 ? "dataset" : "datasets"}
          </span>
          {stats.lastActive && (
            <>
              <span aria-hidden="true">·</span>
              <span>active {relativeTime(stats.lastActive)}</span>
            </>
          )}
        </div>
      )}
      <div className="project-card-desc">{project.description || "No description."}</div>

      <ChipRow label="Skills" items={project.skills} />
      <ChipRow label="Knowledge Bases" items={project.knowledgeBases} />
      <ChipRow label="Tools" items={project.tools} />

      <div className="project-card-actions">
        <button type="button" className="button" onClick={onOpen}>
          {active ? "Reopen" : "Open"}
        </button>
        <button type="button" className="button ghost button-sm" onClick={onEdit}>
          Edit
        </button>
        <button type="button" className="button ghost button-sm" onClick={onMembers}>
          Members
        </button>
        <button type="button" className="button ghost button-sm" onClick={onDelete}>
          Delete
        </button>
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

function MembersModal({
  project,
  onClose,
}: {
  project: Project;
  onClose: () => void;
}) {
  const [members, setMembers] = useState<UserPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [email, setEmail] = useState("");
  const [addError, setAddError] = useState("");
  const [adding, setAdding] = useState(false);

  async function loadMembers() {
    setLoading(true);
    setLoadError("");
    try {
      setMembers(await listProjectMembers(project.name));
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load members.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadMembers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.name]);

  async function addMember() {
    const trimmed = email.trim();
    if (!trimmed) {
      setAddError("Email is required.");
      return;
    }
    setAdding(true);
    setAddError("");
    try {
      await addProjectMember(project.name, trimmed);
      setEmail("");
      await loadMembers();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Failed to add member.");
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal project-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Members of ${project.name}`}
      >
        <div className="panel-header">
          <div className="panel-title">Members — {project.name}</div>
          <button type="button" className="button ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body">
          <div className="project-modal-label">
            <div>Add member</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="input"
                type="email"
                value={email}
                placeholder="user@example.com"
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void addMember();
                  }
                }}
                disabled={adding}
                autoFocus
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="button"
                onClick={() => void addMember()}
                disabled={adding}
              >
                {adding ? "Adding…" : "Add"}
              </button>
            </div>
          </div>

          {addError && (
            <div className="error" style={{ fontSize: 12 }}>
              {addError}
            </div>
          )}

          <div className="project-modal-label">
            <div>Current members</div>
            {loadError ? (
              <div className="error" style={{ fontSize: 12 }}>
                {loadError}{" "}
                <button
                  type="button"
                  className="button ghost button-xs"
                  onClick={() => void loadMembers()}
                >
                  Retry
                </button>
              </div>
            ) : loading ? (
              <div style={{ fontSize: 12, color: "var(--muted)" }}>Loading members…</div>
            ) : members.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--muted)" }}>No members yet.</div>
            ) : (
              <div className="project-modal-skill-list">
                {members.map((m) => (
                  <div key={m.id} className="project-modal-skill-item">
                    <div>
                      <div style={{ fontWeight: 600 }}>{m.email}</div>
                      {m.is_admin && (
                        <div style={{ fontSize: 11, color: "var(--muted)" }}>admin</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ProjectModal({
  mode,
  initial,
  skillCatalog,
  kbCatalog,
  existingNames,
  onCancel,
  onSave,
}: {
  mode: "create" | "edit";
  initial?: Project;
  skillCatalog: SkillSummary[];
  kbCatalog: KnowledgeBaseSummary[];
  existingNames: Set<string>;
  onCancel: () => void;
  onSave: (draft: Omit<Project, "id">) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.systemPrompt ?? "");
  const [skills, setSkills] = useState<Set<string>>(new Set(initial?.skills ?? []));
  const [kbs, setKbs] = useState<Set<string>>(new Set(initial?.knowledgeBases ?? []));
  const [forumRepoUrl, setForumRepoUrl] = useState(initial?.forumRepoUrl ?? "");
  const [toolsRaw, setToolsRaw] = useState((initial?.tools ?? []).join(", "));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function toggleSkill(slug: string) {
    setSkills((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function toggleKb(slug: string) {
    setKbs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  async function commit() {
    const trimmedName = name.trim();
    const trimmedDesc = description.trim();
    if (!trimmedName) {
      setError("Name is required.");
      return;
    }
    if (trimmedName.length < 2) {
      setError("Name must be at least 2 characters.");
      return;
    }
    // The name is used as the URL path segment for CRUD routes, so restrict it
    // to characters that round-trip cleanly without percent-encoding surprises.
    if (!/^[\w ._-]+$/.test(trimmedName)) {
      setError("Name may only contain letters, numbers, spaces, '.', '_', and '-'.");
      return;
    }
    if (mode === "create" && existingNames.has(trimmedName)) {
      setError(`A project named "${trimmedName}" already exists.`);
      return;
    }
    const splitTags = (raw: string) =>
      raw
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);

    setSaving(true);
    setError("");
    try {
      await onSave({
        name: trimmedName,
        description: trimmedDesc,
        systemPrompt: systemPrompt.trim(),
        skills: Array.from(skills),
        knowledgeBases: Array.from(kbs),
        forumRepoUrl: forumRepoUrl.trim(),
        tools: splitTags(toolsRaw),
        // PUT is a full overwrite on the backend — echo the existing usage
        // limits so they aren't reset on edit.
        usageLimits: initial?.usageLimits ?? {},
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save project.");
      setSaving(false);
    }
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
            Name
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              maxLength={80}
            />
          </label>

          <label className="project-modal-label">
            Description
            <textarea
              className="input"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={400}
            />
          </label>

          <label className="project-modal-label">
            System prompt
            <textarea
              className="input"
              rows={4}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Extra instructions appended to the base agent prompt."
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

          <div className="project-modal-label">
            <div>Knowledge Bases</div>
            <div className="project-modal-skill-list">
              {kbCatalog.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--muted)" }}>
                  No knowledge bases yet. Create one from the Knowledge Bases page.
                </div>
              ) : (
                kbCatalog.map((kb) => (
                  <label key={kb.slug} className="project-modal-skill-item">
                    <input
                      type="checkbox"
                      checked={kbs.has(kb.slug)}
                      onChange={() => toggleKb(kb.slug)}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>{kb.name}</div>
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>
                        {(kb.description ?? "").slice(0, 120)}
                        {(kb.description ?? "").length > 120 ? "..." : ""}
                      </div>
                    </div>
                  </label>
                ))
              )}
            </div>
          </div>

          <label className="project-modal-label">
            Hypothesis Lab{" "}
            <span style={{ color: "var(--muted)", fontWeight: 400 }}>
              (git repository the debates publish to; leave blank to turn it off)
            </span>
            <input
              className="input"
              value={forumRepoUrl}
              onChange={(e) => setForumRepoUrl(e.target.value)}
              placeholder="https://github.com/your-org/your-forum.git"
            />
            <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
              Everyone with push access to this repository can post to the
              project&rsquo;s debates, so it is the guest list for the room. The
              repository must already exist; saving checks it can be reached and
              pulls in any threads it already holds.
            </span>
          </label>

          <label className="project-modal-label">
            Tools{" "}
            <span style={{ color: "var(--muted)", fontWeight: 400 }}>
              (comma-separated fnmatch patterns; `!` prefix denies)
            </span>
            <input
              className="input"
              value={toolsRaw}
              onChange={(e) => setToolsRaw(e.target.value)}
              placeholder="e.g. *, !agenthpc_*"
            />
          </label>

          {error && (
            <div className="error" style={{ fontSize: 12 }}>
              {error}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <button type="button" className="button ghost" onClick={onCancel} disabled={saving}>
              Cancel
            </button>
            <button type="button" className="button" onClick={() => void commit()} disabled={saving}>
              {saving ? "Saving…" : mode === "create" ? "Create" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
