import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { ROUTES, STREAM_PATH } from "./fixtures";

/**
 * Installs a fetch stub in the page and a network tripwire around it.
 *
 * Two mechanisms, deliberately:
 *
 *  - The stub replaces `window.fetch` for `/api/**` and answers from
 *    fixtures. Two endpoints get a `ReadableStream` the test drives event by
 *    event, so the SSE parser sees real chunk boundaries and the test can hold
 *    a run open and assert what the page shows mid-stream. A route
 *    interception cannot do that: `route.fulfill` hands over one finished
 *    body.
 *
 *      "send"    `POST /api/chat`, the stream a message opens.
 *      "attach"  `GET /api/chat/run/events`, the stream that re-attaches to a
 *                run. It answers 204 (no active run) unless the test armed it.
 *
 *    Aborting either request errors its stream, as a real fetch does, and is
 *    recorded, so a test can tell "the page stopped watching" from "the run
 *    ended".
 *
 *  - `page.route` is then installed over the same paths purely as a
 *    tripwire. It never serves anything. If it ever fires, a request escaped
 *    the stub and would have reached a Next route handler and then the Python
 *    backend, so the test aborts. That is a stronger check than fulfilling
 *    every route, because it fails loudly instead of quietly substituting.
 */
export type StreamName = "send" | "attach";

export type Stub = {
  /** Requests the page made, as "<METHOD> <path><search>". */
  requests(): Promise<string[]>;
  /** Paths the page asked for that no fixture covers. */
  unstubbed(): Promise<string[]>;
  /** Bodies the page sent with `key` ("PUT /api/chat/session"), parsed. */
  bodies(key: string): Promise<Array<Record<string, unknown>>>;
  /** Streams the page has abandoned, in order. */
  aborted(): Promise<StreamName[]>;
  /** Waits until the page has an open stream. */
  waitForStream(name?: StreamName): Promise<void>;
  /** Writes one SSE event into the open stream. */
  send(event: string, data: unknown, name?: StreamName): Promise<void>;
  /** Closes the stream, ending the watch. */
  end(name?: StreamName): Promise<void>;
  /** Makes the next `GET /api/chat/run/events` open a stream instead of answering 204. */
  armAttach(): Promise<void>;
  /** Makes the next `POST /api/chat` answer 409, as if another view had a run going. */
  armConflict(): Promise<void>;
  /** Changes what a route answers from now on. */
  setRoute(key: string, value: unknown): Promise<void>;
};

/**
 * A route whose answer depends on a query parameter, e.g. one conversation's
 * session versus another's. `cases.default` answers anything unlisted.
 */
export const by = (param: string, cases: Record<string, unknown>) => ({
  __by: param,
  cases,
});

declare global {
  interface Window {
    __vista: {
      requests: string[];
      unstubbed: string[];
      bodies: Record<string, string[]>;
      aborted: string[];
      routes: Record<string, unknown>;
      attachArmed: boolean;
      conflictArmed: boolean;
      streamOpen: boolean;
      isOpen: (name: string) => boolean;
      write: (name: string, chunk: string) => void;
      close: (name: string) => void;
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
      const streams: Record<string, ReadableStreamDefaultController<Uint8Array> | null> = {};

      window.__vista = {
        requests: [],
        unstubbed: [],
        bodies: {},
        aborted: [],
        routes,
        attachArmed: false,
        conflictArmed: false,
        get streamOpen() {
          return (streams.send ?? null) !== null;
        },
        isOpen(name: string) {
          return (streams[name] ?? null) !== null;
        },
        write(name: string, chunk: string) {
          streams[name]?.enqueue(encoder.encode(chunk));
        },
        close(name: string) {
          try {
            streams[name]?.close();
          } catch {
            // already closed
          }
          streams[name] = null;
        },
      } as Window["__vista"];

      const realFetch = window.fetch.bind(window);

      function openStream(name: string, signal: AbortSignal | null | undefined) {
        // A new stream replaces the old one for this name: the page has moved on.
        let controller!: ReadableStreamDefaultController<Uint8Array>;
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            controller = c;
            streams[name] = c;
          },
        });
        signal?.addEventListener("abort", () => {
          window.__vista.aborted.push(name);
          try {
            controller.error(new DOMException("The operation was aborted.", "AbortError"));
          } catch {
            // already closed
          }
          if (streams[name] === controller) streams[name] = null;
        });
        return new Response(body, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }

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
        const key = `${method} ${url.pathname}`;
        window.__vista.requests.push(`${method} ${url.pathname}${url.search}`);
        if (typeof init?.body === "string") {
          (window.__vista.bodies[key] ??= []).push(init.body);
        }
        const signal = init?.signal ?? (input instanceof Request ? input.signal : null);

        if (method === "POST" && url.pathname === streamPath) {
          if (window.__vista.conflictArmed) {
            window.__vista.conflictArmed = false;
            return new Response(
              JSON.stringify({ ok: false, error: "This conversation already has a turn running." }),
              { status: 409, headers: { "content-type": "application/json" } },
            );
          }
          return openStream("send", signal);
        }

        if (method === "GET" && url.pathname === "/api/chat/run/events") {
          if (window.__vista.attachArmed) {
            window.__vista.attachArmed = false;
            return openStream("attach", signal);
          }
          return new Response(null, { status: 204 });
        }

        const current = window.__vista.routes;
        if (!(key in current)) {
          window.__vista.unstubbed.push(key);
          return new Response(JSON.stringify({ error: `no fixture for ${key}` }), {
            status: 599,
            headers: { "content-type": "application/json" },
          });
        }
        let answer = current[key] as unknown;
        if (answer && typeof answer === "object" && "__by" in answer) {
          const picker = answer as { __by: string; cases: Record<string, unknown> };
          const chosen = url.searchParams.get(picker.__by) ?? "";
          answer = chosen in picker.cases ? picker.cases[chosen] : picker.cases.default;
        }
        return new Response(JSON.stringify(answer), {
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
    bodies: async (key) => {
      const raw = await page.evaluate((k) => window.__vista.bodies[k] ?? [], key);
      return raw.map((body) => JSON.parse(body) as Record<string, unknown>);
    },
    aborted: () => page.evaluate(() => window.__vista.aborted as StreamName[]),
    waitForStream: async (name = "send") => {
      await expect
        .poll(() => page.evaluate((n) => window.__vista.isOpen(n), name), { timeout: 15_000 })
        .toBe(true);
    },
    send: async (event, data, name = "send") => {
      const chunk = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      await page.evaluate(([n, c]) => window.__vista.write(n, c), [name, chunk]);
    },
    end: async (name = "send") => {
      await page.evaluate((n) => window.__vista.close(n), name);
    },
    armAttach: async () => {
      await page.evaluate(() => {
        window.__vista.attachArmed = true;
      });
    },
    armConflict: async () => {
      await page.evaluate(() => {
        window.__vista.conflictArmed = true;
      });
    },
    setRoute: async (key, value) => {
      await page.evaluate(([k, v]) => {
        window.__vista.routes[k as string] = v;
      }, [key, value] as const);
    },
  };
}

/**
 * The event sequence a real turn produces, in order: the run starts, the agent
 * logs, calls a tool, gets a result, then streams its answer text in pieces
 * before signalling the final result and the end of the run. Shapes follow
 * `backend/.../api/agent.py`, which emits PydanticAI stream events over SSE
 * with real `event:` lines, wrapped in `run_started` and `run_finished`.
 */
export const ANSWER_CHUNKS = [
  "FLiBe density at 873 K is ",
  "about 2.28 g/cm³, ",
  "from the MSTDB fit.",
];

export const RUN_ID = "run-00000000000000000000000000000001";

/** A run's opening event. */
export async function startRun(
  stub: Stub,
  prompt: string,
  name: StreamName = "send",
  runId = RUN_ID,
) {
  await stub.send(
    "run_started",
    { event_kind: "run_started", run_id: runId, user_prompt: prompt },
    name,
  );
}

/** A run's closing event. */
export async function finishRun(
  stub: Stub,
  state: "done" | "stopped" | "failed" | "interrupted" = "done",
  name: StreamName = "send",
) {
  await stub.send("run_finished", { event_kind: "run_finished", state }, name);
}

export async function toolCall(stub: Stub, name: StreamName = "send") {
  await stub.send(
    "function_tool_call",
    {
      event_kind: "function_tool_call",
      part: { part_kind: "tool-call", tool_name: "rag_search", args: '{"query":"FLiBe density"}' },
    },
    name,
  );
}

export async function streamAnswer(stub: Stub, name: StreamName = "send") {
  await stub.send(
    "function_tool_result",
    {
      event_kind: "function_tool_result",
      part: { part_kind: "tool-return", tool_name: "rag_search", content: "3 passages" },
    },
    name,
  );
  await stub.send("final_result", { event_kind: "final_result" }, name);
  await stub.send(
    "part_start",
    { event_kind: "part_start", index: 0, part: { part_kind: "text", content: "" } },
    name,
  );
  for (const chunk of ANSWER_CHUNKS) {
    await stub.send(
      "part_delta",
      {
        event_kind: "part_delta",
        index: 0,
        delta: { part_delta_kind: "text", content_delta: chunk },
      },
      name,
    );
  }
  await stub.send(
    "part_end",
    { event_kind: "part_end", index: 0, part: { part_kind: "text", content: ANSWER_CHUNKS.join("") } },
    name,
  );
  await stub.send(
    "agent_run_result",
    { event_kind: "agent_run_result", result: { new_messages: [] } },
    name,
  );
}

export async function streamOneTurn(
  stub: Stub,
  opts: { pauseAfterToolCall?: () => Promise<void>; prompt?: string } = {},
) {
  await startRun(stub, opts.prompt ?? "What is the density of FLiBe at 873 K?");
  await stub.send("log", { event_kind: "log", level: "INFO", area: "Agent", message: "run started" });
  await toolCall(stub);

  if (opts.pauseAfterToolCall) await opts.pauseAfterToolCall();

  await streamAnswer(stub);
  await finishRun(stub, "done");
  await stub.end();
}
