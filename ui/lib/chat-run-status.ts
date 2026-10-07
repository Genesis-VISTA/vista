"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Which conversations are working, need the researcher, or have an outcome they
 * have not seen, from `GET /api/chat/runs/status`.
 *
 * One store per project, shared by the conversation list and the nav rail, so
 * both always show the same answer. It fetches when the first component
 * subscribes, then every `STATUS_POLL_MS` while the document is visible, and
 * straight away when the tab becomes visible again. A failed fetch keeps the
 * last answer: a backend restarting should not make every dot vanish.
 *
 * It polls rather than holding a push channel open, because this is a tiny local
 * JSON answer and polling stops by itself when nobody is looking.
 */

export type ChatRunStatusKind =
  | "working"
  | "needs_you"
  | "done"
  | "failed"
  | "interrupted";

export type ChatRunStatusEntry = {
  chat_session_id: string;
  status: ChatRunStatusKind;
  /** The outcome has not been opened yet. Always false while working or needing the researcher. */
  unseen: boolean;
};

export const STATUS_POLL_MS = 3_000;

type Snapshot = {
  /** The last successful answer, or null before one arrives. */
  entries: ChatRunStatusEntry[] | null;
  /** The most recent attempt failed. */
  failing: boolean;
};

type Store = {
  snapshot: Snapshot;
  listeners: Set<() => void>;
  timer: ReturnType<typeof setInterval> | null;
  inFlight: boolean;
  /** Increments per request so a slower, older answer cannot overwrite a newer one. */
  request: number;
  onVisibility: () => void;
};

const stores = new Map<string, Store>();

function storeFor(project: string): Store {
  let store = stores.get(project);
  if (!store) {
    store = {
      snapshot: { entries: null, failing: false },
      listeners: new Set(),
      timer: null,
      inFlight: false,
      request: 0,
      onVisibility: () => {
        if (document.visibilityState === "visible") void load(project);
      },
    };
    stores.set(project, store);
  }
  return store;
}

function set(store: Store, snapshot: Snapshot): void {
  store.snapshot = snapshot;
  store.listeners.forEach((cb) => cb());
}

async function load(project: string): Promise<void> {
  const store = storeFor(project);
  const id = ++store.request;
  store.inFlight = true;
  try {
    const res = await fetch(
      `/api/chat/runs/status?project_name=${encodeURIComponent(project)}`,
      { headers: { accept: "application/json" }, cache: "no-store" },
    );
    if (!res.ok) throw new Error(`Chat run status request failed (${res.status})`);
    const entries = (await res.json()) as ChatRunStatusEntry[];
    if (id !== store.request) return;
    set(store, { entries, failing: false });
  } catch {
    if (id !== store.request) return;
    set(store, { entries: store.snapshot.entries, failing: true });
  } finally {
    if (id === store.request) store.inFlight = false;
  }
}

function tick(project: string): void {
  const store = storeFor(project);
  const visible = typeof document === "undefined" || document.visibilityState === "visible";
  if (visible && !store.inFlight) void load(project);
}

function subscribeTo(project: string, cb: () => void): () => void {
  const store = storeFor(project);
  store.listeners.add(cb);
  if (store.listeners.size === 1) {
    store.timer = setInterval(() => tick(project), STATUS_POLL_MS);
    document.addEventListener("visibilitychange", store.onVisibility);
    tick(project);
  }
  return () => {
    store.listeners.delete(cb);
    if (store.listeners.size === 0) {
      if (store.timer) clearInterval(store.timer);
      store.timer = null;
      document.removeEventListener("visibilitychange", store.onVisibility);
    }
  };
}

const EMPTY: Snapshot = { entries: null, failing: false };

/** Fetch now, e.g. right after a send, a stop, or opening a conversation. */
export function refreshChatRunStatus(project: string): Promise<void> {
  return load(project);
}

/** For tests: forget everything, as a fresh page load would. */
export function resetChatRunStatusForTests(): void {
  stores.forEach((store) => {
    store.request++;
    if (store.timer) clearInterval(store.timer);
    document.removeEventListener("visibilitychange", store.onVisibility);
  });
  stores.clear();
}

/** The statuses from most to least urgent. The nav rail's dot shows the first one present. */
const URGENCY: ChatRunStatusKind[] = [
  "needs_you",
  "failed",
  "interrupted",
  "done",
  "working",
];

export type ChatRunSummary = {
  /** The most urgent status across conversations, or null when none has one. */
  status: ChatRunStatusKind | null;
  /** Conversations that are waiting on the researcher. */
  needsYou: string[];
};

/** Reduce the per-conversation statuses to the one summary the nav rail shows. Pure. */
export function summarizeChatRuns(
  entries: readonly ChatRunStatusEntry[] | null,
): ChatRunSummary {
  const list = entries ?? [];
  const top = URGENCY.find((kind) => list.some((e) => e.status === kind)) ?? null;
  return {
    status: top,
    needsYou: list.filter((e) => e.status === "needs_you").map((e) => e.chat_session_id),
  };
}

export type ChatRunStatusView = {
  /** Null until the first answer. */
  entries: ChatRunStatusEntry[] | null;
  /** Status by conversation id; conversations with no entry are idle. */
  byId: ReadonlyMap<string, ChatRunStatusEntry>;
  summary: ChatRunSummary;
  /** The last request failed, so what is shown may be older than it looks. */
  failing: boolean;
  refresh: () => Promise<void>;
};

/** Live run status for a project's conversations. Pass null where there is no project yet. */
export function useChatRunStatus(projectName: string | null | undefined): ChatRunStatusView {
  const project = projectName ?? null;
  const subscribe = useCallback(
    (cb: () => void) => (project ? subscribeTo(project, cb) : () => {}),
    [project],
  );
  const getSnapshot = useCallback(
    () => (project ? storeFor(project).snapshot : EMPTY),
    [project],
  );
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY);
  const refresh = useCallback(
    () => (project ? load(project) : Promise.resolve()),
    [project],
  );
  return {
    entries: snapshot.entries,
    byId: new Map((snapshot.entries ?? []).map((e) => [e.chat_session_id, e])),
    summary: summarizeChatRuns(snapshot.entries),
    failing: snapshot.failing,
    refresh,
  };
}
