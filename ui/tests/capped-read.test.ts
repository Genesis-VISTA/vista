import { describe, expect, it, vi } from "vitest";
import { readCapped } from "@/lib/capped-read";

function streamOf(chunks: Uint8Array[], onCancel?: () => void): Response {
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(chunks[i++]);
      else controller.close();
    },
    cancel() {
      onCancel?.();
    },
  });
  return new Response(body);
}

describe("readCapped", () => {
  it("reads a small body whole", async () => {
    const out = await readCapped(streamOf([new TextEncoder().encode("hello")]));
    expect(out).toEqual({ text: "hello", bytesRead: 5, truncated: false });
  });
  it("stops at the cap and cancels the stream", async () => {
    const cancel = vi.fn();
    const chunk = new Uint8Array(400).fill(97);
    const out = await readCapped(streamOf([chunk, chunk, chunk, chunk], cancel), 1000);
    expect(out.bytesRead).toBe(1000);
    expect(out.text).toHaveLength(1000);
    expect(out.truncated).toBe(true);
    expect(cancel).toHaveBeenCalled();
  });
  it("is not truncated when the body is exactly the cap", async () => {
    const out = await readCapped(streamOf([new Uint8Array(10).fill(97)]), 10);
    expect(out.truncated).toBe(false);
    expect(out.bytesRead).toBe(10);
  });
  it("keeps a multibyte character split across chunks", async () => {
    const bytes = new TextEncoder().encode("a€b"); // € is 3 bytes
    const out = await readCapped(streamOf([bytes.slice(0, 2), bytes.slice(2)]));
    expect(out.text).toBe("a€b");
  });
  it("drops a character cut by the cap", async () => {
    const bytes = new TextEncoder().encode("a€b");
    const out = await readCapped(streamOf([bytes]), 3);
    expect(out.text).toBe("a");
    expect(out.truncated).toBe(true);
  });
});
