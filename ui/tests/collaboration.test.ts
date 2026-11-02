import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  availability,
  refreshCollaborationStatus,
  resetCollaborationForTests,
  useCollaboration,
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
    signed_in: true,
    sign_in_required: false,
    sign_in_url: null,
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

describe("collaboration store", () => {
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
