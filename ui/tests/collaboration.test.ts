import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  availability,
  refreshCollaborationStatus,
  resetCollaborationForTests,
  teamsView,
  useCollaboration,
  visibility,
  type CollaborationStatus,
} from "@/lib/collaboration";

function status(overrides: Partial<CollaborationStatus> = {}): CollaborationStatus {
  return {
    configured: true,
    reachable: true,
    reason: null,
    url: "http://127.0.0.1:8002",
    source: "settings",
    server_version: "0.1.0",
    enabled: true,
    signed_in: true,
    sign_in_required: false,
    refused: false,
    shown: true,
    default_server: false,
    sign_in_url: null,
    user: null,
    teams: [],
    ...overrides,
  };
}

let current: CollaborationStatus | null;
const fetchMock = vi.fn();

beforeEach(() => {
  resetCollaborationForTests();
  current = status();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () =>
    current
      ? new Response(JSON.stringify(current), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      : new Response("bad gateway", { status: 502 }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("availability", () => {
  it("is off while checking, with the reason", () => {
    expect(availability({ status: null, error: null })).toEqual({
      available: false,
      reason: "Checking the collaboration server…",
    });
  });

  it("is on only for a reachable server", () => {
    expect(availability({ status: status(), error: null })).toEqual({ available: true, reason: null });
  });

  it("is off for a reachable server that wants a token", () => {
    const reason = "Get a token from https://c.test/sign-in and paste it under Settings → Collaboration.";
    expect(
      availability({ status: status({ signed_in: false, sign_in_required: true, reason }), error: null }),
    ).toEqual({ available: false, reason });
  });

  it("carries the backend's reason when off", () => {
    const reason = "No collaboration server is set.";
    expect(
      availability({ status: status({ configured: false, reachable: false, reason }), error: null }),
    ).toEqual({ available: false, reason });
  });

  it("says when the status itself could not be fetched", () => {
    expect(availability({ status: null, error: "request failed (502)" })).toEqual({
      available: false,
      reason: "Couldn't check the collaboration server: request failed (502).",
    });
  });
});

const QUIET = {
  reachable: false,
  signed_in: false,
  sign_in_required: true,
  shown: false,
  default_server: true,
  reason:
    "Sign in to https://obsidian.ccs.ornl.gov/vista/api to publish and import: get a token from its sign-in page and paste it under Settings → Collaboration.",
};

describe("visibility", () => {
  it("hides the controls while checking, and assumes on", () => {
    expect(visibility({ status: null })).toEqual({ enabled: true, shown: false, default_server: false });
  });

  it("hides them for a quiet install, which is on", () => {
    expect(visibility({ status: status(QUIET) })).toEqual({ enabled: true, shown: false, default_server: true });
  });

  it("hides them, and the Skill Hub, when off", () => {
    const off = status({ enabled: false, reachable: false, signed_in: false, shown: false });
    expect(visibility({ status: off })).toMatchObject({ enabled: false, shown: false });
  });

  it("shows them once signed in", () => {
    expect(visibility({ status: status() }).shown).toBe(true);
  });

  it("teams are not shown while quiet, even with teams in the answer", () => {
    const team = {
      id: "00000000-0000-4000-8000-000000000000",
      name: "public",
      public: true,
      created_by: "x",
      created_by_name: null,
      created_at: "2026-11-10",
    };
    expect(teamsView({ status: status({ teams: [team] }), error: null }).shown).toBe(true);
    expect(teamsView({ status: status({ teams: [team], shown: false }), error: null }).shown).toBe(false);
  });
});

describe("collaboration store", () => {
  it("is hidden while checking and while quiet", async () => {
    current = status(QUIET);
    const { result } = renderHook(() => useCollaboration());
    expect(result.current.shown).toBe(false);
    expect(result.current.reason).toBe("Checking the collaboration server…");
    await waitFor(() => expect(result.current.checking).toBe(false));
    expect(result.current.shown).toBe(false);
    expect(result.current.enabled).toBe(true);
    expect(result.current.default_server).toBe(true);
  });

  it("stays shown, disabled with the reason, after a saved token expires", async () => {
    const { result } = renderHook(() => useCollaboration());
    await waitFor(() => expect(result.current.available).toBe(true));
    const reason = QUIET.reason;
    current = status({ ...QUIET, shown: true });
    await act(() => refreshCollaborationStatus());
    expect(result.current.shown).toBe(true);
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toBe(reason);
  });

  it("loads on first subscribe", async () => {
    const { result } = renderHook(() => useCollaboration());
    expect(result.current.available).toBe(false);
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith("/api/collaboration/status", expect.anything());
    expect(result.current.status?.url).toBe("http://127.0.0.1:8002");
  });

  it("every subscriber follows a refresh", async () => {
    const a = renderHook(() => useCollaboration());
    const b = renderHook(() => useCollaboration());
    await waitFor(() => expect(a.result.current.available).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    current = status({ reachable: false, reason: "The server is not answering." });
    await act(() => refreshCollaborationStatus());
    for (const hook of [a, b]) {
      expect(hook.result.current.available).toBe(false);
      expect(hook.result.current.reason).toBe("The server is not answering.");
    }
  });

  it("a failed check disables, with why", async () => {
    current = null;
    const { result } = renderHook(() => useCollaboration());
    await waitFor(() => expect(result.current.checking).toBe(false));
    await waitFor(() => expect(result.current.reason).toMatch(/^Couldn't check/));
    expect(result.current.available).toBe(false);
  });

  it("a failed check keeps the last answer", async () => {
    const { result } = renderHook(() => useCollaboration());
    await waitFor(() => expect(result.current.available).toBe(true));
    current = null;
    await act(() => refreshCollaborationStatus());
    expect(result.current.available).toBe(true);
    expect(result.current.status?.url).toBe("http://127.0.0.1:8002");
    expect(result.current.error).toBe("request failed (502)");
  });

  it("forgets the last answer when the address changes", async () => {
    const { result } = renderHook(() => useCollaboration());
    await waitFor(() => expect(result.current.available).toBe(true));
    let release: (r: Response) => void = () => {};
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => (release = resolve)));
    let pending: Promise<void> = Promise.resolve();
    act(() => {
      pending = refreshCollaborationStatus({ forget: true });
    });
    // While the new address is checked, nothing about the old one stands.
    expect(result.current.status).toBeNull();
    expect(result.current.available).toBe(false);
    expect(result.current.reason).toBe("Checking the collaboration server…");
    await act(async () => {
      release(new Response(JSON.stringify(status({ url: "http://other:8002" })), { status: 200 }));
      await pending;
    });
    expect(result.current.status?.url).toBe("http://other:8002");
  });

  it("a slower, older answer does not overwrite a newer one", async () => {
    let releaseFirst: (r: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () => new Promise<Response>((resolve) => (releaseFirst = resolve)),
    );
    const { result } = renderHook(() => useCollaboration());
    current = status({ reachable: false, reason: "newer" });
    await act(() => refreshCollaborationStatus());
    expect(result.current.reason).toBe("newer");
    await act(async () => {
      releaseFirst(new Response(JSON.stringify(status()), { status: 200 }));
    });
    expect(result.current.reason).toBe("newer");
  });
});
