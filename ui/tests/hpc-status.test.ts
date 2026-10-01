import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  POLL_MS,
  STALE_MS,
  recheckHpcStatus,
  resetHpcStatusForTests,
  useHpcStatus,
  type HpcClusterStatus,
  type HpcState,
} from "@/lib/hpc-status";

function cluster(name: HpcClusterStatus["cluster"], state: HpcState): HpcClusterStatus {
  const ok = { ok: true, reason: null, message: "ok" };
  return {
    cluster: name,
    state,
    checked_at: "2026-09-25T15:00:00Z",
    checks: {
      facility: ok,
      credential: ok,
      globus: name === "perlmutter" || name === "lux" ? null : ok,
      settings: ok,
    },
  };
}

let answers: Array<HpcClusterStatus[] | "fail" | Promise<Response>>;
const fetchMock = vi.fn();

function respond(body: HpcClusterStatus[]): Response {
  return new Response(JSON.stringify({ clusters: body }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

let visibility: DocumentVisibilityState = "visible";

beforeEach(() => {
  vi.useFakeTimers();
  resetHpcStatusForTests();
  answers = [];
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => {
    const next = answers.shift() ?? [cluster("odo", "ready")];
    if (next === "fail") return new Response("{}", { status: 502 });
    if (next instanceof Promise) return next;
    return respond(next);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function urls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe("useHpcStatus", () => {
  it("fetches once on mount, shared between subscribers", async () => {
    answers = [[cluster("frontier", "ready"), cluster("odo", "rejected")]];
    const a = renderHook(() => useHpcStatus());
    const b = renderHook(() => useHpcStatus());
    expect(a.result.current.clusters).toBeNull(); // the rail shows Checking
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a.result.current.clusters?.map((c) => c.state)).toEqual(["ready", "rejected"]);
    expect(b.result.current.clusters?.map((c) => c.state)).toEqual(["ready", "rejected"]);
  });

  it("polls every five minutes while visible, and not while hidden", async () => {
    renderHook(() => useHpcStatus());
    await flush();
    await advance(POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    visibility = "hidden";
    await advance(POLL_MS * 2);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    visibility = "visible";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3); // overdue, so it checks on return
  });

  it("carries Lux's entry and rechecks it alone", async () => {
    answers = [[cluster("frontier", "ready"), cluster("lux", "ready")]];
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    const entry = result.current.clusters?.find((c) => c.cluster === "lux");
    expect(entry?.state).toBe("ready");
    expect(entry?.status.checks.globus).toBeNull();
    await act(async () => {
      await result.current.recheck("lux");
    });
    expect(urls()).toContain("/api/users/me/hpc-status?fresh=true&cluster=lux");
  });

  it("recheck asks for fresh results, for one cluster or all", async () => {
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    await act(async () => {
      await result.current.recheck("odo");
    });
    await act(async () => {
      await recheckHpcStatus();
    });
    expect(urls()).toEqual([
      "/api/users/me/hpc-status",
      "/api/users/me/hpc-status?fresh=true&cluster=odo",
      "/api/users/me/hpc-status?fresh=true",
    ]);
  });

  it("marks only the rechecked cluster as rechecking", async () => {
    answers = [[cluster("frontier", "ready"), cluster("odo", "ready")]];
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    let release!: (r: Response) => void;
    answers = [new Promise<Response>((r) => (release = r))];
    act(() => {
      void result.current.recheck("odo");
    });
    expect(result.current.clusters?.map((c) => [c.cluster, c.rechecking])).toEqual([
      ["frontier", false],
      ["odo", true],
    ]);
    // The last known state stays visible while it runs.
    expect(result.current.clusters?.[1].state).toBe("ready");
    await act(async () => {
      release(respond([cluster("frontier", "ready"), cluster("odo", "rejected")]));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.clusters?.[1]).toMatchObject({ state: "rejected", rechecking: false });
  });

  it("keeps the last result through a failure, then shows Couldn't verify once stale", async () => {
    answers = [[cluster("odo", "ready")], "fail", "fail", "fail", "fail"];
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    const firstSuccess = result.current.lastSuccessAt;

    await advance(POLL_MS);
    expect(result.current.failing).toBe(true);
    expect(result.current.clusters?.[0].state).toBe("ready");
    expect(result.current.lastSuccessAt).toBe(firstSuccess);

    await advance(STALE_MS);
    expect(result.current.clusters?.[0].state).toBe("unverifiable");
  });

  it("a later success clears staleness", async () => {
    answers = [[cluster("odo", "ready")], "fail", "fail", "fail", "fail", [cluster("odo", "ready")]];
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    await advance(STALE_MS + POLL_MS);
    expect(result.current.clusters?.[0].state).toBe("unverifiable");
    await advance(POLL_MS);
    expect(result.current.failing).toBe(false);
    expect(result.current.clusters?.[0].state).toBe("ready");
  });

  it("says unavailable when the very first request fails", async () => {
    answers = ["fail"];
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    expect(result.current.clusters).toBeNull();
    expect(result.current.unavailable).toBe(true);
  });

  it("an older, slower answer does not overwrite a newer one", async () => {
    const { result } = renderHook(() => useHpcStatus());
    await flush();
    let releaseOld!: (r: Response) => void;
    answers = [
      new Promise<Response>((r) => (releaseOld = r)),
      [cluster("odo", "rejected")],
    ];
    await act(async () => {
      void recheckHpcStatus();
      await recheckHpcStatus("odo");
    });
    await act(async () => {
      releaseOld(respond([cluster("odo", "ready")]));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.clusters?.[0].state).toBe("rejected");
  });
});
