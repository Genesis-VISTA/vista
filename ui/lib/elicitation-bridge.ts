/**
 * In-memory bridge between the SSE chat route (which awaits user input)
 * and the /api/chat/elicitation endpoint (where the browser POSTs form data).
 *
 * Both run in the same Next.js Node.js process sharing module scope.
 */

export type ElicitationResponse = {
  action: "accept" | "decline" | "cancel";
  content?: Record<string, unknown>;
};

type PendingEntry = {
  resolve: (resp: ElicitationResponse) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

const pending = new Map<string, PendingEntry>();

const TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes

/**
 * Register a pending elicitation. Returns a promise that resolves when
 * the browser submits the form via resolveElicitation().
 */
export function registerElicitation(id: string): Promise<ElicitationResponse> {
  // Cancel any previous entry with the same id
  cancelElicitation(id);

  return new Promise<ElicitationResponse>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      resolve({ action: "cancel" });
    }, TIMEOUT_MS);

    pending.set(id, { resolve, reject, timer });
  });
}

/**
 * Resolve a pending elicitation with the user's form response.
 * Returns true if the elicitation was found and resolved.
 */
export function resolveElicitation(
  id: string,
  response: ElicitationResponse
): boolean {
  const entry = pending.get(id);
  if (!entry) return false;
  clearTimeout(entry.timer);
  pending.delete(id);
  entry.resolve(response);
  return true;
}

/**
 * Cancel a pending elicitation (e.g. on cleanup).
 */
export function cancelElicitation(id: string): void {
  const entry = pending.get(id);
  if (!entry) return;
  clearTimeout(entry.timer);
  pending.delete(id);
  entry.resolve({ action: "cancel" });
}
