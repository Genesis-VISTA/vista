/**
 * What the chat page does when `/api/chat` refuses a send.
 *
 * The backend refuses a turn with 409 for two unrelated reasons, told apart
 * only by `code`: the conversation already has a turn running (`run_busy`),
 * which the page answers by showing that turn, and anything else, such as no
 * API key or no model chosen (`inference_not_configured`), which it answers by
 * saying why. Never decided from the status or the wording alone.
 */

export type RefusedSend =
  /** The conversation is busy: show the turn that is running. */
  | { kind: "attach" }
  /** Any other refusal: show `message` in the conversation. */
  | { kind: "note"; message: string };

/**
 * Decide on a refused send from its status and parsed JSON body.
 *
 * `body` is the backend's own `{detail, code}`, which the proxy passes
 * through, or the proxy's `{ok: false, error}` for a body that was not JSON,
 * or `null` when the response had no JSON at all.
 */
export function readRefusedSend(status: number, body: unknown): RefusedSend {
  const fields = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  if (fields.code === "run_busy") return { kind: "attach" };
  const text = (value: unknown) => (typeof value === "string" && value ? value : null);
  return {
    kind: "note",
    message: text(fields.detail) ?? text(fields.error) ?? `Backend returned ${status}`,
  };
}
