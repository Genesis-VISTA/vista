// @vitest-environment node
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// `backendHeaders` reads the incoming request through `next/headers`, which only
// works inside a Next request. Each test sets what the "request" carries.
let incoming = new Headers();
vi.mock("next/headers", () => ({ headers: async () => incoming }));

import { GET, POST } from "@/app/api/[...path]/route";
import { backendHeaders } from "@/app/api/_backend";

type Seen = { method?: string; url?: string; authorization?: string; body: string };
let seen: Seen;
let server: Server;

beforeAll(async () => {
  server = createServer(async (req: IncomingMessage, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    seen = { method: req.method, url: req.url, authorization: req.headers.authorization, body };
    if (req.url?.startsWith("/events")) {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("data: one\n\n");
      setTimeout(() => res.end("data: two\n\n"), 300);
      return;
    }
    if (req.url?.startsWith("/gone")) {
      res.writeHead(204).end();
      return;
    }
    res.writeHead(201, { "content-type": "application/json", "x-extra": "kept" });
    res.end(JSON.stringify({ ok: true }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  process.env.VISTA_BACKEND_URL = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(() => {
  delete process.env.VISTA_BACKEND_URL;
  server.close();
});

beforeEach(() => {
  incoming = new Headers();
  seen = { body: "" };
});

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });

describe("catch-all /api proxy", () => {
  it("forwards method, path, query and body, and the answer's status and headers", async () => {
    const res = await POST(
      new Request("http://ui.test/api/projects/a%20b/thing?x=1&y=2", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ hello: "world" }),
      }),
      ctx("projects", "a b", "thing"),
    );
    expect(res.status).toBe(201);
    expect(res.headers.get("x-extra")).toBe("kept");
    expect(await res.json()).toEqual({ ok: true });
    expect(seen).toMatchObject({ method: "POST", url: "/projects/a%20b/thing?x=1&y=2" });
    expect(seen.body).toBe('{"hello":"world"}');
    expect(seen.authorization).toBeUndefined();
  });

  it("sends X-Access-Token to the backend as Authorization: Bearer", async () => {
    await GET(new Request("http://ui.test/api/me", { headers: { "x-access-token": "tok-1" } }), ctx("me"));
    expect(seen.authorization).toBe("Bearer tok-1");
  });

  it("falls back to an incoming Authorization: Bearer, and prefers X-Access-Token", async () => {
    await GET(new Request("http://ui.test/api/me", { headers: { authorization: "Bearer tok-2" } }), ctx("me"));
    expect(seen.authorization).toBe("Bearer tok-2");
    await GET(
      new Request("http://ui.test/api/me", { headers: { authorization: "Bearer tok-2", "x-access-token": "tok-3" } }),
      ctx("me"),
    );
    expect(seen.authorization).toBe("Bearer tok-3");
  });

  it("streams an event stream through as it arrives", async () => {
    const started = Date.now();
    const res = await GET(new Request("http://ui.test/api/events"), ctx("events"));
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    const first = new TextDecoder().decode((await res.body!.getReader().read()).value);
    expect(first).toBe("data: one\n\n");
    // The backend holds the stream open for 300 ms: the first event came before it ended.
    expect(Date.now() - started).toBeLessThan(250);
  });

  it("passes a 204 on without a body and refuses a path that climbs out", async () => {
    expect((await GET(new Request("http://ui.test/api/gone"), ctx("gone"))).status).toBe(204);
    expect((await GET(new Request("http://ui.test/api/x"), ctx("..", "x"))).status).toBe(400);
  });

  it("answers 502 with a detail when the backend is down", async () => {
    const saved = process.env.VISTA_BACKEND_URL;
    process.env.VISTA_BACKEND_URL = "http://127.0.0.1:1";
    const res = await GET(new Request("http://ui.test/api/x"), ctx("x"));
    process.env.VISTA_BACKEND_URL = saved;
    expect(res.status).toBe(502);
    expect((await res.json()).detail).toMatch(/unreachable/i);
  });
});

describe("backendHeaders (routes with their own logic)", () => {
  it("adds the bearer from the incoming request, and nothing without one", async () => {
    incoming = new Headers({ "x-access-token": "tok-4" });
    expect(await backendHeaders({ accept: "application/json" })).toEqual({
      authorization: "Bearer tok-4",
      accept: "application/json",
    });
    incoming = new Headers();
    expect(await backendHeaders()).toEqual({});
  });
});
