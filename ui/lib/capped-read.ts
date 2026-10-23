/** How much of a file a preview reads before it stops: 1 MiB. */
export const PREVIEW_CAP_BYTES = 1_048_576;

export type CappedRead = {
  text: string;
  /** Bytes taken from the stream (at most the cap). */
  bytesRead: number;
  /** True when the stream had more than the cap and was cancelled. */
  truncated: boolean;
};

/**
 * Read a response body as text, up to `cap` bytes, then cancel the stream.
 *
 * The backend has no range support for project files, so the preview stops
 * reading instead. The decoder streams so a multibyte character split across
 * chunks is intact; a character cut by the cap itself is dropped.
 */
export async function readCapped(response: Response, cap: number = PREVIEW_CAP_BYTES): Promise<CappedRead> {
  const body = response.body;
  if (!body) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    const truncated = bytes.length > cap;
    const kept = truncated ? bytes.subarray(0, cap) : bytes;
    const decoder = new TextDecoder("utf-8");
    let text = decoder.decode(kept, { stream: truncated });
    if (!truncated) text += decoder.decode();
    return { text, bytesRead: kept.length, truncated };
  }
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let text = "";
  let bytesRead = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      text += decoder.decode();
      return { text, bytesRead, truncated: false };
    }
    if (!value) continue;
    const room = cap - bytesRead;
    if (value.length >= room) {
      const chunk = value.subarray(0, room);
      bytesRead += chunk.length;
      text += decoder.decode(chunk, { stream: true });
      // Landing exactly on the cap is only a truncation if more follows.
      let more = value.length > room;
      if (!more) {
        const next = await reader.read();
        more = !next.done;
      }
      if (!more) {
        text += decoder.decode();
        return { text, bytesRead, truncated: false };
      }
      await reader.cancel();
      return { text, bytesRead, truncated: true };
    }
    bytesRead += value.length;
    text += decoder.decode(value, { stream: true });
  }
}
