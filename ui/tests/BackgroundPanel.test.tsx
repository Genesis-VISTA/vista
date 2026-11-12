import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BackgroundPanel, { CLOSED_POLL_MS, OPEN_POLL_MS } from "@/components/BackgroundPanel";
import {
  formatRuntime,
  liveCount,
  runtimeAt,
  taskState,
  taskSubject,
  type BackgroundTask,
} from "@/lib/background-tasks";

const CHAT = "00000000-0000-4000-8000-000000000003";

const command = (over: Partial<BackgroundTask> = {}): BackgroundTask => ({
  id: "00000000-0000-4000-8000-0000000000c1",
  label: "bg-1",
  kind: "command",
  state: "running",
  command: "python fit.py",
  log_path: "/mnt/data/tasks/c1.log",
  created_at: "2026-11-12T10:00:00Z",
  runtime_s: 62,
  ...over,
});

const watch = (over: Partial<BackgroundTask> = {}): BackgroundTask => ({
  id: "00000000-0000-4000-8000-0000000000w2",
  label: "watch-2",
  kind: "job_watch",
  state: "watching",
  job_id: "4242",
  cluster: "odo",
  last_job_state: "QUEUED",
  created_at: "2026-11-12T10:00:00Z",
  runtime_s: 30,
  ...over,
});

/** A fetch that answers the tasks routes from `tasks`, and records every request. */
function serve(tasks: () => BackgroundTask[]) {
  const requests: Array<{ url: string; method: string }> = [];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    requests.push({ url, method });
    if (url.startsWith("/api/chat/tasks/stop")) {
      return new Response(JSON.stringify({ ...tasks()[0], state: "stopped" }), { status: 200 });
    }
    if (url.startsWith("/api/chat/tasks/log")) {
      return new Response("step 1\nstep 2\n", { status: 200, headers: { "content-type": "text/plain" } });
    }
    return new Response(JSON.stringify(tasks()), { status: 200 });
  });
  const reads = () => requests.filter((r) => r.url.startsWith("/api/chat/tasks?")).length;
  return { requests, reads, fetchMock };
}

function panel(props: Partial<React.ComponentProps<typeof BackgroundPanel>> = {}) {
  const onTasksChange = vi.fn();
  const utils = render(
    <BackgroundPanel
      projectName="molten-salt"
      chatSessionId={CHAT}
      active
      refreshKey={0}
      onTasksChange={onTasksChange}
      {...props}
    />,
  );
  return { onTasksChange, ...utils };
}

describe("BackgroundPanel", () => {
  it("lists each task with its state and runtime, and tells the page", async () => {
    serve(() => [command(), watch()]);
    const { onTasksChange } = panel();

    expect(await screen.findByText("bg-1")).toBeInTheDocument();
    expect(screen.getByText("python fit.py")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("1m 2s")).toBeInTheDocument();
    expect(screen.getByText("watch-2")).toBeInTheDocument();
    expect(screen.getByText("Job 4242 on Odo")).toBeInTheDocument();
    expect(screen.getByText("watching · queued")).toBeInTheDocument();
    await waitFor(() => expect(onTasksChange).toHaveBeenLastCalledWith([command(), watch()]));
  });

  it("says what a paused watch needs", async () => {
    serve(() => [watch({ state: "paused", paused_reason: "Reconnect Odo in Settings" })]);
    panel();

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Paused: Reconnect Odo in Settings",
    );
  });

  it("stops a live task, and offers no stop for one that ended", async () => {
    const { requests } = serve(() => [command(), command({ id: "x2", label: "bg-2", state: "exited", exit_status: 7 })]);
    panel();
    await screen.findByText("bg-2");

    const stops = screen.getAllByRole("button", { name: "Stop" });
    expect(stops).toHaveLength(1);
    expect(screen.getByText("exited 7")).toBeInTheDocument();
    await userEvent.click(stops[0]);

    await waitFor(() =>
      expect(requests.find((r) => r.method === "POST")?.url).toBe(
        `/api/chat/tasks/stop?project_name=molten-salt&chat_session_id=${CHAT}&task_id=${command().id}`,
      ),
    );
  });

  it("opens a command's log in the file preview, from the tail route", async () => {
    const { requests } = serve(() => [command()]);
    panel();
    await screen.findByText("bg-1");

    await userEvent.click(screen.getByRole("button", { name: "Log" }));

    expect(await screen.findByText(/step 2/)).toBeInTheDocument();
    expect(requests.some((r) => r.url.startsWith("/api/chat/tasks/log?"))).toBe(true);
    expect(screen.getByText("The end of /mnt/data/tasks/c1.log")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: "Log" })).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Hide log" })).toBeInTheDocument();
  });

  it("offers no log for a watch", async () => {
    serve(() => [watch()]);
    panel();
    await screen.findByText("watch-2");

    expect(screen.queryByRole("button", { name: "Log" })).not.toBeInTheDocument();
  });

  it("reads again when a run event bumps its key", async () => {
    const { reads } = serve(() => [command()]);
    const { rerender, onTasksChange } = panel({ active: false });
    await waitFor(() => expect(reads()).toBe(1));

    rerender(
      <BackgroundPanel
        projectName="molten-salt"
        chatSessionId={CHAT}
        active={false}
        refreshKey={1}
        onTasksChange={onTasksChange}
      />,
    );

    await waitFor(() => expect(reads()).toBe(2));
  });

  it("shows nothing to do for a conversation without tasks", async () => {
    serve(() => []);
    panel();

    expect(await screen.findByText("No background tasks in this conversation.")).toBeInTheDocument();
  });
});

describe("BackgroundPanel polling", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reads every 3 s while open, and advances a live runtime every second", async () => {
    const { reads } = serve(() => [command()]);
    panel();
    await screen.findByText("1m 2s");
    const first = reads();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText("1m 4s")).toBeInTheDocument();
    expect(reads()).toBe(first);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(OPEN_POLL_MS - 2000);
    });
    expect(reads()).toBe(first + 1);
  });

  it("reads every 15 s while closed with something live, and not at all with nothing live", async () => {
    let tasks = [command()];
    const { reads } = serve(() => tasks);
    panel({ active: false });
    await waitFor(() => expect(reads()).toBe(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(OPEN_POLL_MS);
    });
    expect(reads()).toBe(1);
    tasks = [command({ state: "exited", exit_status: 0 })];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLOSED_POLL_MS);
    });
    expect(reads()).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLOSED_POLL_MS * 2);
    });
    expect(reads()).toBe(2); // nothing live: no more reading
  });
});

describe("background tasks, read for the page", () => {
  it("formats runtimes as a person would say them", () => {
    expect(formatRuntime(4)).toBe("4s");
    expect(formatRuntime(242)).toBe("4m 2s");
    expect(formatRuntime(3 * 3600 + 120)).toBe("3h 2m");
  });

  it("advances only a live task's runtime", () => {
    expect(runtimeAt(command({ runtime_s: 10 }), 1_000, 6_000)).toBe(15);
    expect(runtimeAt(command({ runtime_s: 10, state: "exited" }), 1_000, 6_000)).toBe(10);
  });

  it("counts running commands and active watches", () => {
    expect(
      liveCount([command(), watch({ state: "paused" }), command({ state: "stopped" }), watch({ state: "ended" })]),
    ).toBe(2);
  });

  it("says each state in words", () => {
    expect(taskState(command({ state: "timed_out" }))).toBe("timed out");
    expect(taskState(watch({ state: "ended", last_job_state: "COMPLETED" }))).toBe("ended · completed");
    expect(taskSubject(watch({ cluster: "perlmutter", job_id: "9" }))).toBe("Job 9 on Perlmutter");
  });
});
