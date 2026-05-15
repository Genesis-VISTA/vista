"use client";

// Client-side active-project state: project name stored in localStorage.
// The chat endpoint is keyed by name (/projects/{project_name}/agent/run/vercel),
// so we use name as the identifier everywhere in the UI too.
//
// State is held in a module-level store so that every component that calls
// `useActiveProject` sees updates immediately — including those in the
// persistent layout (NavRail, TopBar) when a route component (e.g. the
// Projects page) switches the active project. The `storage` event only
// fires for cross-tab changes, so we maintain our own subscriber list.

import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { Project } from "./types";
import { getProject } from "@/app/actions/projects";

const ACTIVE_PROJECT_KEY = "vista.activeProjectName.v1";

let currentProject: Project | null = null;
const listeners = new Set<() => void>();

function notify() {
  for (const cb of listeners) cb();
}

function readName(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(ACTIVE_PROJECT_KEY);
    return v && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

function writeName(name: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (name) window.localStorage.setItem(ACTIVE_PROJECT_KEY, name);
    else window.localStorage.removeItem(ACTIVE_PROJECT_KEY);
  } catch {
    // ignore quota / unavailable storage
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): Project | null {
  return currentProject;
}

function getServerSnapshot(): Project | null {
  return null;
}

let hydratedFor: string | null | undefined = undefined;

async function hydrateFromStorage() {
  const name = readName();
  if (hydratedFor === name) return;
  hydratedFor = name;
  if (!name) {
    if (currentProject !== null) {
      currentProject = null;
      notify();
    }
    return;
  }
  try {
    const project = await getProject(name);
    // Only apply if the user hasn't changed the active project mid-flight.
    if (readName() === name) {
      currentProject = project;
      notify();
    }
  } catch {
    if (readName() === name) {
      currentProject = null;
      notify();
    }
  }
}

export function useActiveProject() {
  const activeProject = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setActiveProject = useCallback((project: Project | null) => {
    writeName(project?.name ?? null);
    hydratedFor = project?.name ?? null;
    currentProject = project;
    notify();
  }, []);

  useEffect(() => {
    void hydrateFromStorage();
    function onStorage(e: StorageEvent) {
      if (e.key === ACTIVE_PROJECT_KEY) {
        hydratedFor = undefined;
        void hydrateFromStorage();
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return { activeProject, setActiveProject };
}
