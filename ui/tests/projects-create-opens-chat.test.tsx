import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProjectsPage from "@/app/projects/page";
import { writePendingDestination } from "@/lib/pending-destination";
import { type ProjectPublic, readActiveProjectName, writeActiveProjectName } from "@/lib/projects";

/*
 * The projects page with the project store and active-project pointer real,
 * and only the router and the network faked. Creating a project should open
 * it: activate it and go where the researcher was headed (chat by default),
 * exactly as clicking an existing project's card does.
 */

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/projects",
}));

// The project list is cached module-level and survives between tests in this
// file, so each test uses its own project names.
let rows: ProjectPublic[];
let failCreate: string | null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function row(name: string): ProjectPublic {
  return {
    id: `id-${name}`,
    name,
    description: null,
    system_prompt: null,
    skills: [],
    knowledge_bases: [],
    tools: [],
    usage_limits: {},
  } as unknown as ProjectPublic;
}

beforeEach(() => {
  push.mockReset();
  replace.mockReset();
  writeActiveProjectName(null);
  window.sessionStorage.clear();
  rows = [];
  failCreate = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    if (url === "/api/projects" && method === "POST") {
      if (failCreate) return json({ detail: failCreate }, 400);
      const body = JSON.parse(String(init?.body)) as { name: string };
      const created = row(body.name);
      rows.push(created);
      return json(created, 201);
    }
    if (url.startsWith("/api/projects/") && method === "PUT") {
      const body = JSON.parse(String(init?.body)) as { name: string };
      return json(row(body.name));
    }
    if (url === "/api/projects") return json(rows);
    if (url.startsWith("/api/settings")) return json({ install_id: "i1", email: "researcher@ornl.gov" });
    // Skills, knowledge bases, and the per-card stats.
    return json([]);
  });
});

async function createProjectNamed(name: string) {
  const user = userEvent.setup();
  render(<ProjectsPage />);
  await user.click(await screen.findByRole("button", { name: /create a project|new project/i }));
  const dialog = await screen.findByRole("dialog", { name: "Create project" });
  await user.type(within(dialog).getByLabelText("Name"), name);
  await user.click(within(dialog).getByRole("button", { name: "Create" }));
}

describe("creating a project", () => {
  it("activates the new project and opens chat", async () => {
    await createProjectNamed("fluoride-salts");

    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(readActiveProjectName()).toBe("fluoride-salts");
  });

  it("goes where the researcher was headed when chat sent them here", async () => {
    writePendingDestination("/files");

    await createProjectNamed("chloride-salts");

    await waitFor(() => expect(push).toHaveBeenCalledWith("/files"));
    expect(readActiveProjectName()).toBe("chloride-salts");
  });

  it("stays on the page, with the error shown, when creation fails", async () => {
    failCreate = "Forum token cannot reach the repository.";

    await createProjectNamed("nitrate-salts");

    expect(await screen.findByText(failCreate)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
    expect(readActiveProjectName()).toBeNull();
  });
});

describe("editing a project", () => {
  it("saves without navigating or changing the active project", async () => {
    rows = [row("carbonate-salts")];
    writeActiveProjectName("something-else");
    const user = userEvent.setup();
    render(<ProjectsPage />);

    await user.click(await screen.findByRole("button", { name: /edit/i }));
    const dialog = await screen.findByRole("dialog", { name: "Edit project" });
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(push).not.toHaveBeenCalled();
    expect(readActiveProjectName()).toBe("something-else");
  });
});
