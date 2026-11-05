import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetCollaborationForTests, type CollaborationStatus } from "@/lib/collaboration";
import { refreshProjects, writeActiveProjectName, type ProjectPublic } from "@/lib/projects";

/*
 * The project dialog's forum choice and the move onto the collaboration
 * server, with the projects page real and only the router and network faked.
 */

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/projects",
}));
vi.mock("@/components/AppTopBar", () => ({
  AppTopBar: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import ProjectsPage from "@/app/projects/page";

const SERVER = "https://obsidian.example.org/vista/api";
const HOSTED = `${SERVER}/git/11111111-2222-4333-8444-555555555555.git`;

function status(overrides: Partial<CollaborationStatus> = {}): CollaborationStatus {
  return {
    configured: true,
    reachable: true,
    reason: null,
    url: SERVER,
    source: "settings",
    server_version: "0.1.0",
    signed_in: true,
    sign_in_required: false,
    sign_in_url: null,
    user: null,
    teams: [],
    team_id: null,
    ...overrides,
  };
}

function row(name: string, forum: string | null = null): ProjectPublic {
  return {
    id: `id-${name}`,
    name,
    description: null,
    system_prompt: null,
    skills: [],
    knowledge_bases: [],
    tools: [],
    usage_limits: {},
    forum_repo_url: forum,
  } as unknown as ProjectPublic;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let collab: CollaborationStatus;
let rows: ProjectPublic[];
let createAnswer: Record<string, unknown> | null;
let hostAnswer: () => Promise<Response>;
const fetchMock = vi.fn();

beforeEach(() => {
  push.mockReset();
  writeActiveProjectName(null);
  resetCollaborationForTests();
  collab = status();
  rows = [];
  createAnswer = null;
  hostAnswer = async () => json({ detail: "unset" }, 500);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    if (url === "/api/collaboration/status") return json(collab);
    if (url.startsWith("/api/projects") && method === "POST" && !url.includes("/forum/host")) {
      const body = JSON.parse(String(init?.body)) as { name: string };
      return json({ ...row(body.name), ...createAnswer }, 201);
    }
    if (url.endsWith("/forum/host") && method === "POST") return hostAnswer();
    if (url === "/api/projects") return json(rows);
    if (url.startsWith("/api/settings")) {
      return json({ install_id: "i1", has_github_token: false, has_gitlab_token: false });
    }
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function created(): { url: string; body: Record<string, unknown> } {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => String(input).startsWith("/api/projects") && init?.method === "POST",
  );
  if (!call) throw new Error("no create was sent");
  return { url: String(call[0]), body: JSON.parse(String(call[1].body)) };
}

async function openCreate(name: string) {
  const user = userEvent.setup();
  render(<ProjectsPage />);
  await user.click(await screen.findByRole("button", { name: /create a project|new project/i }));
  const dialog = await screen.findByRole("dialog", { name: "Create project" });
  await user.type(within(dialog).getByLabelText("Name"), name);
  return { user, dialog };
}

describe("the create dialog's forum choice", () => {
  it("defaults to hosted when signed in, and names the sign-in as the credential", async () => {
    const { user, dialog } = await openCreate("hosted-one");
    const hosted = await within(dialog).findByRole("radio", {
      name: "Hosted on the collaboration server",
    });
    expect(hosted).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "A repository URL" })).not.toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "No forum" })).not.toBeChecked();
    expect(within(dialog).getByText(/Publishes with your collaboration sign-in/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Only you can read it until you publish/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created().url).toBe("/api/projects");
    expect(created().body.forum_repo_url).toBeNull();
  });

  it("sends the repository's URL for the URL choice, with the forge's credential line", async () => {
    const { user, dialog } = await openCreate("url-one");
    await user.click(await within(dialog).findByRole("radio", { name: "A repository URL" }));
    await user.type(
      within(dialog).getByPlaceholderText(/github.com\/your-org/),
      "https://code.example.org/g/forum.git",
    );
    expect(await within(dialog).findByText(/system git’s credentials/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/collaboration sign-in/)).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created().url).toBe("/api/projects");
    expect(created().body.forum_repo_url).toBe("https://code.example.org/g/forum.git");
  });

  it("asks for no hosted forum for 'No forum'", async () => {
    const { user, dialog } = await openCreate("none-one");
    await user.click(await within(dialog).findByRole("radio", { name: "No forum" }));
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created().url).toBe("/api/projects?hosted_forum_wanted=false");
    expect(created().body.forum_repo_url).toBeNull();
  });

  it("shows the forum notice and stays, the project made", async () => {
    createAnswer = { forum_notice: "No hosted forum was made: the server is unreachable." };
    const { user, dialog } = await openCreate("notice-one");
    await user.click(await within(dialog).findByRole("radio", { name: "No forum" }));
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/the server is unreachable/)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("behaves as today when not signed in: a URL field, no choice", async () => {
    collab = status({ signed_in: false, sign_in_required: true, reason: "Sign in." });
    const { user, dialog } = await openCreate("plain-one");
    expect(within(dialog).getByPlaceholderText(/github.com\/your-org/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("radio")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created().url).toBe("/api/projects");
    expect(created().body.forum_repo_url).toBeNull();
  });
});

async function openEdit(project: ProjectPublic) {
  rows = [project];
  // The list is cached across tests; ask again for this one's project.
  await refreshProjects();
  const user = userEvent.setup();
  render(<ProjectsPage />);
  await user.click(await screen.findByRole("button", { name: /edit/i }));
  const dialog = await screen.findByRole("dialog", { name: "Edit project" });
  return { user, dialog };
}

describe("moving a project's forum onto the server", () => {
  const GITHUB = "https://github.com/o/forum.git";

  it("is offered with its explanation when signed in", async () => {
    const { dialog } = await openEdit(row("move-offer", GITHUB));
    expect(
      await within(dialog).findByRole("button", { name: "Host on the collaboration server" }),
    ).toBeEnabled();
    expect(within(dialog).getByText(/Threads are copied.*old repository is left unchanged/)).toBeInTheDocument();
  });

  it("is not offered when not signed in", async () => {
    collab = status({ signed_in: false, sign_in_required: true, reason: "Sign in." });
    const { dialog } = await openEdit(row("move-hidden", GITHUB));
    await within(dialog).findByDisplayValue(GITHUB);
    expect(
      within(dialog).queryByRole("button", { name: "Host on the collaboration server" }),
    ).not.toBeInTheDocument();
  });

  it("shows progress, then the hosted state instead of a URL", async () => {
    let finish: (r: Response) => void = () => {};
    hostAnswer = () => new Promise<Response>((resolve) => (finish = resolve));
    const { user, dialog } = await openEdit(row("move-ok", GITHUB));
    await user.click(
      await within(dialog).findByRole("button", { name: "Host on the collaboration server" }),
    );
    expect(await within(dialog).findByRole("button", { name: "Moving the forum…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();

    finish(json(row("move-ok", HOSTED)));
    expect(await within(dialog).findByTestId("forum-hosted")).toHaveTextContent(
      "Hosted on the collaboration server",
    );
    expect(within(dialog).queryByDisplayValue(HOSTED)).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "Host on the collaboration server" }),
    ).not.toBeInTheDocument();
  });

  it("shows the server's failure and stays on the old forum", async () => {
    hostAnswer = async () =>
      json({ detail: "The forum was not moved; the project still uses " + GITHUB + ": refused." }, 400);
    const { user, dialog } = await openEdit(row("move-fail", GITHUB));
    await user.click(
      await within(dialog).findByRole("button", { name: "Host on the collaboration server" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/The forum was not moved/);
    expect(within(dialog).getByDisplayValue(GITHUB)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Host on the collaboration server" }),
    ).toBeEnabled();
  });

  it("shows an already hosted project as hosted, not as a URL to edit", async () => {
    const { dialog } = await openEdit(row("already-hosted", HOSTED));
    expect(await within(dialog).findByTestId("forum-hosted")).toBeInTheDocument();
    expect(within(dialog).queryByDisplayValue(HOSTED)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/collaboration sign-in/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Only you can read it until you publish/)).toBeInTheDocument();
  });
});
