import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetCollaborationForTests, type CollaborationStatus } from "@/lib/collaboration";
import {
  kbSharingLabel,
  slugError,
  suggestedSlug,
  type RemoteKnowledgeBase,
} from "@/lib/kb-sharing";
import { importAction } from "@/lib/skill-sharing";
import type { KnowledgeBase } from "@/lib/types";

/*
 * Knowledge bases on the collaboration server, from the UI's side: the labels,
 * the slug rule, and the Knowledge Bases page's Publish and Import with only
 * the router and network faked.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/knowledge-bases",
}));
vi.mock("@/components/AppTopBar", () => ({
  AppTopBar: ({ title, actions }: { title: string; actions?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import KnowledgeBasesPage from "@/app/knowledge-bases/page";

const SERVER = "http://127.0.0.1:8002";
const ITEM = "55555555-5555-4555-8555-555555555555";

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

function kb(overrides: Partial<KnowledgeBase> = {}): KnowledgeBase {
  return {
    id: "kb-1",
    slug: "salt-papers",
    name: "Salt papers",
    description: "Molten salt literature.",
    pdfs_dir: "/data/knowledge-bases/salt-papers/pdfs",
    rag_db_path: "/data/knowledge-bases/salt-papers/rag_db",
    shared_with_mcp: false,
    publications: [],
    build_status: "ready",
    last_built_at: null,
    created_at: "2026-10-01T00:00:00+00:00",
    updated_at: "2026-10-01T00:00:00+00:00",
    index_progress: null,
    origin_server: null,
    remote_id: null,
    remote_version: null,
    modified: true,
    ...overrides,
  };
}

function remote(overrides: Partial<RemoteKnowledgeBase> = {}): RemoteKnowledgeBase {
  return {
    id: ITEM,
    name: "nobel-papers",
    head_version: 1,
    created_by_name: "Bea",
    created_at: "2026-10-01T00:00:00+00:00",
    published_by_name: "Bea",
    published_at: "2026-10-01T00:00:00+00:00",
    manifest: {
      format: 1,
      slug: "nobel-papers",
      name: "Nobel papers",
      description: "Bea's prize-winning papers.",
      publications: [],
      files: 3,
      size: 3 * 1024 * 1024,
      archive_size: 5 * 1024 * 1024,
      has_index: true,
      embedding_model: "microsoft/harrier-oss-v1-270m",
      embedding_revision: "31de22b673913c7d658c0f03f792d77c2dcf8ebd",
      chromadb: "1.5.9",
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

describe("kbSharingLabel", () => {
  it("says where a knowledge base stands", () => {
    expect(kbSharingLabel(kb(), SERVER)).toBe("Not published");
    const published = kb({ origin_server: SERVER, remote_id: ITEM, remote_version: 2, modified: false });
    expect(kbSharingLabel(published, SERVER)).toBe("Version 2");
    expect(kbSharingLabel({ ...published, modified: true }, SERVER)).toBe("Changed here since version 2");
    expect(kbSharingLabel(published, SERVER, 3)).toBe("Version 3 available (this is version 2)");
    expect(kbSharingLabel(published, "http://elsewhere:8002")).toBe(
      "Published on another collaboration server",
    );
  });
});

describe("importAction, for a knowledge base", () => {
  it("names what importing would do here", () => {
    expect(importAction(remote())).toEqual({ label: "Import", kind: "import" });
    expect(importAction(remote({ name_taken: true }))).toEqual({ label: "Import as…", kind: "rename" });
    const copy = { name: "nobel-papers", remote_version: 1, modified: false };
    expect(importAction(remote({ local: copy, head_version: 2 }))).toEqual({
      label: "Update to version 2",
      kind: "update",
    });
    expect(importAction(remote({ local: copy }))).toEqual({ label: "Imported ✓", kind: "current" });
    expect(importAction(remote({ local: { ...copy, modified: true } }))).toEqual({
      label: "Revert to version 1",
      kind: "revert",
    });
  });
});

describe("slugs", () => {
  it("follows the backend's rule", () => {
    expect(slugError("salt-papers")).toBeNull();
    expect(slugError("a")).toBeNull();
    expect(slugError("Salt Papers")).not.toBeNull();
    expect(slugError("-salt")).not.toBeNull();
    expect(slugError("x".repeat(81))).not.toBeNull();
  });

  it("suggests one the rule accepts", () => {
    expect(suggestedSlug(remote())).toBe("nobel-papers-2");
    expect(slugError(suggestedSlug(remote({ name: "x".repeat(80) })))).toBeNull();
  });
});

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
let routes: Record<string, Handler>;
let rows: KnowledgeBase[];
const fetchMock = vi.fn();

beforeEach(() => {
  resetCollaborationForTests();
  rows = [];
  routes = {
    "GET /api/collaboration/status": () => json(status()),
    "GET /api/knowledge-bases": () => json(rows),
    "GET /api/collaboration/knowledge-bases?manifests=false": () => json([]),
  };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    const handler = routes[`${method} ${url}`];
    if (handler) return handler(url, init);
    const detail = /^\/api\/knowledge-bases\/([a-z0-9-]+)$/.exec(url);
    if (method === "GET" && detail) {
      const found = rows.find((r) => r.slug === detail[1]);
      return found ? json(found) : json({ ok: false }, 404);
    }
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

describe("the knowledge bases page", () => {
  it("keeps Publish and Import off without a server, with the reason", async () => {
    routes["GET /api/collaboration/status"] = () =>
      json(status({ configured: false, reachable: false, url: null, reason: "No collaboration server is set." }));
    rows = [kb()];
    render(<KnowledgeBasesPage />);
    const publish = await screen.findByRole("button", { name: "Publish" });
    await waitFor(() => expect(publish).toHaveAttribute("title", "No collaboration server is set."));
    expect(publish).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("does not publish while indexing", async () => {
    rows = [
      kb({
        index_progress: { phase: "indexing", processed: 0, total: 1, started_at: Date.now() / 1000 },
      }),
    ];
    render(<KnowledgeBasesPage />);
    const publish = await screen.findByRole("button", { name: "Publish" });
    await waitFor(() => expect(publish).toHaveAttribute("title", "Publish once indexing has finished."));
    expect(publish).toBeDisabled();
  });

  it("confirms a first publish and says which version it became", async () => {
    rows = [kb()];
    routes["POST /api/knowledge-bases/salt-papers/publish"] = () => {
      rows = [kb({ origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: false })];
      return json({ status: "published", knowledge_base: rows[0] });
    };
    render(<KnowledgeBasesPage />);
    const publish = await screen.findByRole("button", { name: "Publish" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    const dialog = within(await screen.findByRole("dialog", { name: "Publish knowledge base" }));
    expect(sent("POST /api/knowledge-bases/salt-papers/publish")).toHaveLength(0);
    await userEvent.click(dialog.getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("Published Salt papers as version 1.")).toBeInTheDocument();
    expect(sent("POST /api/knowledge-bases/salt-papers/publish")).toEqual([
      { on_top_of: null, as_new: false },
    ]);
    expect(await screen.findByText("Version 1")).toBeInTheDocument();
  });

  it("offers the choices when the server's head has moved on", async () => {
    rows = [kb({ origin_server: SERVER, remote_id: ITEM, remote_version: 1, modified: true })];
    routes["POST /api/knowledge-bases/salt-papers/publish"] = () =>
      json(
        { detail: "salt-papers has been published since version 1: the server's newest is version 2.", code: "version_conflict", head_version: 2 },
        409,
      );
    routes[`POST /api/collaboration/knowledge-bases/${ITEM}/import`] = () =>
      json({
        status: "updated",
        knowledge_base: kb({ origin_server: SERVER, remote_id: ITEM, remote_version: 2, modified: false }),
      });
    render(<KnowledgeBasesPage />);
    const publish = await screen.findByRole("button", { name: "Publish changes" });
    await waitFor(() => expect(publish).toBeEnabled());
    await userEvent.click(publish);
    const dialog = within(await screen.findByRole("dialog", { name: "Publish conflict" }));
    expect(dialog.getByRole("button", { name: "Publish mine as version 3" })).toBeInTheDocument();
    expect(
      dialog.getByRole("button", { name: "Publish Salt papers as a new knowledge base" }),
    ).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Use the server's version 2" }));
    expect(await screen.findByText("Salt papers is now version 2.")).toBeInTheDocument();
    expect(sent(`POST /api/collaboration/knowledge-bases/${ITEM}/import`)).toEqual([
      { version: 2, rename_to: null, replace_modified: true },
    ]);
  });

  it("imports a knowledge base, asking for a slug when its own is taken", async () => {
    rows = [kb({ slug: "nobel-papers", name: "My nobel papers" })];
    routes["GET /api/collaboration/knowledge-bases"] = () => json([remote({ name_taken: true })]);
    routes[`POST /api/collaboration/knowledge-bases/${ITEM}/import`] = (_u, init) => {
      const body = JSON.parse(String(init?.body));
      const imported = kb({
        id: "kb-2",
        slug: body.rename_to,
        name: "Nobel papers",
        origin_server: SERVER,
        remote_id: ITEM,
        remote_version: 1,
        modified: false,
      });
      rows = [...rows, imported];
      return json({ status: "imported", knowledge_base: imported });
    };
    render(<KnowledgeBasesPage />);
    const open = await screen.findByRole("button", { name: "Import" });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = within(await screen.findByRole("dialog", { name: "Import a knowledge base" }));
    expect(await dialog.findByText("Bea's prize-winning papers.")).toBeInTheDocument();
    expect(dialog.getByText("3 PDFs (3.0 MB), 5.0 MB to download")).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Import as…" }));
    const input = dialog.getByRole("textbox", { name: "Import it as" });
    expect(input).toHaveValue("nobel-papers-2");
    await userEvent.clear(input);
    await userEvent.type(input, "Nobel Papers");
    expect(dialog.getByRole("button", { name: "Import" })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "nobel-papers-bea");
    await userEvent.click(dialog.getByRole("button", { name: "Import" }));
    expect(
      await screen.findByText("Imported Nobel papers (nobel-papers-bea) version 1."),
    ).toBeInTheDocument();
    expect(sent(`POST /api/collaboration/knowledge-bases/${ITEM}/import`)).toEqual([
      { version: null, rename_to: "nobel-papers-bea", replace_modified: false },
    ]);
  });

  it("says why an index cannot be searched here", async () => {
    routes["GET /api/collaboration/knowledge-bases"] = () => json([remote()]);
    routes[`POST /api/collaboration/knowledge-bases/${ITEM}/import`] = () =>
      json({ detail: "Nobel papers was indexed with other/embedder; this VISTA searches with another." }, 409);
    render(<KnowledgeBasesPage />);
    const open = await screen.findByRole("button", { name: "Import" });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = within(await screen.findByRole("dialog", { name: "Import a knowledge base" }));
    await userEvent.click(await dialog.findByRole("button", { name: "Import" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent("indexed with other/embedder");
  });
});
