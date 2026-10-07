import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  STATUS_POLL_MS,
  refreshChatRunStatus,
  resetChatRunStatusForTests,
  summarizeChatRuns,
  useChatRunStatus,
  type ChatRunStatusEntry,
  type ChatRunStatusKind,
} from "@/lib/chat-run-status";

function entry(id: string, status: ChatRunStatusKind, unseen = false): ChatRunStatusEntry {
  return { chat_session_id: id, status, unseen };
}

type Answer = ChatRunStatusEntry[] | "fail" | Promise<Response>;
let answers: Answer[];
const fetchMock = vi.fn();
let visibility: DocumentVisibilityState = "visible";

function respond(body: ChatRunStatusEntry[]): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  resetChatRunStatusForTests();
  answers = [];
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => {
    const next = answers.shift() ?? [];
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

const urls = () => fetchMock.mock.calls.map((c) => String(c[0]));

describe("useChatRunStatus", () => {
  it("fetches once on mount, shared between subscribers", async () => {
    answers = [[entry("a", "working"), entry("b", "done", true)]];
    const list = renderHook(() => useChatRunStatus("molten-salt"));
    const rail = renderHook(() => useChatRunStatus("molten-salt"));
    expect(list.result.current.entries).toBeNull();
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(urls()).toEqual(["/api/chat/runs/status?project_name=molten-salt"]);
    for (const view of [list, rail]) {
      expect(view.result.current.entries?.map((e) => e.status)).toEqual(["working", "done"]);
      expect(view.result.current.byId.get("b")).toMatchObject({ status: "done", unseen: true });
      expect(view.result.current.byId.has("c")).toBe(false);
    }
  });

  it("polls every three seconds while visible, and not while hidden", async () => {
    renderHook(() => useChatRunStatus("p"));
    await flush();
    await advance(STATUS_POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    visibility = "hidden";
    await advance(STATUS_POLL_MS * 5);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    visibility = "visible";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3); // checks on return, without waiting for the next tick
  });

  it("stops polling once nothing is subscribed", async () => {
    const { unmount } = renderHook(() => useChatRunStatus("p"));
    await flush();
    unmount();
    await advance(STATUS_POLL_MS * 4);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows a change on the next poll", async () => {
    answers = [[entry("a", "working")], [entry("a", "needs_you")], [entry("a", "done", true)]];
    const { result } = renderHook(() => useChatRunStatus("p"));
    await flush();
    expect(result.current.byId.get("a")?.status).toBe("working");
    await advance(STATUS_POLL_MS);
    expect(result.current.byId.get("a")?.status).toBe("needs_you");
    await advance(STATUS_POLL_MS);
    expect(result.current.byId.get("a")).toMatchObject({ status: "done", unseen: true });
  });

  it("keeps the last answer through a failure and recovers after it", async () => {
    answers = [[entry("a", "working")], "fail", [entry("a", "done", true)]];
    const { result } = renderHook(() => useChatRunStatus("p"));
    await flush();
    await advance(STATUS_POLL_MS);
    expect(result.current.failing).toBe(true);
    expect(result.current.byId.get("a")?.status).toBe("working");
    await advance(STATUS_POLL_MS);
    expect(result.current.failing).toBe(false);
    expect(result.current.byId.get("a")?.status).toBe("done");
  });

  it("refresh asks again at once", async () => {
    const { result } = renderHook(() => useChatRunStatus("p"));
    await flush();
    await act(async () => {
      await result.current.refresh();
    });
    await act(async () => {
      await refreshChatRunStatus("p");
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("does nothing without a project", async () => {
    const { result } = renderHook(() => useChatRunStatus(null));
    await flush();
    await advance(STATUS_POLL_MS * 2);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.entries).toBeNull();
  });

  it("keeps projects apart", async () => {
    answers = [[entry("a", "working")], [entry("z", "failed", true)]];
    const one = renderHook(() => useChatRunStatus("one"));
    await flush();
    const two = renderHook(() => useChatRunStatus("two"));
    await flush();
    expect(one.result.current.byId.has("a")).toBe(true);
    expect(two.result.current.byId.has("a")).toBe(false);
    expect(two.result.current.byId.get("z")?.status).toBe("failed");
  });

  it("an older, slower answer does not overwrite a newer one", async () => {
    const { result } = renderHook(() => useChatRunStatus("p"));
    await flush();
    let releaseOld!: (r: Response) => void;
    answers = [new Promise<Response>((r) => (releaseOld = r)), [entry("a", "done", true)]];
    await act(async () => {
      void refreshChatRunStatus("p");
      await refreshChatRunStatus("p");
    });
    expect(result.current.byId.get("a")?.status).toBe("done");
    await act(async () => {
      releaseOld(respond([entry("a", "working")]));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.byId.get("a")?.status).toBe("done");
  });
});

describe("summarizeChatRuns", () => {
  it("has no summary for no conversations", () => {
    expect(summarizeChatRuns(null)).toEqual({ status: null, needsYou: [] });
    expect(summarizeChatRuns([])).toEqual({ status: null, needsYou: [] });
  });

  it("ranks needs you over failed or interrupted over done", () => {
    const summary = (...e: ChatRunStatusEntry[]) => summarizeChatRuns(e).status;
    expect(summary(entry("a", "done", true), entry("b", "needs_you"))).toBe("needs_you");
    expect(summary(entry("a", "done", true), entry("b", "failed", true))).toBe("failed");
    expect(summary(entry("a", "done", true), entry("b", "interrupted", true))).toBe(
      "interrupted",
    );
    expect(summary(entry("a", "working"), entry("b", "done", true))).toBe("done");
    expect(summary(entry("a", "working"))).toBe("working");
  });

  it("lists every conversation that needs the researcher", () => {
    const summary = summarizeChatRuns([
      entry("a", "needs_you"),
      entry("b", "working"),
      entry("c", "needs_you"),
    ]);
    expect(summary.needsYou).toEqual(["a", "c"]);
  });
});
