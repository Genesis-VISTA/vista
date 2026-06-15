import type { ModelMessage } from "./agent-events";
import type { ChatMessage, ExecutionResult } from "./types";

export type PersistedChatSession = {
  id: string;
  user_id: string;
  project_id: string;
  created_at: string;
  updated_at: string;
  message_history: ModelMessage[];
  messages: ChatMessage[];
  latest_result: ExecutionResult | null;
};

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

export async function fetchPersistedChatSession(projectName: string): Promise<PersistedChatSession> {
  const res = await fetch(
    `/api/chat/session?project_name=${encodeURIComponent(projectName)}`,
    {
      headers: { accept: "application/json" },
      cache: "no-store",
    }
  );
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as PersistedChatSession;
}

export async function savePersistedChatSession(
  projectName: string,
  payload: { messageHistory: ModelMessage[]; messages: ChatMessage[]; latestResult: ExecutionResult | null }
): Promise<PersistedChatSession> {
  const res = await fetch("/api/chat/session", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      project_name: projectName,
      message_history: payload.messageHistory,
      messages: payload.messages,
      latest_result: payload.latestResult,
    }),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as PersistedChatSession;
}
