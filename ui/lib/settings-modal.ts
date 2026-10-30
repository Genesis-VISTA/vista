"use client";

import { useSyncExternalStore } from "react";
import type { SettingsSection } from "@/components/UserSettingsModal";

/**
 * Whether the Settings modal is open, and at which section.
 *
 * One store for the whole page, the same pattern as `requestModelChoice` in
 * `lib/agent-settings.ts`, so the nav rail's button, a cluster card and the
 * onboarding tour all open the one modal the rail renders.
 */

export type SettingsModalState = {
  open: boolean;
  /** The section to show on open; undefined is Research profile. */
  section: SettingsSection | undefined;
};

const CLOSED: SettingsModalState = { open: false, section: undefined };

let snapshot: SettingsModalState = CLOSED;
const listeners = new Set<() => void>();

function set(next: SettingsModalState): void {
  snapshot = next;
  listeners.forEach((cb) => cb());
}

/** Open Settings, at a section when one is named and at Research profile otherwise. */
export function openSettings(section?: SettingsSection): void {
  set({ open: true, section });
}

export function closeSettings(): void {
  set(CLOSED);
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

const getSnapshot = () => snapshot;
const getServerSnapshot = () => CLOSED;

export function useSettingsModal(): SettingsModalState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
