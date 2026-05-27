/**
 * Project model — a Project is a saved bundle of agent context (system prompt,
 * skills, knowledge bases, tools, usage limits) that scopes a chat session.
 *
 * Projects live in the backend DB and are reached through the `/api/projects`
 * proxy routes; this module is the frontend's data-access + React-hook layer
 * over that API. The only thing kept in localStorage is a pointer to the
 * currently active project's `name`.
 *
 * The set of knowledge bases in scope for a chat session comes from the
 * project's own `knowledgeBases` field — there is no per-session override.
 */

import { useEffect, useReducer, useSyncExternalStore } from "react";
import type { ProjectCreate, ProjectPublic, UserPublic } from "./agent-events";

export type { ProjectCreate, ProjectPublic, UserPublic };

/** The frontend-facing project shape, reconciled from backend `ProjectPublic`. */
export interface Project {
  /** Backend uuid — stable internal identity (not used as the CRUD key). */
  id: string;
  /** Backend `name` — the CRUD key (`{project_name}` on all project routes),
   *  the display label, and the active-project pointer value. Must be unique. */
  name: string;
  description: string;
  systemPrompt: string;
  skills: string[];
  knowledgeBases: string[];
  tools: string[];
  usageLimits: Record<string, unknown>;
}

export const ACTIVE_PROJECT_KEY = "vista.activeProject.v1";

function fromPublic(p: ProjectPublic): Project {
  return {
    id: p.id,
    name: p.name,
    description: p.description ?? "",
    systemPrompt: p.system_prompt ?? "",
    skills: Array.isArray(p.skills) ? p.skills : [],
    knowledgeBases: Array.isArray(p.knowledge_bases) ? p.knowledge_bases : [],
    tools: Array.isArray(p.tools) ? p.tools : [],
    usageLimits: p.usage_limits ?? {},
  };
}

/** Build a `ProjectCreate` body from a frontend `Project` (full overwrite). */
export function toCreate(project: Omit<Project, "id">): ProjectCreate {
  return {
    name: project.name,
    description: project.description || null,
    system_prompt: project.systemPrompt || null,
    skills: project.skills,
    knowledge_bases: project.knowledgeBases,
    tools: project.tools,
    usage_limits: project.usageLimits ?? {},
  };
}

/* ------------------------------------------------------------------ */
/*  Active-project pointer (localStorage, stores the project NAME)     */
/* ------------------------------------------------------------------ */

export function readActiveProjectName(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ACTIVE_PROJECT_KEY);
    return typeof raw === "string" && raw.length > 0 ? raw : null;
  } catch {
    return null;
  }
}

export function writeActiveProjectName(name: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (name) {
      window.localStorage.setItem(ACTIVE_PROJECT_KEY, name);
    } else {
      window.localStorage.removeItem(ACTIVE_PROJECT_KEY);
    }
  } catch {
    // ignore quota / unavailable storage
  }
}

const activeNameListeners = new Set<() => void>();

export function notifyActiveProjectChanged(): void {
  activeNameListeners.forEach((cb) => cb());
}

function activeNameSubscribe(cb: () => void): () => void {
  activeNameListeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === ACTIVE_PROJECT_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    activeNameListeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

/* ------------------------------------------------------------------ */
/*  Project list — fetched from the backend, cached module-level       */
/* ------------------------------------------------------------------ */

let projectsCache: Project[] = [];
let projectsLoaded = false;
let projectsError: string | null = null;
let inFlight: Promise<Project[]> | null = null;
const projectsListeners = new Set<() => void>();

function notifyProjects(): void {
  projectsListeners.forEach((cb) => cb());
}

/**
 * Fetch the project list from the backend, update the module cache, and notify
 * subscribers. Concurrent calls share a single in-flight request.
 */
export function refreshProjects(): Promise<Project[]> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const res = await fetch("/api/projects", {
        headers: { accept: "application/json" },
      });
      if (!res.ok) throw new Error(`Failed to load projects (${res.status})`);
      const data = (await res.json()) as ProjectPublic[];
      projectsCache = Array.isArray(data) ? data.map(fromPublic) : [];
      projectsError = null;
    } catch (e) {
      projectsError = e instanceof Error ? e.message : "Failed to load projects";
    } finally {
      projectsLoaded = true;
      inFlight = null;
      notifyProjects();
    }
    return projectsCache;
  })();
  return inFlight;
}

export interface UseProjectsResult {
  projects: Project[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<Project[]>;
}

/**
 * Subscribe to the backend project list. Triggers an initial fetch on mount and
 * re-renders whenever the cache changes (including after create/edit/delete).
 */
export function useProjects(): UseProjectsResult {
  const [, forceRender] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    projectsListeners.add(forceRender);
    if (!projectsLoaded && !inFlight) void refreshProjects();
    return () => {
      projectsListeners.delete(forceRender);
    };
  }, []);
  return {
    projects: projectsCache,
    loading: !projectsLoaded,
    error: projectsError,
    refresh: refreshProjects,
  };
}

/**
 * The active project, resolved by name against the fetched list. Returns `null`
 * on the server, before the list loads, or when no project is active.
 */
export function useActiveProject(): Project | null {
  const { projects } = useProjects();
  const activeName = useSyncExternalStore(
    activeNameSubscribe,
    readActiveProjectName,
    () => null
  );
  if (!activeName) return null;
  return projects.find((p) => p.name === activeName) ?? null;
}

/**
 * Activate a project: point the active-project pointer at its `name`. The
 * backend project already owns its own skills/tools, so there is nothing else
 * to wire. Does not navigate.
 */
export function activateProject(project: Project): void {
  writeActiveProjectName(project.name);
  notifyActiveProjectChanged();
}

/* ------------------------------------------------------------------ */
/*  CRUD helpers (talk to the /api/projects proxy routes)              */
/* ------------------------------------------------------------------ */

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown; error?: unknown }).detail ??
      (data as { error?: unknown }).error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) =>
          typeof d === "object" && d && "msg" in d
            ? String((d as { msg: unknown }).msg)
            : JSON.stringify(d)
        )
        .join("; ");
    }
    if (detail != null) return JSON.stringify(detail);
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

export async function createProject(input: ProjectCreate): Promise<Project> {
  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await extractError(res));
  const created = (await res.json()) as ProjectPublic;
  await refreshProjects();
  return fromPublic(created);
}

export async function updateProject(
  name: string,
  input: ProjectCreate
): Promise<Project> {
  const res = await fetch(`/api/projects/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await extractError(res));
  const updated = (await res.json()) as ProjectPublic;
  await refreshProjects();
  return fromPublic(updated);
}

export async function deleteProject(name: string): Promise<void> {
  const res = await fetch(`/api/projects/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error(await extractError(res));
  await refreshProjects();
}

/* ------------------------------------------------------------------ */
/*  Membership helpers                                                 */
/* ------------------------------------------------------------------ */

export async function listProjectMembers(name: string): Promise<UserPublic[]> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(name)}/members`,
    { headers: { accept: "application/json" } }
  );
  if (!res.ok) throw new Error(await extractError(res));
  const data = (await res.json()) as UserPublic[];
  return Array.isArray(data) ? data : [];
}

export async function addProjectMember(name: string, email: string): Promise<void> {
  const res = await fetch(`/api/projects/${encodeURIComponent(name)}/members`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok && res.status !== 201) throw new Error(await extractError(res));
}
