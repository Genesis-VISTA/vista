// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({ headers: async () => new Headers() }));

import { GET } from "@/app/api/chat/displayed-files/route";

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

const request = (query: string) =>
  new Request(`http://ui.test/api/chat/displayed-files${query}`);

describe("GET /api/chat/displayed-files", () => {
  it("forwards to the conversation's displayed-files and returns the list as is", async () => {
    const list = [{ kind: "outputs", path: "a.png", status: "current" }];
    fetchMock.mockResolvedValue(new Response(JSON.stringify(list), { status: 200 }));
    const res = await GET(request("?project_name=molten%20salt&chat_session_id=c-1"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(list);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://backend.test/projects/molten%20salt/chat-sessions/c-1/displayed-files",
    );
  });

  it("passes the backend's 404 through", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 404 }));
    const res = await GET(request("?project_name=p&chat_session_id=c-1"));
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ detail: "nope" });
  });

  it("asks for both parameters", async () => {
    expect((await GET(request("?chat_session_id=c"))).status).toBe(400);
    expect((await GET(request("?project_name=p"))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 when the backend is down", async () => {
    fetchMock.mockRejectedValue(new Error("refused"));
    const res = await GET(request("?project_name=p&chat_session_id=c"));
    expect(res.status).toBe(502);
  });
});
