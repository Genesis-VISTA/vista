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
  default: ({ path, src }: { path: string; src?: string }) => (
    <div data-testid="preview" data-src={src}>
      {path}
    </div>
  ),
}));
const { tourPosition, tourReport } = vi.hoisted(() => ({
  tourPosition: { current: null as { step: string; subStep: string } | null },
  tourReport: vi.fn(),
}));
vi.mock("@/components/TourProvider", () => ({
  useTour: () => ({ position: tourPosition.current, report: tourReport }),
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
  tourPosition.current = null;
  tourReport.mockReset();
  window.sessionStorage.clear();
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

  it("reopens the folders that were open when the researcher comes back", async () => {
    outputs = [
      { name: "folder-a/nestd/deep file.txt", size: 5, modifiedAt: "2026-01-01T00:00:00Z", shown_in: null },
      { name: "folder-b/other.txt", size: 5, modifiedAt: "2026-01-01T00:00:00Z", shown_in: null },
    ];
    const folder = (name: string) => screen.getByRole("button", { name });
    const first = render(<FilesPage />);
    await advance(0);
    act(() => folder("folder-a").click());
    act(() => folder("nestd").click());
    // Off to a chat: the page goes away.
    first.unmount();

    render(<FilesPage />);
    await advance(0);
    expect(folder("folder-a")).toHaveAttribute("aria-expanded", "true");
    expect(folder("nestd")).toHaveAttribute("aria-expanded", "true");
    expect(folder("folder-b")).toHaveAttribute("aria-expanded", "false");
  });
});

describe("Files page during the tour's files step", () => {
  const exampleNames = ["summary.md", "risk-scores.svg", "risks.csv"];

  it("lists no example files when no tour is running", async () => {
    render(<FilesPage />);
    await advance(0);
    for (const name of exampleNames) expect(screen.queryByText(name)).not.toBeInTheDocument();
    expect(document.querySelector("[data-tour]")).toBeNull();
  });

  it("lists no example files on another step", async () => {
    tourPosition.current = { step: "skills", subStep: "open-skill" };
    render(<FilesPage />);
    await advance(0);
    for (const name of exampleNames) expect(screen.queryByText(name)).not.toBeInTheDocument();
  });

  it("lists the examples with an Example badge and no actions, and they are gone after the step", async () => {
    tourPosition.current = { step: "files", subStep: "open-examples" };
    const view = render(<FilesPage />);
    await advance(0);
    for (const name of exampleNames) expect(screen.getByText(name)).toBeInTheDocument();
    expect(screen.getAllByText("Example")).toHaveLength(3);
    expect(screen.queryByRole("link", { name: /Download/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Show in chat" })).not.toBeInTheDocument();
    expect(document.querySelector('[data-tour="files:open-examples"]')).not.toBeNull();

    // Both opened: the examples stay while the review coachmark waits for Next.
    tourPosition.current = { step: "files", subStep: "review" };
    view.rerender(<FilesPage />);
    for (const name of exampleNames) expect(screen.getByText(name)).toBeInTheDocument();
    expect(document.querySelector('[data-tour="files:review"]')).not.toBeNull();

    tourPosition.current = { step: "hpc", subStep: "open-card" };
    view.rerender(<FilesPage />);
    for (const name of exampleNames) expect(screen.queryByText(name)).not.toBeInTheDocument();
  });

  it("keeps real files' actions, and asks the backend for nothing about an example", async () => {
    tourPosition.current = { step: "files", subStep: "open-examples" };
    search.current = "kind=outputs&path=summary.md";
    outputs = [{ name: "real.txt", size: 5, modifiedAt: "2026-01-02T00:00:00Z", shown_in: null }];
    render(<FilesPage />);
    await advance(0);
    expect(screen.getByRole("link", { name: "Download real.txt" })).toBeInTheDocument();
    // The preview gets the example's own content, a data URL.
    expect(screen.getByTestId("preview")).toHaveAttribute("data-src", expect.stringMatching(/^data:text\/markdown/));
    const requested = fetchMock.mock.calls.map((c) => decodeURIComponent(String(c[0])));
    expect(requested.filter((url) => url.includes("summary.md") || url.includes("risk"))).toEqual([]);
  });

  it("reports the example image and the example report when they are opened", async () => {
    tourPosition.current = { step: "files", subStep: "open-examples" };
    search.current = "kind=outputs&path=risk-scores.svg";
    const view = render(<FilesPage />);
    await advance(0);
    expect(tourReport).toHaveBeenCalledExactlyOnceWith("example-image-opened");
    expect(screen.getByTestId("preview")).toHaveAttribute("data-src", expect.stringMatching(/^data:image\/svg\+xml/));

    search.current = "kind=outputs&path=summary.md";
    view.rerender(<FilesPage />);
    await advance(0);
    expect(tourReport).toHaveBeenLastCalledWith("example-md-opened");
  });

  it("clears an example's selection when the step ends", async () => {
    tourPosition.current = { step: "files", subStep: "open-examples" };
    search.current = "kind=outputs&path=summary.md";
    const view = render(<FilesPage />);
    await advance(0);
    expect(replace).not.toHaveBeenCalled();

    tourPosition.current = { step: "hpc", subStep: "open-card" };
    view.rerender(<FilesPage />);
    await advance(0);
    expect(replace).toHaveBeenCalledExactlyOnceWith("/files");
  });

  it("does not take a real file of the same name for an example", async () => {
    tourPosition.current = { step: "files", subStep: "open-examples" };
    search.current = "kind=outputs&path=summary.md";
    outputs = [{ name: "summary.md", size: 5, modifiedAt: "2026-01-02T00:00:00Z", shown_in: null }];
    render(<FilesPage />);
    await advance(0);
    expect(screen.getAllByText("Example")).toHaveLength(2);
    expect(screen.getByTestId("preview")).not.toHaveAttribute("data-src");
    expect(tourReport).not.toHaveBeenCalled();
  });
});
