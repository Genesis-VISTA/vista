import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FilesPage from "@/app/files/page";
import {
  STATUS_POLL_MS,
  resetChatRunStatusForTests,
  type ChatRunStatusEntry,
  type ChatRunStatusKind,
} from "@/lib/chat-run-status";
import { finishedChats, useOnChatRunFinished } from "@/lib/use-chat-run-finished";

/*
 * Reloading the Files listing when a turn finishes. The status poll and the
 * listing are the only things faked: both come from a stubbed fetch, and the
 * status store is the real one, reset between tests.
 */

const { replace, search } = vi.hoisted(() => ({
  replace: vi.fn(),
  search: { current: "" },
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search.current),
  usePathname: () => "/files",
}));
vi.mock("@/lib/projects", () => ({
  useActiveProject: () => ({ name: "molten-salt" }),
}));
vi.mock("@/components/AppTopBar", () => ({ AppTopBar: () => null }));
vi.mock("@/components/FilePreview", () => ({
  default: ({ path }: { path: string }) => <div data-testid="preview">{path}</div>,
}));

function entry(id: string, status: ChatRunStatusKind): ChatRunStatusEntry {
  return { chat_session_id: id, status, unseen: false };
}

let status: ChatRunStatusEntry[];
let outputs: unknown[];
const fetchMock = vi.fn();

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const listingCalls = () =>
  fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/files/outputs")).length;

beforeEach(() => {
  vi.useFakeTimers();
  resetChatRunStatusForTests();
  status = [];
  outputs = [];
  search.current = "";
  replace.mockReset();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/chat/runs/status")) return json(status);
    if (url.startsWith("/api/files/outputs")) return json(outputs);
    if (url.startsWith("/api/files/uploads")) return json([]);
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  resetChatRunStatusForTests();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("finishedChats", () => {
  it("counts a conversation that left working or needs_you, or vanished", () => {
    expect(finishedChats([entry("a", "working")], [entry("a", "done")])).toEqual(["a"]);
    expect(finishedChats([entry("a", "needs_you")], [entry("a", "failed")])).toEqual(["a"]);
    expect(finishedChats([entry("a", "working")], [])).toEqual(["a"]);
  });

  it("ignores a conversation that is still active, or newly started", () => {
    expect(finishedChats([entry("a", "working")], [entry("a", "needs_you")])).toEqual([]);
    expect(finishedChats([], [entry("a", "working")])).toEqual([]);
    expect(finishedChats(null, [entry("a", "done")])).toEqual([]);
  });
});

describe("useOnChatRunFinished", () => {
  it("fires once when a working conversation finishes, not on the first answer", async () => {
    status = [entry("a", "working")];
    const onFinished = vi.fn();
    renderHook(() => useOnChatRunFinished("molten-salt", onFinished));
    await advance(0);
    expect(onFinished).not.toHaveBeenCalled();

    status = [entry("a", "done")];
    await advance(STATUS_POLL_MS);
    expect(onFinished).toHaveBeenCalledTimes(1);

    await advance(STATUS_POLL_MS);
    expect(onFinished).toHaveBeenCalledTimes(1);
  });
});

describe("Files page", () => {
  it("reloads the listing when a conversation leaves working", async () => {
    status = [entry("a", "working")];
    render(<FilesPage />);
    await advance(0);
    expect(listingCalls()).toBe(1);
    expect(screen.queryByText("plot.png")).not.toBeInTheDocument();

    outputs = [{ name: "plot.png", size: 10, modifiedAt: "2026-01-01T00:00:00Z", shown_in: null }];
    status = [entry("a", "done")];
    await advance(STATUS_POLL_MS);
    await advance(0);

    expect(listingCalls()).toBe(2);
    expect(screen.getByText("plot.png")).toBeInTheDocument();
  });

  it("does not reload while a conversation keeps working", async () => {
    status = [entry("a", "working")];
    render(<FilesPage />);
    await advance(0);
    await advance(STATUS_POLL_MS * 3);
    expect(listingCalls()).toBe(1);
  });

  it("previews the file named in the URL and links Shown in only when set", async () => {
    search.current = "kind=outputs&path=run1%2Fplot.png";
    outputs = [
      {
        name: "run1/plot.png",
        size: 10,
        modifiedAt: "2026-01-01T00:00:00Z",
        shown_in: { chat_session_id: "c1", chat_title: "My chat", run_id: "r1", tool_call_id: "t1" },
      },
      { name: "run1/other.txt", size: 5, modifiedAt: "2026-01-01T00:00:00Z", shown_in: null },
    ];
    render(<FilesPage />);
    await advance(0);

    expect(screen.getByTestId("preview")).toHaveTextContent("run1/plot.png");
    const [show, ...others] = screen.getAllByRole("link", { name: "Show in chat" });
    expect(others).toHaveLength(0);
    expect(show).toHaveAttribute("href", "/?chat=c1&run=r1&step=t1");
  });

  it("keeps folders the researcher opened open when a file inside them is selected", async () => {
    outputs = [
      { name: "folder-a/nestd/deep file.txt", size: 5, modifiedAt: "2026-01-01T00:00:00Z", shown_in: null },
    ];
    const view = render(<FilesPage />);
    await advance(0);
    const folder = (name: string) => screen.getByRole("button", { name });

    act(() => folder("folder-a").click());
    act(() => folder("nestd").click());
    act(() => screen.getByRole("button", { name: /^deep file\.txt/ }).click());
    // The router is mocked, so feed the replaced URL back as the page would see it.
    search.current = String(replace.mock.calls.at(-1)?.[0]).split("?")[1];
    view.rerender(<FilesPage />);
    await advance(0);

    expect(folder("folder-a")).toHaveAttribute("aria-expanded", "true");
    expect(folder("nestd")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("preview")).toHaveTextContent("folder-a/nestd/deep file.txt");
  });
});
