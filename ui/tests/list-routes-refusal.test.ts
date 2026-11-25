// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `backendHeaders` reads the incoming request through `next/headers`, which only
// works inside a Next request. Stand in an empty header set.
vi.mock("next/headers", () => ({ headers: async () => new Headers() }));

import { GET as projects } from "@/app/api/projects/route";
import { GET as skills } from "@/app/api/skills/route";
import { GET as knowledgeBases } from "@/app/api/knowledge-bases/route";

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

const request = () => new Request("http://ui.test/api/list");
const routes: Record<string, () => Promise<Response>> = {
  projects: () => projects(),
  skills: () => skills(),
  knowledgeBases: () => knowledgeBases(request()),
};

describe.each(Object.entries(routes))("GET %s", (_name, get) => {
  it.each([401, 403])("passes a %i refusal through (hosted: signed out or no access)", async (status) => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Sign in to use VISTA." }), { status }),
    );
    const response = await get();
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ detail: "Sign in to use VISTA." });
  });

  it("still shows an empty list when the backend fails otherwise", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 500 }));
    const response = await get();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([]);
  });
});
