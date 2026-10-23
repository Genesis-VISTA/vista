"use client";

import { useSyncExternalStore } from "react";

/**
 * Whether publishing and importing can work right now, from
 * `GET /api/collaboration/status`, and the one hook every Publish and Import
 * control reads.
 *
 * One store for the page, the same pattern as `lib/agent-settings.ts`: the
 * Settings section and every control show the same answer, and saving the
 * server address calls `refreshCollaborationStatus()` so they all follow. A
 * control is disabled whenever `available` is false, and shows `reason`.
 */

/** Backend `CollaborationStatus`. */
export type CollaborationStatus = {
  /** A server address is set, in Settings or the installation's configuration. */
  configured: boolean;
  /** It answered, and speaks the API this VISTA does. */
  reachable: boolean;
  /** Why publishing and importing are off; null when they are on. */
  reason: string | null;
  /** The address in effect. */
  url: string | null;
  /** The researcher's own address, or the installation's. */
  source: "settings" | "config" | null;
  server_version: string | null;
};

type Snapshot = {
  /** The last answer; null until the first. Kept when a later request fails. */
  status: CollaborationStatus | null;
  /** Why the last request failed, if it did; beside the last answer, not instead of it. */
  error: string | null;
  checking: boolean;
  /** Client clock, ms, of the last answer. */
  checkedAt: number | null;
};

/** A new subscriber re-asks once the answer is older than this. */
export const STALE_MS = 30_000;

const initial = (): Snapshot => ({ status: null, error: null, checking: false, checkedAt: null });

let snapshot: Snapshot = initial();
let request = 0;
const listeners = new Set<() => void>();

function set(update: Partial<Snapshot>): void {
  snapshot = { ...snapshot, ...update };
  listeners.forEach((cb) => cb());
}

/**
 * Ask again. Every subscriber follows. `forget` drops the last answer first:
 * after the address changes, an answer about the old address says nothing
 * about the new one, so controls go back to "Checking…" until it arrives.
 */
export async function refreshCollaborationStatus(
  { forget = false }: { forget?: boolean } = {},
): Promise<void> {
  const id = ++request;
  set(forget ? { checking: true, status: null, error: null } : { checking: true });
  try {
    const res = await fetch("/api/collaboration/status", {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`request failed (${res.status})`);
    const status = (await res.json()) as CollaborationStatus;
    // A slower, older request must not overwrite a newer answer.
    if (id !== request) return;
    set({ status, error: null, checking: false, checkedAt: Date.now() });
  } catch (e) {
    if (id !== request) return;
    // One failed request (a backend restarting) does not throw away the last
    // answer about the same address: the controls keep it, and Settings shows
    // the error beside it.
    set({
      error: e instanceof Error ? e.message : "request failed",
      checking: false,
      checkedAt: Date.now(),
    });
  }
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const stale = snapshot.checkedAt === null || Date.now() - snapshot.checkedAt >= STALE_MS;
  if (!snapshot.checking && stale) void refreshCollaborationStatus();
  return () => {
    listeners.delete(cb);
  };
}

const getSnapshot = () => snapshot;
const SERVER_SNAPSHOT: Snapshot = initial();
const getServerSnapshot = () => SERVER_SNAPSHOT;

/** For tests: forget everything, as a fresh page load would. */
export function resetCollaborationForTests(): void {
  request++;
  snapshot = initial();
}

/**
 * Whether Publish and Import are enabled, and if not, the sentence to show.
 * Pure, for testing. Off until the first answer: a control enabled while
 * checking would fail for a reason the researcher was not shown. After that,
 * the last answer stands even if a later check could not be made.
 */
export function availability(s: Pick<Snapshot, "status" | "error">): {
  available: boolean;
  reason: string | null;
} {
  if (s.status?.reachable) return { available: true, reason: null };
  if (s.status) {
    return { available: false, reason: s.status.reason ?? "The collaboration server is unavailable." };
  }
  if (s.error) return { available: false, reason: `Couldn't check the collaboration server: ${s.error}.` };
  return { available: false, reason: "Checking the collaboration server…" };
}

export type CollaborationView = {
  status: CollaborationStatus | null;
  checking: boolean;
  /** Publish and Import can be used. */
  available: boolean;
  /** Why they cannot, for the disabled control; null when they can. */
  reason: string | null;
  /** Why the last check could not be made, beside the last answer it kept. */
  error: string | null;
  refresh: () => Promise<void>;
};

export function useCollaboration(): CollaborationView {
  const s = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return {
    status: s.status,
    checking: s.checking,
    error: s.error,
    ...availability(s),
    refresh: () => refreshCollaborationStatus(),
  };
}
