import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SlackSection } from "@/components/SlackSection";
import type { SlackConfig, SlackLink } from "@/lib/slack";

vi.mock("@/lib/projects", () => ({ useProjects: () => ({ projects: [] }) }));

const config: SlackConfig = {
  enabled: true,
  has_bot_token: true,
  has_app_token: true,
  secret_store_available: true,
  secret_store_problem: null,
  allowed_team_ids: ["T1"],
  allowed_channel_ids: [],
  max_nodes: 4,
  max_walltime_minutes: 60,
  max_jobs_per_day: 5,
  locked: false,
  locked_at: null,
  locked_by: null,
  connection: "connected",
  connection_detail: null,
};

const link = (over: Partial<SlackLink> = {}): SlackLink => ({
  id: "l1",
  team_id: "T1",
  slack_user_id: "U1",
  collab_name: null,
  project_id: null,
  created_at: "2026-11-25T00:00:00Z",
  ...over,
});

function mockFetch(links: SlackLink[]) {
  const fn = vi.fn(async (_url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "PUT") return new Response(JSON.stringify(link({ locked: false })), { status: 200 });
    return new Response(JSON.stringify(links), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("SlackSection", () => {
  it("hosted: shows state, limits and the note, with no editable controls", async () => {
    mockFetch([]);
    render(<SlackSection config={{ ...config, server_managed: true }} onChange={vi.fn()} />);
    expect(screen.getByText(/operator manages the Slack app/)).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText(/Slack is on/)).toBeInTheDocument();
    expect(screen.getByText(/Most nodes per job: 4/)).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByLabelText("Bot token")).toBeNull();
    expect(screen.queryByLabelText("Allowed workspace IDs")).toBeNull();
    expect(screen.queryByLabelText("Most nodes per job")).toBeNull();
    await waitFor(() => expect(screen.getByText("None yet.")).toBeInTheDocument());
  });

  it("desktop: keeps the editable controls", async () => {
    mockFetch([]);
    render(<SlackSection config={config} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Bot token")).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed workspace IDs")).toBeInTheDocument();
    expect(screen.getByLabelText("Most nodes per job")).toBeInTheDocument();
    expect(screen.getAllByRole("switch").length).toBeGreaterThan(0);
    expect(screen.queryByText(/operator manages/)).toBeNull();
    await waitFor(() => expect(screen.getByText("None yet.")).toBeInTheDocument());
  });

  it("a locked link shows Unlock, which sends locked: false", async () => {
    const fetchFn = mockFetch([link({ locked: true }), link({ id: "l2", slack_user_id: "U2" })]);
    render(<SlackSection config={{ ...config, server_managed: true }} onChange={vi.fn()} />);
    const unlock = await screen.findByRole("button", { name: "Unlock" });
    expect(screen.getAllByRole("button", { name: "Unlock" })).toHaveLength(1);
    await userEvent.click(unlock);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Unlock" })).toBeNull());
    const put = fetchFn.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(String(put[0])).toContain("/slack/links/l1");
    expect(JSON.parse(String(put[1]!.body))).toEqual({ locked: false });
  });
});
