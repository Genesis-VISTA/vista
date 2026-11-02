import { render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetCollaborationForTests,
  useCollaboration,
  type CollaborationStatus,
} from "@/lib/collaboration";
import {
  importAction,
  publishSkill,
  sharingLabel,
  type RemoteSkill,
} from "@/lib/skill-sharing";
import type { SkillSummary } from "@/lib/types";

/*
 * Skills on the collaboration server, from the UI's side: the labels and
 * button states, the refusals the proxies pass through, and the Skill Hub and
 * Skills pages with only the network (and the project store) faked.
 */

const { project } = vi.hoisted(() => ({
  project: {
    current: null as null | { id: string; name: string; skills: string[] },
  },
}));
vi.mock("@/lib/projects", () => ({
  useActiveProject: () => project.current,
  useProjects: () => ({ projects: [], loading: false, error: null }),
  activateProject: vi.fn(),
  refreshProjects: vi.fn(async () => []),
}));
vi.mock("@/lib/loaded-skills", () => ({ setSkillLoaded: vi.fn() }));
vi.mock("@/components/AppTopBar", () => ({
  AppTopBar: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import SkillHubPage from "@/app/skill-hub/page";
import SkillsPage from "@/app/skills/page";

const SERVER = "http://127.0.0.1:8002";

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
    ...overrides,
  };
}

function remote(overrides: Partial<RemoteSkill> = {}): RemoteSkill {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    name: "vllm-lux",
    head_version: 1,
    created_by_name: "Bea",
    created_at: "2026-10-01T00:00:00+00:00",
    published_by_name: "Bea",
    published_at: "2026-10-01T00:00:00+00:00",
    manifest: {
      name: "vllm-lux",
      description: "Serve a model with vLLM on Lux.",
      hpc_jobs: ["serve"],
      files: 3,
      size: 2048,
    },
    local: null,
    name_taken: false,
    ...overrides,
  };
}

function local(overrides: Partial<SkillSummary> = {}): SkillSummary {
  return {
    slug: "salt-density",
    name: "salt-density",
    description: "Estimate a salt's density.",
    path: "skills/salt-density/SKILL.md",
    hpcJobs: [],
    bundled: false,
    originServer: null,
    remoteId: null,
    remoteVersion: null,
    modified: true,
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
const fetchMock = vi.fn();

beforeEach(() => {
  resetCollaborationForTests();
  project.current = { id: "p1", name: "molten-salt", skills: [] };
  routes = { "GET /api/collaboration/status": () => json(status()) };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected fetch: ${key}`);
    return handler(url, init);
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

describe("sharingLabel", () => {
  it("says where a skill stands", () => {
    expect(sharingLabel(local({ bundled: true }), SERVER)).toBe("Bundled with VISTA");
    expect(sharingLabel(local(), SERVER)).toBe("Not published");
    const published = local({ originServer: SERVER, remoteId: "x", remoteVersion: 2, modified: false });
    expect(sharingLabel(published, SERVER)).toBe("Version 2");
    expect(sharingLabel({ ...published, modified: true }, SERVER)).toBe("Changed here since version 2");
    expect(sharingLabel(published, SERVER, 3)).toBe("Version 3 available (this is version 2)");
    expect(sharingLabel(published, "http://other:8002")).toBe(
      "Published on another collaboration server",
    );
  });
});

describe("importAction", () => {
  it("follows the copy here", () => {
    expect(importAction(remote()).kind).toBe("import");
    expect(importAction(remote({ name_taken: true })).kind).toBe("rename");
    const copy = { name: "vllm-lux", remote_version: 1, modified: false };
    expect(importAction(remote({ local: copy })).kind).toBe("current");
    expect(importAction(remote({ local: copy, head_version: 2 }))).toEqual({
      label: "Update to version 2",
      kind: "update",
    });
    // Changed here at the newest version: the server's copy can be taken back.
    expect(importAction(remote({ local: { ...copy, modified: true } }))).toEqual({
      label: "Revert to version 1",
      kind: "revert",
    });
  });
});

describe("publishSkill", () => {
  it("passes a conflict through with the head", async () => {
    routes["POST /api/skills/salt-density/publish"] = () =>
      json({ detail: "salt-density has been published since…", code: "version_conflict", head_version: 4 }, 409);
    const result = await publishSkill("salt-density");
    expect(result).toEqual({
      ok: false,
      status: 409,
      detail: "salt-density has been published since…",
      code: "version_conflict",
      headVersion: 4,
    });
  });

  it("checks the server again when the token is refused", async () => {
    const reason = "Your token has expired. Get a token from the sign-in page.";
    let current = status();
    routes["GET /api/collaboration/status"] = () => json(current);
    const { result: view } = renderHook(() => useCollaboration());
    await waitFor(() => expect(view.current.available).toBe(true));
    routes["POST /api/skills/salt-density/publish"] = () => {
      current = status({ signed_in: false, sign_in_required: true, reason });
      return json({ detail: reason, code: "collaboration_sign_in_required" }, 401);
    };
    const result = await publishSkill("salt-density");
    expect(result).toMatchObject({ ok: false, code: "collaboration_sign_in_required" });
    // Every Publish and Import control follows, not only this one.
    await waitFor(() => expect(view.current).toMatchObject({ available: false, reason }));
  });
});

describe("the Skill Hub", () => {
  it("lists nothing without a server, and says why", async () => {
    const reason = "No collaboration server is set. Add its address under Settings → Collaboration to publish and import.";
    routes["GET /api/collaboration/status"] = () =>
      json(status({ configured: false, reachable: false, reason, url: null, source: null }));
    render(<SkillHubPage />);
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => u === "/api/collaboration/skills")).toBe(false);
  });

  it("imports a skill into the active project", async () => {
    let listing = [remote()];
    routes["GET /api/collaboration/skills"] = () => json(listing);
    routes[`POST /api/collaboration/skills/${remote().id}/import`] = () => {
      listing = [remote({ local: { name: "vllm-lux", remote_version: 1, modified: false } })];
      return json({ status: "imported", skill: local({ slug: "vllm-lux", remoteVersion: 1 }) });
    };
    render(<SkillHubPage />);
    expect(await screen.findByText("Serve a model with vLLM on Lux.")).toBeInTheDocument();
    expect(screen.getByText("Runs HPC jobs: serve")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/Imported vllm-lux version 1 and loaded it into molten-salt/)).toBeInTheDocument();
    expect(sent(`POST /api/collaboration/skills/${remote().id}/import`)[0]).toMatchObject({
      project: "molten-salt",
      rename_to: null,
    });
    expect(await screen.findByRole("button", { name: "Imported ✓" })).toBeDisabled();
  });

  it("asks for a new name when the skill's is taken here", async () => {
    routes["GET /api/collaboration/skills"] = () => json([remote({ name_taken: true })]);
    routes[`POST /api/collaboration/skills/${remote().id}/import`] = () =>
      json({ status: "imported", skill: local({ slug: "vllm-lux-bea" }) });
    render(<SkillHubPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Import as…" }));
    const dialog = within(screen.getByRole("dialog", { name: "Import under a new name" }));
    await userEvent.clear(dialog.getByLabelText("New name"));
    await userEvent.type(dialog.getByLabelText("New name"), "vllm-lux-bea");
    await userEvent.click(dialog.getByRole("button", { name: "Import" }));
    await waitFor(() =>
      expect(sent(`POST /api/collaboration/skills/${remote().id}/import`)[0]).toMatchObject({
        rename_to: "vllm-lux-bea",
      }),
    );
  });

  it("warns before an update replaces changes made here", async () => {
    const copy = { name: "vllm-lux", remote_version: 1, modified: true };
    routes["GET /api/collaboration/skills"] = () => json([remote({ head_version: 2, local: copy })]);
    routes[`POST /api/collaboration/skills/${remote().id}/import`] = () =>
      json({ status: "updated", skill: local({ slug: "vllm-lux", remoteVersion: 2 }) });
    render(<SkillHubPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Update to version 2" }));
    const dialog = within(screen.getByRole("dialog", { name: "Replace your changes?" }));
    expect(fetchMock.mock.calls.some(([u, i]) => i?.method === "POST" && String(u).includes("/import"))).toBe(false);
    await userEvent.click(dialog.getByRole("button", { name: "Replace my changes" }));
    await waitFor(() =>
      expect(sent(`POST /api/collaboration/skills/${remote().id}/import`)[0]).toMatchObject({
        replace_modified: true,
      }),
    );
  });
});

describe("the Skills page", () => {
  it("offers the three choices when the server's head has moved on", async () => {
    const mine = local({ originServer: SERVER, remoteId: remote().id, remoteVersion: 1, modified: true });
    project.current = { id: "p1", name: "molten-salt", skills: ["salt-density"] };
    routes["GET /api/skills"] = () => json([mine]);
    // Only the head versions: the page shows no manifests.
    routes["GET /api/collaboration/skills?manifests=false"] = () =>
      json([remote({ id: remote().id, head_version: 2, manifest: null })]);
    const publishes: Record<string, unknown>[] = [];
    routes["POST /api/skills/salt-density/publish"] = (_u, init) => {
      const body = JSON.parse(String(init?.body));
      publishes.push(body);
      if (body.on_top_of === 2) {
        return json({ ...mine, remoteVersion: 3, modified: false });
      }
      return json(
        { detail: "salt-density has been published since version 1: the server's newest is version 2.", code: "version_conflict", head_version: 2 },
        409,
      );
    };
    render(<SkillsPage />);
    expect(await screen.findByText("Version 2 available (this is version 1, changed here)")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Publish changes" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Publish conflict" }));
    expect(dialog.getByRole("button", { name: "Use the server's version 2" })).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Publish salt-density as a new skill" })).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Publish mine as version 3" }));

    expect(await screen.findByText("Published salt-density as version 3.")).toBeInTheDocument();
    expect(publishes).toEqual([
      { on_top_of: null, as_new: false },
      { on_top_of: 2, as_new: false },
    ]);
  });

  it("keeps Publish off without a server, with the reason", async () => {
    const reason = "The collaboration server at http://127.0.0.1:8002 is not answering.";
    routes["GET /api/collaboration/status"] = () => json(status({ reachable: false, reason }));
    project.current = { id: "p1", name: "molten-salt", skills: [] };
    routes["GET /api/skills"] = () => json([local(), local({ slug: "salt-analysis", name: "salt-analysis", bundled: true })]);
    render(<SkillsPage />);
    const publish = await screen.findByRole("button", { name: "Publish" });
    await waitFor(() => expect(publish).toHaveAttribute("title", reason));
    expect(publish).toBeDisabled();
    // A bundled skill is never published, but can be loaded.
    expect(screen.getAllByRole("button", { name: "Publish" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Load" })).toHaveLength(2);
    // Nor deleted: it ships with VISTA.
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(1);
  });
});
