import { useSyncExternalStore } from "react";
import type { ModelMessage } from "./agent-events";
import type { ChatMessage, ExecutionResult } from "./types";

const ACTIVE_CHAT_SESSION_KEY_PREFIX = "vista.activeChatSession";
const activeChatSessionListeners = new Set<() => void>();

export type PersistedChatSessionSummary = {
  id: string;
  user_id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

/** One event of a run the researcher did not watch, as stored by the backend (compacted). */
export type StoredRunEvent = { event: string; data: Record<string, unknown> };

export type PersistedChatSession = PersistedChatSessionSummary & {
  /** Written only by the backend. The page reads it but never sends it back. */
  message_history: ModelMessage[];
  messages: ChatMessage[];
  latest_result: ExecutionResult | null;
  run_state: "idle" | "running" | "done" | "failed" | "interrupted" | "stopped";
  /** Live: `working` and `needs_you` come from the running turn, the rest from the saved outcome. */
  run_status: "idle" | "working" | "needs_you" | "done" | "failed" | "interrupted" | "stopped";
  run_unseen: boolean;
  /** The events of the last run, kept until the page has drawn them and acknowledged the run. */
  run_events: StoredRunEvent[] | null;
};

export class PersistedChatSessionError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function activeChatSessionStorageKey(projectName: string): string {
  return `${ACTIVE_CHAT_SESSION_KEY_PREFIX}.${projectName}.v1`;
}

export function readActiveChatSessionId(projectName: string | null | undefined): string | null {
  if (!projectName || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(activeChatSessionStorageKey(projectName));
    return typeof raw === "string" && raw.length > 0 ? raw : null;
  } catch {
    return null;
  }
}

export function writeActiveChatSessionId(
  projectName: string | null | undefined,
  chatSessionId: string | null,
): void {
  if (!projectName || typeof window === "undefined") return;
  try {
    const key = activeChatSessionStorageKey(projectName);
    if (chatSessionId) {
      window.localStorage.setItem(key, chatSessionId);
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // ignore quota / unavailable storage
  }
}

export function notifyActiveChatSessionChanged(): void {
  activeChatSessionListeners.forEach((cb) => cb());
}

function activeChatSessionSubscribe(cb: () => void): () => void {
  activeChatSessionListeners.add(cb);
  const onStorage = (e: StorageEvent) => {
    if (e.key?.startsWith(`${ACTIVE_CHAT_SESSION_KEY_PREFIX}.`)) cb();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    activeChatSessionListeners.delete(cb);
    window.removeEventListener("storage", onStorage);
  };
}

export function useActiveChatSessionId(projectName: string | null | undefined): string | null {
  return useSyncExternalStore(
    activeChatSessionSubscribe,
    () => readActiveChatSessionId(projectName),
    () => null,
  );
}

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown; error?: unknown }).detail ??
      (data as { error?: unknown }).error;
    if (typeof detail === "string") return detail;
    if (detail != null) return JSON.stringify(detail);
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

export async function listPersistedChatSessions(
  projectName: string
): Promise<PersistedChatSessionSummary[]> {
  const res = await fetch(
    `/api/chat/sessions?project_name=${encodeURIComponent(projectName)}`,
    {
      headers: { accept: "application/json" },
      cache: "no-store",
    }
  );
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as PersistedChatSessionSummary[];
}

export async function createPersistedChatSession(
  projectName: string,
  payload: { title?: string } = {}
): Promise<PersistedChatSession> {
  const res = await fetch("/api/chat/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      project_name: projectName,
      title: payload.title ?? null,
    }),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as PersistedChatSession;
}

export async function fetchPersistedChatSession(
  projectName: string,
  chatSessionId?: string | null,
): Promise<PersistedChatSession> {
  const qs = new URLSearchParams({ project_name: projectName });
  if (chatSessionId) qs.set("chat_session_id", chatSessionId);
  const res = await fetch(`/api/chat/session?${qs.toString()}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new PersistedChatSessionError(await extractError(res), res.status);
  }
  return (await res.json()) as PersistedChatSession;
}

export async function savePersistedChatSession(
  projectName: string,
  payload: {
    chatSessionId?: string | null;
    title?: string | null;
    messageHistory?: ModelMessage[] | null;
    messages?: ChatMessage[] | null;
    latestResult?: ExecutionResult | null;
  }
): Promise<PersistedChatSession> {
  const res = await fetch("/api/chat/session", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      project_name: projectName,
      chat_session_id: payload.chatSessionId ?? null,
      title: payload.title ?? null,
      message_history: payload.messageHistory ?? null,
      messages: payload.messages ?? null,
      latest_result: payload.latestResult ?? null,
    }),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as PersistedChatSession;
}

export async function renamePersistedChatSession(
  projectName: string,
  chatSessionId: string,
  title: string,
): Promise<PersistedChatSession> {
  return savePersistedChatSession(projectName, {
    chatSessionId,
    title,
  });
}

export async function deletePersistedChatSession(
  projectName: string,
  chatSessionId: string,
): Promise<void> {
  const qs = new URLSearchParams({
    project_name: projectName,
    chat_session_id: chatSessionId,
  });
  const res = await fetch(`/api/chat/session?${qs.toString()}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await extractError(res));
}
