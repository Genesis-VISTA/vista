/**
 * Reading a chat run's Server-Sent Events.
 *
 * The same parser serves the stream a send opens and the one a re-attach opens
 * (`GET /api/chat/run/events`): both carry a run's events from its start, so the
 * page draws them with the same renderer (`run-renderer.ts`).
 */

export type SseEvent = {
  /** The `event:` line, or null when the block had none. */
  event: string | null;
  /** The JSON payload. */
  data: unknown;
  /** The `id:` line: for a run's events, the event's position in the run. */
  id: string | null;
};

/**
 * Read `body` to its end, calling `onEvent` for each complete event block.
 *
 * Resolves when the stream closes. Rejects if the stream errors or the request
 * was aborted; the caller decides whether that is news. A block whose data is not
 * valid JSON is dropped rather than ending the stream.
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event: string | null = null;
  let id: string | null = null;
  let dataLines: string[] = [];

  function dispatchBlock() {
    if (dataLines.length === 0) {
      event = null;
      id = null;
      return;
    }
    const raw = dataLines.join("\n");
    const block = { event, id };
    dataLines = [];
    event = null;
    id = null;
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      return; // Drop malformed event blocks rather than aborting the stream.
    }
    onEvent({ ...block, data });
  }

  function takeLine(rawLine: string) {
    const line = rawLine.replace(/\r$/, "");
    if (line === "") {
      dispatchBlock();
    } else if (line.startsWith(":")) {
      // SSE comment (keep-alive).
    } else if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("id:")) {
      id = line.slice(3).trim();
    } else if (line.startsWith("data:")) {
      // Strip the single leading space SSE permits after "data:".
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) takeLine(line);
    }
    // Flush any trailing block held in the buffer.
    if (buffer) takeLine(buffer);
    dispatchBlock();
  } finally {
    reader.releaseLock();
  }
}
