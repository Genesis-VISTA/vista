"use client";

// Projects management page — create / edit / delete projects.

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  getProjects,
  createProject,
  updateProject,
  deleteProject,
} from "@/app/actions/projects";
import { getSkills } from "@/app/actions/skills";
import { useActiveProject } from "@/lib/projects";
import type { Project, ProjectCreate, SkillSummary } from "@/lib/types";

type ModalState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; project: Project };

const emptyDraft: ProjectCreate = {
  name: "",
  description: null,
  system_prompt: null,
  skills: [],
  tools: [],
  usage_limits: {},
};

export default function ProjectsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-gray-500">Loading…</div>}>
      <ProjectsPageContent />
    </Suspense>
  );
}

function ProjectsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { activeProject, setActiveProject } = useActiveProject();

  const [projects, setProjects] = useState<Project[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>(
    searchParams?.get("new") === "1" ? { mode: "create" } : { mode: "closed" },
  );

  async function refresh() {
    try {
      setProjects(await getProjects());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load projects");
    }
  }

  useEffect(() => {
    getProjects()
      .then((data) => { setProjects(data); setError(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load projects"));
    getSkills().then(setSkills).catch(() => setSkills([]));
  }, []);

  useEffect(() => {
    if (searchParams?.get("new") === "1") router.replace("/projects");
  }, [searchParams, router]);

  function openProject(p: Project) {
    setActiveProject(p);
    router.push("/");
  }

  async function removeProject(p: Project) {
    if (!window.confirm(`Delete project "${p.name}"? This cannot be undone.`)) return;
    try {
      await deleteProject(p.name);
      if (activeProject?.name === p.name) setActiveProject(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  }

  async function saveDraft(draft: ProjectCreate, editing?: Project) {
    try {
      if (editing) await updateProject(editing.name, draft);
      else await createProject(draft);
      setModal({ mode: "closed" });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    }
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-semibold">Projects</h1>
          <p className="text-sm text-gray-600">
            Pick a project to scope the chat session with its system prompt, skills, and tools.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModal({ mode: "create" })}
          className="rounded-md bg-blue-600 text-white px-3 py-1.5 text-sm hover:bg-blue-700"
        >
          New project
        </button>
      </div>

      {error && <div className="mb-4 rounded bg-rose-50 text-rose-700 p-3 text-sm">{error}</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {projects.map((p) => {
          const isActive = activeProject?.name === p.name;
          return (
            <div
              key={p.id}
              className={`border rounded-md p-3 ${isActive ? "border-blue-500 bg-blue-50/40" : "border-gray-200"}`}
            >
              <div className="flex items-center gap-2">
                {isActive && <span className="w-2 h-2 rounded-full bg-blue-500" />}
                <div className="font-medium">{p.name}</div>
              </div>
              {p.description && (
                <div className="text-xs text-gray-600 mt-1">{p.description}</div>
              )}
              <div className="text-xs text-gray-500 mt-2">
                {p.skills.length} skills · {p.tools.length} tool patterns
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  className="text-xs rounded border border-gray-300 px-2 py-1 hover:bg-gray-50"
                  onClick={() => openProject(p)}
                >
                  Open
                </button>
                <button
                  type="button"
                  className="text-xs rounded border border-gray-300 px-2 py-1 hover:bg-gray-50"
                  onClick={() => setModal({ mode: "edit", project: p })}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="text-xs rounded border border-rose-300 text-rose-700 px-2 py-1 hover:bg-rose-50 ml-auto"
                  onClick={() => removeProject(p)}
                >
                  Delete
                </button>
              </div>
            </div>
          );
        })}
        {projects.length === 0 && (
          <div className="text-gray-500 text-sm">No projects yet — create one above.</div>
        )}
      </div>

      {modal.mode !== "closed" && (
        <ProjectEditor
          skills={skills}
          initial={modal.mode === "edit" ? modal.project : null}
          onCancel={() => setModal({ mode: "closed" })}
          onSave={(draft) => saveDraft(draft, modal.mode === "edit" ? modal.project : undefined)}
        />
      )}
    </div>
  );
}

interface EditorProps {
  initial: Project | null;
  skills: SkillSummary[];
  onCancel: () => void;
  onSave: (draft: ProjectCreate) => void;
}

const NAME_PATTERN = /^[A-Za-z0-9_ .\-]+$/;

function isNameValid(name: string) {
  return name.length >= 2 && name.length <= 80 && NAME_PATTERN.test(name);
}

function ProjectEditor({ initial, skills, onCancel, onSave }: EditorProps) {
  const [draft, setDraft] = useState<ProjectCreate>(
    initial
      ? {
          name: initial.name,
          description: initial.description,
          system_prompt: initial.system_prompt,
          skills: initial.skills,
          tools: initial.tools,
          usage_limits: initial.usage_limits,
        }
      : emptyDraft,
  );

  function toggleSkill(slug: string) {
    setDraft((d) => ({
      ...d,
      skills: d.skills.includes(slug)
        ? d.skills.filter((s) => s !== slug)
        : [...d.skills, slug],
    }));
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40"
      onClick={onCancel}
    >
      <div
        className="bg-white rounded-md p-5 max-w-2xl w-full max-h-[90vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-3">
          {initial ? `Edit ${initial.name}` : "New project"}
        </h2>

        <label className="block text-sm font-medium mt-2">Name</label>
        <input
          className="w-full border rounded px-2 py-1 text-sm"
          value={draft.name}
          maxLength={80}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        {draft.name && !isNameValid(draft.name) && (
          <p className="text-xs text-rose-600 mt-0.5">
            2–80 characters; letters, digits, spaces, underscores, periods, hyphens only.
          </p>
        )}

        <label className="block text-sm font-medium mt-3">Description</label>
        <input
          className="w-full border rounded px-2 py-1 text-sm"
          value={draft.description ?? ""}
          onChange={(e) => setDraft({ ...draft, description: e.target.value || null })}
        />

        <label className="block text-sm font-medium mt-3">System prompt</label>
        <textarea
          rows={4}
          className="w-full border rounded px-2 py-1 text-sm font-mono"
          value={draft.system_prompt ?? ""}
          onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value || null })}
        />

        <label className="block text-sm font-medium mt-3">
          Tools (fnmatch patterns; prefix with ! to deny). One per line.
        </label>
        <textarea
          rows={3}
          className="w-full border rounded px-2 py-1 text-sm font-mono"
          value={draft.tools.join("\n")}
          onChange={(e) =>
            setDraft({
              ...draft,
              tools: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
            })
          }
        />

        <label className="block text-sm font-medium mt-3">Skills</label>
        <div className="max-h-40 overflow-auto border rounded p-2 text-sm">
          {skills.length === 0 && <div className="text-gray-500">No skills available.</div>}
          {skills.map((s) => (
            <label key={s.name} className="flex items-center gap-2 py-0.5">
              <input
                type="checkbox"
                checked={draft.skills.includes(s.name)}
                onChange={() => toggleSkill(s.name)}
              />
              <span className="font-medium">{s.name}</span>
              <span className="text-gray-500">{s.description}</span>
            </label>
          ))}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="px-3 py-1.5 text-sm rounded border border-gray-300 hover:bg-gray-50"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!isNameValid(draft.name)}
            className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-300"
            onClick={() => onSave(draft)}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
