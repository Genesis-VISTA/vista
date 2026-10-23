import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetCollaborationForTests, type CollaborationStatus } from "@/lib/collaboration";
import {
  importSummary,
  publishedFirst,
  projectImportAction,
  projectSharingLabel,
  publishProject,
  type ProjectImported,
  type RemoteProject,
} from "@/lib/project-sharing";
import { fromPublic, refreshProjects, type Project, type ProjectPublic } from "@/lib/projects";

/*
 * Projects on the collaboration server, from the UI's side: the labels, the
 * refusals the proxies pass through, and the projects page's Publish and
 * Import with the project store real and only the router and network faked.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/projects",
}));
vi.mock("@/components/AppTopBar", () => ({
  AppTopBar: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import ProjectsPage from "@/app/projects/page";

const SERVER = "http://127.0.0.1:8002";
const ITEM = "22222222-2222-4222-8222-222222222222";
const KB_ITEM = "44444444-4444-4444-8444-444444444444";

function status(overrides: Partial<CollaborationStatus> = {}): CollaborationStatus {
  return {
    configured: true,
    reachable: true,
    reason: null,
    url: SERVER,
    source: "settings",
    server_version: "0.1.0",
    ...overrides,
  };
}

function row(name: string, overrides: Partial<ProjectPublic> = {}): ProjectPublic {
  return {
    id: `id-${name}`,
    name,
    description: null,
    system_prompt: null,
    skills: [],
    knowledge_bases: [],
    forum_repo_url: null,
    tools: [],
    usage_limits: {},
    origin_server: null,
    remote_id: null,
    remote_version: null,
    modified: true,
    ...overrides,
  };
}

function project(overrides: Partial<ProjectPublic> = {}): Project {
  return fromPublic(row("salt", overrides));
}

function remote(overrides: Partial<RemoteProject> = {}): RemoteProject {
  return {
    id: ITEM,
    name: "salt-lab",
    head_version: 1,
    created_by_name: "Bea",
    created_at: "2026-10-01T00:00:00+00:00",
    published_by_name: "Bea",
    published_at: "2026-10-01T00:00:00+00:00",
    manifest: {
      format: 2,
      name: "salt-lab",
      description: "Bea's molten salt project.",
      system_prompt: null,
      tools: [],
      usage_limits: {},
      forum_repo_url: null,
      skills: [{ name: "vllm-lux", bundled: false, remote_id: ITEM, version: 1 }],
      knowledge_bases: [{ slug: "salt-papers", remote_id: KB_ITEM, version: 1 }],
    },
    local: null,
    name_taken: false,
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
let routes: Record<string, Handler>;
let rows: ProjectPublic[];
const fetchMock = vi.fn();

beforeEach(() => {
  resetCollaborationForTests();
  rows = [];
  routes = {
    "GET /api/collaboration/status": () => json(status()),
    "GET /api/projects": () => json(rows),
    "GET /api/collaboration/projects?manifests=false": () => json([]),
  };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    const handler = routes[`${method} ${url}`];
    if (handler) return handler(url, init);
    // Skills, knowledge bases, settings and the per-card stats.
    if (method === "GET") return json([]);
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function sent(key: string): Record<string, unknown>[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => `${init?.method ?? "GET"} ${input}` === key)
    .map(([, init]) => JSON.parse(String(init?.body)));
}

async function renderWith(projects: ProjectPublic[]) {
  rows = projects;
  // The project list is cached module-level; load this test's rows into it.
  await refreshProjects();
  render(<ProjectsPage />);
}

describe("projectSharingLabel", () => {
  it("says where a project stands", () => {
    expect(projectSharingLabel(project(), SERVER)).toBe("Not published");
    const published = project({ origin_server: SERVER, remote_id: ITEM, remote_version: 2, modified: false });
    expect(projectSharingLabel(published, SERVER)).toBe("Version 2");
    expect(projectSharingLabel({ ...published, modified: true }, SERVER)).toBe(
      "Differs here from version 2",
    );
    expect(projectSharingLabel(published, SERVER, 3)).toBe("Version 3 available (this is version 2)");
    expect(projectSharingLabel(published, "http://other:8002")).toBe(
      "Published on another collaboration server",
    );
  });
});

describe("projectImportAction", () => {
  it("names what importing would do here", () => {
    expect(projectImportAction(remote())).toEqual({ label: "Import", kind: "import" });
    expect(projectImportAction(remote({ name_taken: true }))).toEqual({
      label: "Import as…",
      kind: "rename",
    });
    const copy = { name: "salt-lab", remote_version: 1, modified: false };
    expect(projectImportAction(remote({ head_version: 2, local: copy }))).toEqual({
      label: "Update to version 2",
      kind: "update",
    });
    expect(projectImportAction(remote({ local: copy }))).toEqual({ label: "Imported ✓", kind: "current" });
    expect(projectImportAction(remote({ local: { ...copy, modified: true } }))).toEqual({
      label: "Re-import version 1",
      kind: "reimport",
    });
  });
});

describe("importSummary", () => {
  it("says what was imported and what was left out", () => {
    const result: ProjectImported = {
      ok: true,
      status: "imported",
      project: project(),
      skills: [
        { name: "vllm-lux", status: "imported", detail: null },
        { name: "salt-analysis", status: "unchanged", detail: null },
        { name: "mine", status: "kept", detail: "The project uses version 2; the copy here is version 3 here, and was kept." },
        { name: "theirs", status: "missing", detail: "Bundled with the publisher's VISTA, not this one." },
      ],
      knowledgeBases: [
        { slug: "salt-papers-2", status: "imported", detail: null },
        { slug: "old-papers", status: "kept", detail: "The project uses version 1; the copy here is version 2 here, and was kept." },
      ],
      forumError: "could not reach the forum",
    };
    expect(importSummary(result)).toBe(
      "Skills imported: vllm-lux. " +
        "mine: The project uses version 2; the copy here is version 3 here, and was kept. " +
        "theirs: Bundled with the publisher's VISTA, not this one. " +
        "Knowledge bases imported: salt-papers-2. " +
        "old-papers: The project uses version 1; the copy here is version 2 here, and was kept. " +
        "The Hypothesis Lab is not set up: could not reach the forum",
    );
    expect(
      importSummary({ ...result, skills: [], knowledgeBases: [], forumError: null }),
    ).toBeNull();
  });
});

describe("publishedFirst", () => {
  it("names the skills and knowledge bases published before the project", () => {
    const base = { ok: true as const, status: "published" as const, project: project() };
    expect(publishedFirst({ ...base, skillsPublished: [], knowledgeBasesPublished: [] })).toBe("");
    expect(
      publishedFirst({ ...base, skillsPublished: ["a", "b"], knowledgeBasesPublished: ["salt-papers"] }),
    ).toBe(", after publishing a, b and the knowledge base salt-papers");
    expect(
      publishedFirst({ ...base, skillsPublished: [], knowledgeBasesPublished: ["x", "y"] }),
    ).toBe(", after publishing the knowledge bases x, y");
  });
});

describe("publishProject", () => {
  it("passes a skill's conflict through, naming the skill", async () => {
    routes["POST /api/projects/salt/publish"] = () =>
      json({ detail: "salt was not published: its skill salt-density needs a choice first.", code: "skill_conflict", skill: "salt-density", skill_code: "version_conflict" }, 409);
    const result = await publishProject("salt");
    expect(result).toMatchObject({ ok: false, status: 409, code: "skill_conflict", skill: "salt-density" });
  });
});

describe("the projects page", () => {
  it("keeps Publish and Import off without a server, with the reason", async () => {
    const reason = "The collaboration server at http://127.0.0.1:8002 is not answering.";
    routes["GET /api/collaboration/status"] = () => json(status({ reachable: false, reason }));
    await renderWith([row("off-salt")]);
    const publish = await screen.findByRole("button", { name: "Publish" });
    await waitFor(() => expect(publish).toHaveAttribute("title", reason));
    expect(publish).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import from the collaboration server" })).toBeDisabled();
    expect(screen.getByText("Not published")).toBeInTheDocument();
  });

  it("confirms a first publish and says which skills went with it", async () => {
    routes["POST /api/projects/first-salt/publish"] = () =>
      json({
        status: "published",
        project: row("first-salt", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: false }),
        skills_published: ["salt-density"],
      });
    await renderWith([row("first-salt", { skills: ["salt-density"] })]);
    const publish = await screen.findByRole("button", { name: "Publish" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    const dialog = within(await screen.findByRole("dialog", { name: "Publish project" }));
    expect(sent("POST /api/projects/first-salt/publish")).toHaveLength(0);
    await userEvent.click(dialog.getByRole("button", { name: "Publish" }));
    expect(
      await screen.findByText("Published first-salt as version 1, after publishing salt-density."),
    ).toBeInTheDocument();
    expect(sent("POST /api/projects/first-salt/publish")).toEqual([{ on_top_of: null, as_new: false }]);
  });

  it("offers the choices when the server's head has moved on", async () => {
    const mine = row("stale-salt", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: true });
    routes["GET /api/collaboration/projects?manifests=false"] = () =>
      json([remote({ id: ITEM, name: "stale-salt", head_version: 2, manifest: null })]);
    routes["POST /api/projects/stale-salt/publish"] = (_u, init) => {
      const body = JSON.parse(String(init?.body));
      if (body.as_new) {
        return json({
          status: "published",
          project: { ...mine, remote_id: "33333333-3333-4333-8333-333333333333", remote_version: 1, modified: false },
          skills_published: [],
        });
      }
      return json(
        { detail: "stale-salt has been published since version 1: the server's newest is version 2.", code: "version_conflict", head_version: 2 },
        409,
      );
    };
    await renderWith([mine]);
    expect(await screen.findByText("Version 2 available (this is version 1, differs here)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Publish changes" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Publish conflict" }));
    expect(dialog.getByRole("button", { name: "Use the server's version 2" })).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Publish mine as version 3" })).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Publish stale-salt as a new project" }));
    expect(await screen.findByText("Published stale-salt as version 1.")).toBeInTheDocument();
    expect(sent("POST /api/projects/stale-salt/publish")).toEqual([
      { on_top_of: null, as_new: false },
      { on_top_of: null, as_new: true },
    ]);
  });

  it("takes the server's version it showed, not whatever is newest by then", async () => {
    const mine = row("taken-salt", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: true });
    routes["POST /api/projects/taken-salt/publish"] = () =>
      json({ detail: "taken-salt has been published since version 1.", code: "version_conflict", head_version: 2 }, 409);
    routes[`POST /api/collaboration/projects/${ITEM}/import`] = () =>
      json({
        status: "updated",
        project: { ...mine, remote_version: 2, modified: false },
        skills: [],
        forum_error: null,
      });
    await renderWith([mine]);
    const publish = await screen.findByRole("button", { name: "Publish changes" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    const dialog = within(await screen.findByRole("dialog", { name: "Publish conflict" }));
    await userEvent.click(dialog.getByRole("button", { name: "Use the server's version 2" }));
    await waitFor(() =>
      expect(sent(`POST /api/collaboration/projects/${ITEM}/import`)[0]).toEqual({
        version: 2,
        rename_to: null,
        replace_modified: true,
      }),
    );
  });

  it("sends a skill's conflict to the Skills page", async () => {
    routes["POST /api/projects/skill-salt/publish"] = () =>
      json({ detail: "skill-salt was not published: its skill salt-density needs a choice first.", code: "skill_conflict", skill: "salt-density" }, 409);
    await renderWith([row("skill-salt", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: true })]);
    const publish = await screen.findByRole("button", { name: "Publish changes" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    expect(await screen.findByText(/its skill salt-density needs a choice first/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the Skills page" })).toHaveAttribute("href", "/skills");
  });

  it("sends a knowledge base's conflict to the Knowledge Bases page", async () => {
    routes["POST /api/projects/kb-salt/publish"] = () =>
      json(
        {
          detail: "kb-salt was not published: its knowledge base salt-papers needs a choice first.",
          code: "knowledge_base_conflict",
          knowledge_base: "salt-papers",
          knowledge_base_code: "version_conflict",
        },
        409,
      );
    await renderWith([row("kb-salt", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: true })]);
    const publish = await screen.findByRole("button", { name: "Publish changes" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    expect(await screen.findByText(/its knowledge base salt-papers needs a choice first/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the Knowledge Bases page" })).toHaveAttribute(
      "href",
      "/knowledge-bases",
    );
    expect(screen.queryByRole("link", { name: "Open the Skills page" })).not.toBeInTheDocument();
  });

  it("imports a project from the server and says what came with it", async () => {
    routes["GET /api/collaboration/projects"] = () => json([remote()]);
    routes[`POST /api/collaboration/projects/${ITEM}/import`] = () =>
      json({
        status: "imported",
        project: row("salt-lab", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: false }),
        skills: [{ name: "vllm-lux", status: "imported", detail: null }],
        knowledge_bases: [{ slug: "salt-papers", status: "imported", detail: null }],
        forum_error: null,
      });
    await renderWith([]);
    const open = await screen.findByRole("button", { name: "Import from the collaboration server" });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = within(await screen.findByRole("dialog", { name: "Import a project" }));
    expect(await dialog.findByText("Bea's molten salt project.")).toBeInTheDocument();
    expect(dialog.getByText("1 knowledge base")).toBeInTheDocument();
    expect(dialog.getByText("salt-papers")).toHaveAttribute("title", "Knowledge base");
    await userEvent.click(dialog.getByRole("button", { name: "Import" }));
    expect(
      await screen.findByText(
        "Imported salt-lab version 1. Skills imported: vllm-lux. Knowledge bases imported: salt-papers.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Import a project" })).not.toBeInTheDocument();
  });

  it("asks for a new name when the project's is taken here", async () => {
    routes["GET /api/collaboration/projects"] = () => json([remote({ name_taken: true })]);
    routes[`POST /api/collaboration/projects/${ITEM}/import`] = () =>
      json({
        status: "imported",
        project: row("salt-lab-bea", { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: false }),
        skills: [],
        forum_error: null,
      });
    await renderWith([row("salt-lab")]);
    const open = await screen.findByRole("button", { name: "Import from the collaboration server" });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = within(await screen.findByRole("dialog", { name: "Import a project" }));
    await userEvent.click(await dialog.findByRole("button", { name: "Import as…" }));
    const input = dialog.getByLabelText(/Import it as/);
    await userEvent.clear(input);
    await userEvent.type(input, "salt-lab-bea");
    await userEvent.click(dialog.getByRole("button", { name: "Import" }));
    await waitFor(() =>
      expect(sent(`POST /api/collaboration/projects/${ITEM}/import`)[0]).toMatchObject({
        rename_to: "salt-lab-bea",
      }),
    );
  });

  it("asks for a usable name when the server's is not one, and checks the one typed", async () => {
    routes["GET /api/collaboration/projects"] = () => json([remote({ name: "salts/v2" })]);
    routes[`POST /api/collaboration/projects/${ITEM}/import`] = (_u, init) => {
      const body = JSON.parse(String(init?.body));
      if (!body.rename_to) {
        return json({ detail: "salts/v2 cannot be a project name here.", code: "name_invalid", name: "salts/v2" }, 409);
      }
      return json({
        status: "imported",
        project: row(body.rename_to, { origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: false }),
        skills: [],
        forum_error: null,
      });
    };
    await renderWith([]);
    const open = await screen.findByRole("button", { name: "Import from the collaboration server" });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = within(await screen.findByRole("dialog", { name: "Import a project" }));
    await userEvent.click(await dialog.findByRole("button", { name: "Import" }));
    expect(await dialog.findByText("salts/v2 cannot be a project name here.")).toBeInTheDocument();
    const input = dialog.getByLabelText(/Import it as/);
    expect(input).toHaveValue("salts-v2-2");
    await userEvent.clear(input);
    await userEvent.type(input, "mine/v2");
    expect(dialog.getByText(/Name may only contain/)).toBeInTheDocument();
    const submit = dialog.getAllByRole("button", { name: "Import" }).at(-1)!;
    expect(submit).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "salts v2");
    await userEvent.click(submit);
    await waitFor(() =>
      expect(sent(`POST /api/collaboration/projects/${ITEM}/import`)[1]).toMatchObject({
        rename_to: "salts v2",
      }),
    );
  });

  it("keeps the name typed when it is taken too", async () => {
    routes["GET /api/collaboration/projects"] = () => json([remote({ name_taken: true })]);
    routes[`POST /api/collaboration/projects/${ITEM}/import`] = () =>
      json({ detail: "A project named salt-lab-bea is already here.", code: "name_taken", name: "salt-lab-bea" }, 409);
    await renderWith([row("salt-lab"), row("salt-lab-bea")]);
    const open = await screen.findByRole("button", { name: "Import from the collaboration server" });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = within(await screen.findByRole("dialog", { name: "Import a project" }));
    await userEvent.click(await dialog.findByRole("button", { name: "Import as…" }));
    const input = dialog.getByLabelText(/Import it as/);
    await userEvent.clear(input);
    await userEvent.type(input, "salt-lab-bea");
    await userEvent.click(dialog.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(dialog.getByLabelText(/Import it as/)).toHaveValue("salt-lab-bea"));
  });
});
