"use client";

// Per-browser loaded-skill set, shared between the Skill Hub and chat pages
// via localStorage. Keeps the source-of-truth in one place so multiple
// components can subscribe with useSyncExternalStore.

import { useCallback, useSyncExternalStore } from "react";

const LOADED_SKILLS_KEY = "vista.loadedSkills.v1";

const listeners = new Set<() => void>();

function read(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(LOADED_SKILLS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is string => typeof x === "string");
  } catch {
    return [];
  }
}

function write(slugs: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LOADED_SKILLS_KEY, JSON.stringify(slugs));
  } catch {
    // localStorage may be unavailable or full; degrade silently.
  }
  listeners.forEach((cb) => cb());
}

// Snapshot is identity-stable across renders unless the value changes, so
// useSyncExternalStore doesn't loop. We cache the latest array reference.
let cached: string[] = [];
let cachedRaw: string | null = null;

function getSnapshot(): string[] {
  if (typeof window === "undefined") return cached;
  const raw = window.localStorage.getItem(LOADED_SKILLS_KEY);
  if (raw === cachedRaw) return cached;
  cachedRaw = raw;
  cached = read();
  return cached;
}

function getServerSnapshot(): string[] {
  return cached;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key === LOADED_SKILLS_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

export function useLoadedSkills() {
  const loadedSlugs = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggleSkill = useCallback((slug: string) => {
    const current = read();
    write(
      current.includes(slug)
        ? current.filter((s) => s !== slug)
        : [...current, slug],
    );
  }, []);

  return { loadedSlugs, toggleSkill };
}
