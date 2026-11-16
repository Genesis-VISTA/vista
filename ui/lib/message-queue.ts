/**
 * Messages queued for a conversation's running turn (openspec change background-tasks).
 *
 * A send to a conversation whose turn is still running is not refused: the
 * backend queues it in the conversation's inbox and answers 202 with the item.
 * The running turn takes it at its next step, and until then the researcher can
 * edit or delete it. Every view shows the same queue: the run's events announce
 * each change (`inbox_changed`), and a view that opens the conversation reads it.
 */

export type InboxItem = {
  id: number;
  /** `message` from the researcher, or `task_event` from a background task. */
  kind: string;
  text: string;
  payload?: Record<string, unknown> | null;
  state: string;
  created_at: string;
  taken_at?: string | null;
};

/** The researcher's own queued messages, the ones shown as chips. */
export function queuedMessages(items: InboxItem[]): InboxItem[] {
  return items.filter((item) => item.kind === "message" && item.state === "queued");
}

function isItem(value: unknown): value is InboxItem {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.id === "number" && typeof v.kind === "string" && typeof v.text === "string";
}

/**
 * The queued item, when `/api/chat` answered that the message was queued for a
 * running turn rather than starting one. Null for any other answer.
 */
export function readQueuedSend(status: number, body: unknown): InboxItem | null {
  if (status !== 202 || typeof body !== "object" || body === null) return null;
  const { started, item } = body as { started?: unknown; item?: unknown };
  return started === false && isItem(item) ? item : null;
}

/** The items of an `inbox_changed` event; empty for anything malformed. */
export function readInboxChanged(data: unknown): InboxItem[] {
  const queued = (data as { queued?: unknown } | null)?.queued;
  return Array.isArray(queued) ? queued.filter(isItem) : [];
}

/**
 * The message box after Stop gave back queued messages: their texts first, in
 * the order they were sent, then whatever the researcher had typed since.
 */
export function restoreWithdrawn(current: string, withdrawn: string[]): string {
  const parts = [...withdrawn, current].map((t) => t.trim()).filter(Boolean);
  return parts.join("\n\n");
}

export type QueueChange =
  | { ok: true; item: InboxItem | null }
  /** Too late: the agent has it. `item` is what it received. */
  | { ok: false; taken: true; item: InboxItem | null }
  | { ok: false; taken: false; error: string };

function inboxUrl(projectName: string, chatSessionId: string, itemId?: number): string {
  const params = new URLSearchParams({ project_name: projectName, chat_session_id: chatSessionId });
  if (itemId !== undefined) params.set("item_id", String(itemId));
  return `/api/chat/inbox?${params}`;
}

/** What is queued for the conversation, in arrival order. */
export async function fetchQueue(projectName: string, chatSessionId: string): Promise<InboxItem[]> {
  const response = await fetch(inboxUrl(projectName, chatSessionId));
  if (!response.ok) return [];
  const body: unknown = await response.json().catch(() => null);
  return Array.isArray(body) ? body.filter(isItem) : [];
}

async function readChange(response: Response): Promise<QueueChange> {
  const body: unknown = response.status === 204 ? null : await response.json().catch(() => null);
  if (response.ok) return { ok: true, item: isItem(body) ? body : null };
  const fields = (typeof body === "object" && body !== null ? body : {}) as Record<string, unknown>;
  if (response.status === 409 && fields.code === "inbox_item_taken") {
    return { ok: false, taken: true, item: isItem(fields.item) ? fields.item : null };
  }
  const detail = typeof fields.detail === "string" ? fields.detail : null;
  const error = typeof fields.error === "string" ? fields.error : null;
  return { ok: false, taken: false, error: detail ?? error ?? `Backend returned ${response.status}` };
}

export async function editQueued(
  projectName: string,
  chatSessionId: string,
  itemId: number,
  text: string,
): Promise<QueueChange> {
  const response = await fetch(inboxUrl(projectName, chatSessionId, itemId), {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return readChange(response);
}

export async function deleteQueued(
  projectName: string,
  chatSessionId: string,
  itemId: number,
): Promise<QueueChange> {
  return readChange(await fetch(inboxUrl(projectName, chatSessionId, itemId), { method: "DELETE" }));
}
