// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `backendHeaders` reads the incoming request through `next/headers`, which only
// works inside a Next request. Stand in an empty header set.
vi.mock("next/headers", () => ({ headers: async () => new Headers() }));

import { GET } from "@/app/api/files/[kind]/[...name]/route";

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

function call(path: string, kind = "outputs", name = ["plot.png"]) {
  return GET(new Request(`http://ui.test${path}`), {
    params: Promise.resolve({ kind, name }),
  });
}

describe("GET /api/files/[kind]/[...name]", () => {
  it("forwards the preview flag upstream", async () => {
    fetchMock.mockResolvedValue(new Response("x", { status: 200 }));

    await call("/api/files/outputs/plot.png?project_name=salts&preview=1");

    expect(upstreamUrl()).toBe("http://backend.test/projects/salts/outputs/plot.png?preview=1");
  });

  it("sends no query upstream when preview is absent", async () => {
    fetchMock.mockResolvedValue(new Response("x", { status: 200 }));

    await call("/api/files/outputs/plot.png?project_name=salts");

    expect(upstreamUrl()).toBe("http://backend.test/projects/salts/outputs/plot.png");
  });

  it("passes the upstream disposition and safety headers through", async () => {
    fetchMock.mockResolvedValue(
      new Response("x", {
        status: 200,
        headers: {
          "content-type": "image/png",
          "content-disposition": 'inline; filename="plot.png"',
          "x-content-type-options": "nosniff",
          "content-security-policy": "sandbox; default-src 'none'",
        },
      })
    );

    const res = await call("/api/files/outputs/plot.png?project_name=salts&preview=1");

    expect(res.headers.get("content-type")).toBe("image/png");
    expect(res.headers.get("content-disposition")).toBe('inline; filename="plot.png"');
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-security-policy")).toBe("sandbox; default-src 'none'");
  });

  it("sets the safety headers and an attachment disposition when upstream lacks them", async () => {
    fetchMock.mockResolvedValue(new Response("x", { status: 200 }));

    const res = await call("/api/files/uploads/page.html?project_name=salts", "uploads", [
      "page.html",
    ]);

    expect(res.headers.get("content-disposition")).toBe('attachment; filename="page.html"');
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-security-policy")).toBe("sandbox; default-src 'none'");
  });
});
