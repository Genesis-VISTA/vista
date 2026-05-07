/**
 * Project model — a Project is a saved bundle of context (skills, datasets,
 * knowledge bases) the user can activate to scope a chat session.
 *
 * Built-in seeds are defined in this module and treated as read-only.
 * User-created projects live in localStorage under PROJECTS_STORAGE_KEY.
 * The slug of the currently active project lives under ACTIVE_PROJECT_KEY.
 *
 * Activating a project writes its `skills` list to LOADED_SKILLS_STORAGE_KEY
 * (the same key the chat agent reads), so a project switch is just a write +
 * navigation; no extra wiring on the chat side.
 */

import { useSyncExternalStore } from "react";

export interface Project {
  slug: string;
  title: string;
  description: string;
  skills: string[];
  datasets: string[];
  knowledgeBases: string[];
  /** True for the seeded projects defined in this file. */
  builtin?: boolean;
}

export const PROJECTS_STORAGE_KEY = "vista.projects.v1";
export const ACTIVE_PROJECT_KEY = "vista.activeProject.v1";
export const LOADED_SKILLS_STORAGE_KEY = "vista.loadedSkills.v1";

export const BUILTIN_PROJECTS: Project[] = [
  {
    slug: "genesis-splash",
    title: "Genesis Splash",
    description:
      "Molten salt tritium breeding agent: thermophysical analysis, phase diagrams, GP property prediction, FORGE fine-tuning on Frontier, and dataset documentation.",
    skills: ["salt-analysis", "salt-prediction", "model-fine-tuning", "datacard-generation"],
    datasets: ["MSTDB"],
    knowledgeBases: ["Molten Salt Papers"],
    builtin: true,
  },
  {
    slug: "high-entropy-alloy-design",
    title: "High Entropy Alloy Design",
    description:
      "Agentic composition search for refractory high-entropy alloys (MoNbTaW) on the Andes Slurm cluster.",
    skills: ["alloy-design"],
    datasets: [],
    knowledgeBases: [],
    builtin: true,
  },
];

export function readUserProjects(): Project[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PROJECTS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidProject);
  } catch {
    return [];
  }
}

export function writeUserProjects(projects: Project[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      PROJECTS_STORAGE_KEY,
      JSON.stringify(projects.filter((p) => !p.builtin))
    );
    notifyUserProjectsChanged();
  } catch {
    // ignore quota / unavailable storage
  }
}

export function readActiveProjectSlug(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ACTIVE_PROJECT_KEY);
    return typeof raw === "string" && raw.length > 0 ? raw : null;
  } catch {
    return null;
  }
}

export function writeActiveProjectSlug(slug: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (slug) {
      window.localStorage.setItem(ACTIVE_PROJECT_KEY, slug);
    } else {
      window.localStorage.removeItem(ACTIVE_PROJECT_KEY);
    }
  } catch {
    // ignore
  }
}

export function getAllProjects(): Project[] {
  return [...BUILTIN_PROJECTS, ...readUserProjects()];
}

export function findProject(slug: string): Project | undefined {
  return getAllProjects().find((p) => p.slug === slug);
}

/**
 * Activate a project: replace loaded skills with this project's skill list
 * (a project is the user's context, so other skills get unloaded), and set
 * the active-project pointer. Does not navigate — call `router.push("/")`
 * after this.
 */
export function activateProject(project: Project): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      LOADED_SKILLS_STORAGE_KEY,
      JSON.stringify(project.skills)
    );
    writeActiveProjectSlug(project.slug);
    notifyActiveProjectChanged();
  } catch {
    // ignore
  }
}

/** Generate a slug from a title for new user-created projects. */
export function slugify(title: string): string {
  const base = title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || `project-${Date.now()}`;
}

function isValidProject(value: unknown): value is Project {
  if (!value || typeof value !== "object") return false;
  const p = value as Record<string, unknown>;
  return (
    typeof p.slug === "string" &&
    typeof p.title === "string" &&
    typeof p.description === "string" &&
    Array.isArray(p.skills) &&
    Array.isArray(p.datasets) &&
    Array.isArray(p.knowledgeBases)
  );
}

/* ------------------------------------------------------------------ */
/*  Active-project hook (SSR-safe)                                     */
/* ------------------------------------------------------------------ */

/**
 * Tiny external store + hook for reading the active project on the client
 * without triggering a hydration mismatch.
 *
 * Returns `null` on the server and during the first client render, then
 * re-renders with the real value after hydration. Subscribes to the
 * `storage` event so other tabs / pages writing the active-project slug
 * are picked up live.
 */
const activeProjectListeners = new Set<() => void>();

function activeProjectSubscribe(cb: () => void): () => void {
  activeProjectListeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === ACTIVE_PROJECT_KEY || e.key === PROJECTS_STORAGE_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    activeProjectListeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

function activeProjectGetSnapshot(): Project | null {
  const slug = readActiveProjectSlug();
  return slug ? findProject(slug) ?? null : null;
}

function activeProjectGetServerSnapshot(): Project | null {
  return null;
}

export function notifyActiveProjectChanged(): void {
  activeProjectListeners.forEach((cb) => cb());
}

export function useActiveProject(): Project | null {
  return useSyncExternalStore(
    activeProjectSubscribe,
    activeProjectGetSnapshot,
    activeProjectGetServerSnapshot
  );
}

/* ------------------------------------------------------------------ */
/*  User-projects hook (SSR-safe)                                      */
/* ------------------------------------------------------------------ */

const userProjectsListeners = new Set<() => void>();

function userProjectsSubscribe(cb: () => void): () => void {
  userProjectsListeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === PROJECTS_STORAGE_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    userProjectsListeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

const EMPTY_PROJECTS: Project[] = [];

// Cache the parsed snapshot so successive useSyncExternalStore reads return
// the same reference until localStorage actually changes — this keeps React
// from looping ("getSnapshot should be cached") when the same shape is read
// twice within a render.
let cachedUserProjectsRaw: string | null = null;
let cachedUserProjects: Project[] = EMPTY_PROJECTS;

function userProjectsGetSnapshot(): Project[] {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(PROJECTS_STORAGE_KEY);
  } catch {
    return EMPTY_PROJECTS;
  }
  if (raw === cachedUserProjectsRaw) return cachedUserProjects;
  cachedUserProjectsRaw = raw;
  cachedUserProjects = readUserProjects();
  return cachedUserProjects;
}

function userProjectsGetServerSnapshot(): Project[] {
  return EMPTY_PROJECTS;
}

/**
 * Fire when the user-project list changes via the current tab so listeners
 * pick up the change without waiting for a `storage` event (which only fires
 * for *other* tabs).
 */
export function notifyUserProjectsChanged(): void {
  cachedUserProjectsRaw = null; // invalidate cache so next snapshot re-reads
  userProjectsListeners.forEach((cb) => cb());
}

export function useUserProjects(): Project[] {
  return useSyncExternalStore(
    userProjectsSubscribe,
    userProjectsGetSnapshot,
    userProjectsGetServerSnapshot
  );
}
