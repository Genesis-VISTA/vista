// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `backendHeaders` reads the incoming request through `next/headers`, which only
// works inside a Next request. Stand in an empty header set.
vi.mock("next/headers", () => ({ headers: async () => new Headers() }));

import { GET as getEvents } from "@/app/api/chat/run/events/route";
import { POST as postStop } from "@/app/api/chat/run/stop/route";
import { GET as getStatus } from "@/app/api/chat/runs/status/route";
import { PUT as putSession } from "@/app/api/chat/session/route";

const fetchMock = vi.fn();

beforeEach(() => {
  process.env.VISTA_BACKEND_URL = "http://backend.test";
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.VISTA_BACKEND_URL;
});

const upstreamUrl = () => String(fetchMock.mock.calls[0][0]);
const get = (path: string, init?: RequestInit) =>
  new Request(`http://ui.test${path}`, init);
const post = (path: string, body: unknown) =>
  new Request(`http://ui.test${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

/** An SSE body the test controls chunk by chunk. */
function sseBody() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    body,
    write: (chunk: string) => controller.enqueue(encoder.encode(chunk)),
    close: () => controller.close(),
  };
}

describe("GET /api/chat/run/events", () => {
  it("re-attaches to the backend's run events and streams them through as they arrive", async () => {
    const sse = sseBody();
    fetchMock.mockResolvedValue(
      new Response(sse.body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const res = await getEvents(
      get("/api/chat/run/events?project_name=molten%20salt&chat_session_id=abc&after=7"),
    );

    expect(upstreamUrl()).toBe(
      "http://backend.test/projects/molten%20salt/chat-sessions/abc/run/events?after=7",
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("cache-control")).toBe("no-cache");

    // Streamed, not buffered: the first chunk is readable before the run ends.
    const reader = res.body!.getReader();
    sse.write("id: 8\nevent: log\ndata: {}\n\n");
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toBe("id: 8\nevent: log\ndata: {}\n\n");
    sse.write("id: 9\nevent: run_finished\ndata: {}\n\n");
    sse.close();
    const second = await reader.read();
    expect(new TextDecoder().decode(second.value)).toContain("id: 9");
    expect((await reader.read()).done).toBe(true);
  });

  it("replays from the start when no after is given", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await getEvents(get("/api/chat/run/events?project_name=p&chat_session_id=abc"));
    expect(upstreamUrl()).toContain("/run/events?after=0");
  });

  it("answers 204 with no body when the conversation has no active run", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const res = await getEvents(get("/api/chat/run/events?project_name=p&chat_session_id=abc"));
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });

  it("forwards the browser's abort upstream, so watching can stop", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const controller = new AbortController();
    await getEvents(
      get("/api/chat/run/events?project_name=p&chat_session_id=abc", {
        signal: controller.signal,
      }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    controller.abort();
    expect(init.signal?.aborted).toBe(true);
  });

  it("rejects a missing project or conversation without calling the backend", async () => {
    expect((await getEvents(get("/api/chat/run/events?chat_session_id=abc"))).status).toBe(400);
    expect((await getEvents(get("/api/chat/run/events?project_name=p"))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes a backend error through with its status", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Chat session not found" }), { status: 404 }),
    );
    const res = await getEvents(get("/api/chat/run/events?project_name=p&chat_session_id=abc"));
    expect(res.status).toBe(404);
    expect((await res.json()).error).toContain("Chat session not found");
  });

  it("answers 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new Error("connect ECONNREFUSED"));
    const res = await getEvents(get("/api/chat/run/events?project_name=p&chat_session_id=abc"));
    expect(res.status).toBe(502);
    expect((await res.json()).error).toContain("ECONNREFUSED");
  });
});

describe("POST /api/chat/run/stop", () => {
  it("stops the conversation's run and passes the backend's answer through", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 202 }));

    const res = await postStop(post("/api/chat/run/stop", { project_name: "p", chat_session_id: "abc" }));

    expect(upstreamUrl()).toBe("http://backend.test/projects/p/chat-sessions/abc/run/stop");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("passes 404 through when the run had already finished", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "No active run" }), { status: 404 }),
    );
    const res = await postStop(post("/api/chat/run/stop", { project_name: "p", chat_session_id: "abc" }));
    expect(res.status).toBe(404);
  });

  it("rejects a malformed request without calling the backend", async () => {
    const bad = new Request("http://ui.test/api/chat/run/stop", { method: "POST", body: "{" });
    expect((await postStop(bad)).status).toBe(400);
    expect((await postStop(post("/api/chat/run/stop", { chat_session_id: "abc" }))).status).toBe(400);
    expect((await postStop(post("/api/chat/run/stop", { project_name: "p" }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new Error("down"));
    const res = await postStop(post("/api/chat/run/stop", { project_name: "p", chat_session_id: "abc" }));
    expect(res.status).toBe(502);
  });
});

describe("GET /api/chat/runs/status", () => {
  it("returns the project's conversation statuses", async () => {
    const statuses = [{ chat_session_id: "abc", status: "done", unseen: true }];
    fetchMock.mockResolvedValue(new Response(JSON.stringify(statuses), { status: 200 }));

    const res = await getStatus(get("/api/chat/runs/status?project_name=molten%20salt"));

    expect(upstreamUrl()).toBe("http://backend.test/projects/molten%20salt/chat-runs/status");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(statuses);
  });

  it("rejects a missing project", async () => {
    expect((await getStatus(get("/api/chat/runs/status"))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new Error("down"));
    expect((await getStatus(get("/api/chat/runs/status?project_name=p"))).status).toBe(502);
  });
});

describe("PUT /api/chat/session", () => {
  const put = (body: unknown) =>
    putSession(
      new Request("http://ui.test/api/chat/session", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      }),
    );

  it("forwards ack_run and never forwards model history", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));

    await put({
      project_name: "p",
      chat_session_id: "abc",
      messages: [],
      message_history: [{ kind: "request" }],
      ack_run: true,
    });

    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent.ack_run).toBe(true);
    expect(sent).not.toHaveProperty("message_history");
  });

  it("does not ack unless asked", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));
    await put({ project_name: "p", chat_session_id: "abc", messages: [] });
    const sent = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sent.ack_run).toBe(false);
  });
});
