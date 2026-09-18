import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { ROUTES, STREAM_PATH } from "./fixtures";

/**
 * Installs a fetch stub in the page and a network tripwire around it.
 *
 * Two mechanisms, deliberately:
 *
 *  - The stub replaces `window.fetch` for `/api/**` and answers from
 *    fixtures. `POST /api/chat` gets a `ReadableStream` the test drives event
 *    by event, so the SSE parser sees real chunk boundaries and the test can
 *    hold a run open and assert what the page shows mid-stream. A route
 *    interception cannot do that: `route.fulfill` hands over one finished
 *    body.
 *
 *  - `page.route` is then installed over the same paths purely as a
 *    tripwire. It never serves anything. If it ever fires, a request escaped
 *    the stub and would have reached a Next route handler and then the Python
 *    backend, so the test aborts. That is a stronger check than fulfilling
 *    every route, because it fails loudly instead of quietly substituting.
 */
export type Stub = {
  /** Requests the page made, as "<METHOD> <path><search>". */
  requests(): Promise<string[]>;
  /** Paths the page asked for that no fixture covers. */
  unstubbed(): Promise<string[]>;
  /** Waits until the page has an open `/api/chat` stream. */
  waitForStream(): Promise<void>;
  /** Writes one SSE event into the open stream. */
  send(event: string, data: unknown): Promise<void>;
  /** Closes the stream, ending the agent run. */
  end(): Promise<void>;
};

declare global {
  interface Window {
    __vista: {
      requests: string[];
      unstubbed: string[];
      streamOpen: boolean;
      write: (chunk: string) => void;
      close: () => void;
    };
  }
}

export async function installStub(
  page: Page,
  /** Per-test route overrides, merged over the shared fixtures. */
  overrides: Record<string, unknown> = {},
): Promise<Stub> {
  await page.addInitScript(
    ({ routes, streamPath }) => {
      const encoder = new TextEncoder();
      let stream: ReadableStreamDefaultController<Uint8Array> | null = null;

      window.__vista = {
        requests: [],
        unstubbed: [],
        get streamOpen() {
          return stream !== null;
        },
        write(chunk: string) {
          stream?.enqueue(encoder.encode(chunk));
        },
        close() {
          stream?.close();
          stream = null;
        },
      } as Window["__vista"];

      const realFetch = window.fetch.bind(window);

      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const href =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        const url = new URL(href, location.origin);
        if (!url.pathname.startsWith("/api/")) return realFetch(input, init);

        const method = (
          init?.method ??
          (input instanceof Request ? input.method : "GET")
        ).toUpperCase();
        window.__vista.requests.push(`${method} ${url.pathname}${url.search}`);

        if (method === "POST" && url.pathname === streamPath) {
          const body = new ReadableStream<Uint8Array>({
            start(controller) {
              stream = controller;
            },
          });
          return new Response(body, {
            status: 200,
            headers: { "content-type": "text/event-stream" },
          });
        }

        const key = `${method} ${url.pathname}`;
        if (!(key in routes)) {
          window.__vista.unstubbed.push(key);
          return new Response(JSON.stringify({ error: `no fixture for ${key}` }), {
            status: 599,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response(JSON.stringify(routes[key]), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      };
    },
    { routes: { ...ROUTES, ...overrides }, streamPath: STREAM_PATH },
  );

  await page.route("**/api/**", async (route) => {
    await route.abort("failed");
    throw new Error(
      `a request escaped the stub and hit the network: ${route.request().method()} ${route.request().url()}`,
    );
  });

  return {
    requests: () => page.evaluate(() => window.__vista.requests),
    unstubbed: () => page.evaluate(() => window.__vista.unstubbed),
    waitForStream: async () => {
      await expect
        .poll(() => page.evaluate(() => window.__vista.streamOpen), { timeout: 15_000 })
        .toBe(true);
    },
    send: async (event, data) => {
      const chunk = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      await page.evaluate((c) => window.__vista.write(c), chunk);
    },
    end: async () => {
      await page.evaluate(() => window.__vista.close());
    },
  };
}

/**
 * The event sequence a real turn produces, in order: the agent logs, calls a
 * tool, gets a result, then streams its answer text in pieces before
 * signalling the final result. Shapes follow
 * `backend/.../api/agent.py`, which emits PydanticAI stream events over SSE
 * with real `event:` lines.
 */
export const ANSWER_CHUNKS = [
  "FLiBe density at 873 K is ",
  "about 2.28 g/cm³, ",
  "from the MSTDB fit.",
];

export async function streamOneTurn(stub: Stub, opts: { pauseAfterToolCall?: () => Promise<void> } = {}) {
  await stub.send("log", { event_kind: "log", level: "INFO", area: "Agent", message: "run started" });
  await stub.send("function_tool_call", {
    event_kind: "function_tool_call",
    part: { part_kind: "tool-call", tool_name: "rag_search", args: '{"query":"FLiBe density"}' },
  });

  if (opts.pauseAfterToolCall) await opts.pauseAfterToolCall();

  await stub.send("function_tool_result", {
    event_kind: "function_tool_result",
    part: { part_kind: "tool-return", tool_name: "rag_search", content: "3 passages" },
  });
  await stub.send("final_result", { event_kind: "final_result" });
  await stub.send("part_start", {
    event_kind: "part_start",
    index: 0,
    part: { part_kind: "text", content: "" },
  });
  for (const chunk of ANSWER_CHUNKS) {
    await stub.send("part_delta", {
      event_kind: "part_delta",
      index: 0,
      delta: { part_delta_kind: "text", content_delta: chunk },
    });
  }
  await stub.send("part_end", {
    event_kind: "part_end",
    index: 0,
    part: { part_kind: "text", content: ANSWER_CHUNKS.join("") },
  });
  await stub.send("agent_run_result", {
    event_kind: "agent_run_result",
    result: { new_messages: [] },
  });
  await stub.end();
}
