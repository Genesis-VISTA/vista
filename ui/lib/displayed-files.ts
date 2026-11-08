import type { FileKind } from "./files-tree";

/** One file `display_file` showed in a conversation, as the backend lists it. */
export type DisplayedFile = {
  kind: FileKind;
  /** Path inside the kind. */
  path: string;
  name: string;
  mime_type: string;
  run_id: string;
  tool_call_id: string;
  shown_at: string;
  /** Whether the file on disk is still the one shown. */
  status: "current" | "changed" | "missing";
};

/** The conversation's displayed files, newest first. */
export async function fetchDisplayedFiles(
  projectName: string,
  chatSessionId: string,
  signal?: AbortSignal,
): Promise<DisplayedFile[]> {
  const qs = new URLSearchParams({ project_name: projectName, chat_session_id: chatSessionId });
  const res = await fetch(`/api/chat/displayed-files?${qs.toString()}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  const body: unknown = await res.json();
  return Array.isArray(body) ? (body as DisplayedFile[]) : [];
}

const FILE_URL = /^\/api\/files\/(outputs|uploads)\/([^?#]+)/;

/**
 * A display that has just happened in a running turn, before the backend's record
 * of it is read back. Built from the URL `display_file` returned, which is the
 * files API's own. Null when the URL is not one of those.
 */
export function provisionalDisplayedFile(
  file: { url: string; mimeType?: string; name?: string },
  runId: string,
  toolCallId: string,
): DisplayedFile | null {
  const match = FILE_URL.exec(file.url);
  if (!match) return null;
  let path: string;
  try {
    path = match[2].split("/").map(decodeURIComponent).join("/");
  } catch {
    return null;
  }
  return {
    kind: match[1] as FileKind,
    path,
    name: file.name ?? path.split("/").pop() ?? path,
    mime_type: file.mimeType ?? "application/octet-stream",
    run_id: runId,
    tool_call_id: toolCallId,
    shown_at: new Date().toISOString(),
    status: "current",
  };
}

/**
 * What the feed shows: provisional items (newest first) that the record does not
 * have yet, then the recorded ones. A re-attached run replays displays that are
 * already recorded, so those are not listed twice.
 */
export function mergeFeed(provisional: DisplayedFile[], recorded: DisplayedFile[]): DisplayedFile[] {
  const key = (f: DisplayedFile) => `${f.run_id}/${f.tool_call_id}`;
  const seen = new Set(recorded.map(key));
  return [...provisional.filter((f) => !seen.has(key(f))), ...recorded];
}
