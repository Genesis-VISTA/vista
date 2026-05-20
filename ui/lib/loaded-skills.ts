/**
 * Per-project loaded-skills helpers.
 *
 * A project's mandated skills (from `Project.skills`) are always considered
 * loaded for that project — they appear on the /skills page and show as
 * `Loaded ✓` in the Skill Hub. On top of that, users can opt-in to additional
 * skills via the hub; those extras are stored per-project in localStorage so
 * switching projects swaps the additions in/out.
 *
 * Storage shape (key `vista.loadedSkills.v2`):
 *   {
 *     "<project name>": ["extra-skill-1", "extra-skill-2", ...],
 *     ...
 *   }
 *
 * The previous v1 schema was a single shared array; we don't migrate from it
 * because the v1 list mixed up project context and is the source of the bug
 * this module fixes.
 */

import type { Project } from "@/lib/projects";

export const LOADED_SKILLS_STORAGE_KEY = "vista.loadedSkills.v2";

type AdditionsMap = Record<string, string[]>;

function readMap(): AdditionsMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(LOADED_SKILLS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const out: AdditionsMap = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof k === "string" && Array.isArray(v)) {
          out[k] = v.filter((s): s is string => typeof s === "string");
        }
      }
      return out;
    }
  } catch {
    // ignore corrupt entries
  }
  return {};
}

function writeMap(map: AdditionsMap): void {
  try {
    window.localStorage.setItem(LOADED_SKILLS_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // localStorage may be unavailable (private mode, quota); callers proceed
    // with in-memory state and recover on next mount.
  }
}

/** Read just the additions set for one project. */
export function readAdditions(projectName: string | null | undefined): Set<string> {
  if (!projectName) return new Set();
  const map = readMap();
  return new Set(map[projectName] ?? []);
}

/** Overwrite the additions set for one project. */
export function writeAdditions(
  projectName: string | null | undefined,
  additions: Set<string>
): void {
  if (!projectName) return;
  const map = readMap();
  if (additions.size === 0) {
    delete map[projectName];
  } else {
    map[projectName] = Array.from(additions);
  }
  writeMap(map);
}

/**
 * Effective loaded set for a project: mandated `project.skills` ∪ additions.
 * Returns an empty set if `project` is null (e.g. before hydration).
 */
export function computeLoaded(
  project: Project | null | undefined,
  additions: Set<string>
): Set<string> {
  const out = new Set<string>();
  for (const slug of project?.skills ?? []) out.add(slug);
  for (const slug of additions) out.add(slug);
  return out;
}

/** True if a slug is part of the project's mandated set (cannot be unloaded). */
export function isMandated(
  project: Project | null | undefined,
  slug: string
): boolean {
  return (project?.skills ?? []).includes(slug);
}
