// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({ headers: async () => new Headers() }));

import { GET } from "@/app/api/files/[kind]/route";

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

describe("GET /api/files/[kind]", () => {
  it("passes shown_in through, null where the backend sent none", async () => {
    const shown = { chat_session_id: "c1", chat_title: "T", run_id: "r1", tool_call_id: "t1" };
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify([
          { name: "a.png", size: 1, created: "x", modified: "2026-01-01T00:00:00Z", shown_in: shown },
          { name: "b.txt", size: 2, created: "x", modified: "2026-01-01T00:00:00Z" },
        ]),
        { status: 200 },
      ),
    );
    const res = await GET(new Request("http://ui.test/api/files/outputs?project_name=salts"), {
      params: Promise.resolve({ kind: "outputs" }),
    });
    const body = await res.json();
    expect(body.map((f: { shown_in: unknown }) => f.shown_in)).toEqual([shown, null]);
    expect(body[0]).toMatchObject({ name: "a.png", modifiedAt: "2026-01-01T00:00:00Z" });
  });
});
