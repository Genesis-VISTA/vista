/**
 * What the chat page says when `/api/chat` refuses a send.
 *
 * A send to a busy conversation is no longer refused: it is queued for the
 * running turn (202, `lib/message-queue.ts`). Any refusal left, such as no API
 * key or no model chosen (`inference_not_configured`), is shown with its own
 * message so the researcher can fix it and resend.
 */

export type RefusedSend = { kind: "note"; message: string };

/**
 * Decide on a refused send from its status and parsed JSON body.
 *
 * `body` is the backend's own `{detail, code}`, which the proxy passes
 * through, or the proxy's `{ok: false, error}` for a body that was not JSON,
 * or `null` when the response had no JSON at all.
 */
export function readRefusedSend(status: number, body: unknown): RefusedSend {
  const fields = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  const text = (value: unknown) => (typeof value === "string" && value ? value : null);
  return {
    kind: "note",
    message: text(fields.detail) ?? text(fields.error) ?? `Backend returned ${status}`,
  };
}
