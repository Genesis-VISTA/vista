import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PUBLIC_TEAM_ID,
  effectiveTeam,
  publishAudience,
  refreshCollaborationStatus,
  resetCollaborationForTests,
  teamsView,
  usableInProject,
  type CollaborationStatus,
  type CollaborationTeam,
} from "@/lib/collaboration";
import { refreshProjects, type ProjectPublic } from "@/lib/projects";
import { TeamsModal } from "@/components/TeamsModal";
import { PublishToTeamModal } from "@/components/PublishToTeamModal";
import ProjectsPage from "@/app/projects/page";
import SkillHubPage from "@/app/skill-hub/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/projects",
}));

const ALPHA = "aaaaaaaa-0000-4000-8000-000000000001";
const BETA = "bbbbbbbb-0000-4000-8000-000000000002";
const GONE = "cccccccc-0000-4000-8000-000000000003";

function team(id: string, name: string, isPublic = false): CollaborationTeam {
  return {
    id,
    name,
    public: isPublic,
    created_by: "me",
    created_by_name: "Me",
    created_at: "2026-10-01T00:00:00Z",
  };
}

const TEAMS = [team(PUBLIC_TEAM_ID, "public", true), team(ALPHA, "Alpha"), team(BETA, "Beta")];
const KNOWN = new Set([PUBLIC_TEAM_ID, ALPHA, BETA]);

function status(overrides: Partial<CollaborationStatus> = {}): CollaborationStatus {
  return {
    configured: true,
    reachable: true,
    reason: null,
    url: "http://127.0.0.1:8002",
    source: "settings",
    server_version: "0.1.0",
    enabled: true,
    signed_in: true,
    sign_in_required: false,
    refused: false,
    shown: true,
    default_server: false,
    sign_in_url: null,
    user: { sub: "me", email: "me@ornl.gov", name: "Me" },
    teams: TEAMS,
    ...overrides,
  };
}

function json(body: unknown, code = 200): Response {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: { "content-type": "application/json" },
  });
}

type Handler = (init: RequestInit | undefined, url: string) => Response | Promise<Response>;
let routes: Record<string, Handler>;
let calls: { method: string; url: string; body: unknown }[];
let current: CollaborationStatus;

beforeEach(() => {
  resetCollaborationForTests();
  current = status();
  calls = [];
  routes = {
    "GET /api/collaboration/status": () => json(current),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const path = url.split("?")[0];
      const handler = routes[`${method} ${url}`] ?? routes[`${method} ${path}`];
      return handler ? handler(init, url) : json([]);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a resource's team", () => {
  it("counts a team the researcher is not in as private", () => {
    expect(effectiveTeam(ALPHA, KNOWN)).toBe(ALPHA);
    expect(effectiveTeam(GONE, KNOWN)).toBeNull();
    expect(effectiveTeam(null, KNOWN)).toBeNull();
  });

  it("is the one it records until the teams are known", () => {
    const view = teamsView({ status: status({ signed_in: false, teams: [] }), error: null });
    expect(view.shown).toBe(false);
    expect(view.teamOf(BETA)).toBe(BETA);
    expect(view.teamOf(null)).toBeNull();
    expect(teamsView({ status: null, error: null }).teamOf(GONE)).toBe(GONE);
  });

  it("is its team, named, with one", () => {
    const view = teamsView({ status: status(), error: null });
    expect(view.shown).toBe(true);
    expect(view.teamOf(ALPHA)).toBe(ALPHA);
    expect(view.teamOf(GONE)).toBeNull();
    expect(view.teamName(BETA)).toBe("Beta");
    expect(view.teamName(PUBLIC_TEAM_ID)).toBe("Public");
    expect(view.teamName(null)).toBeNull();
  });
});

describe("what a project can use", () => {
  it("is a team's project's own team's, and anything for a private one, as the backend enforces", () => {
    expect(usableInProject(ALPHA, ALPHA)).toBe(true);
    expect(usableInProject(null, ALPHA)).toBe(false);
    expect(usableInProject(PUBLIC_TEAM_ID, ALPHA)).toBe(false);
    // Unlike publishing, a team the researcher left is still another team.
    expect(usableInProject(GONE, ALPHA)).toBe(false);
    for (const team of [null, ALPHA, PUBLIC_TEAM_ID, GONE]) {
      expect(usableInProject(team, null)).toBe(true);
    }
  });
});

describe("publishing", () => {
  it("says which team a publish goes to, and that a private one needs one", () => {
    const view = teamsView({ status: status(), error: null });
    expect(publishAudience(PUBLIC_TEAM_ID, view)).toMatch(/public team, visible to everyone/);
    expect(publishAudience(ALPHA, view)).toMatch(/team Alpha: only its members/);
    expect(publishAudience(null, view)).toMatch(/private, on this computer only: choose a team/);
  });
});

describe("managing teams", () => {
  beforeEach(() => {
    for (const id of [ALPHA, BETA]) {
      routes[`GET /api/collaboration/teams/${id}/members`] = () =>
        json([{ sub: "me", email: "me@ornl.gov", name: `Me in ${id === ALPHA ? "Alpha" : "Beta"}`, added_by: "me", added_at: "x" }]);
    }
    routes["GET /api/collaboration/users"] = (_init, url) => {
      const q = new URL(url, "http://ui").searchParams.get("q") ?? "";
      const everyone = [
        { sub: "me", email: "me@ornl.gov", name: "Me" },
        { sub: "bob", email: "bob@ornl.gov", name: "Bob Builder" },
        { sub: "ann", email: "ann@ornl.gov", name: "Ann" },
      ];
      return json(everyone.filter((u) => `${u.name} ${u.email}`.toLowerCase().includes(q)));
    };
    routes[`POST /api/collaboration/teams/${ALPHA}/members`] = () =>
      json({ sub: "bob", email: "bob@ornl.gov", name: "Bob Builder", added_by: "me", added_at: "y" }, 201);
  });

  it("lists the first own team's members, and another's once picked", async () => {
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    const list = await screen.findByRole("list", { name: "Members" });
    expect(within(list).getByText("Me in Alpha (you)")).toBeInTheDocument();
    const picker = screen.getByRole("combobox", { name: "Team" });
    // The public team has no member list, so it is not offered.
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(["Alpha", "Beta"]);
    await user.selectOptions(picker, "Beta");
    expect(await screen.findByText("Me in Beta (you)")).toBeInTheDocument();
    // Picking a team to manage saves nothing.
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("says so when the researcher has no team of their own", async () => {
    current = status({ teams: [team(PUBLIC_TEAM_ID, "public", true)] });
    render(<TeamsModal onClose={() => {}} />);
    expect(await screen.findByText(/no team of your own yet/)).toBeInTheDocument();
  });

  it("searches people as you type, by name and email, and adds the one picked", async () => {
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await screen.findByRole("list", { name: "Members" });

    await user.type(screen.getByRole("combobox", { name: "Add a member" }), "bo");
    const option = await screen.findByRole("option", { name: /Bob Builder/ });
    expect(option).toHaveTextContent("bob@ornl.gov");
    expect(calls.some((c) => c.url === "/api/collaboration/users?q=bo")).toBe(true);
    expect(screen.queryByRole("option", { name: /Ann/ })).not.toBeInTheDocument();

    await user.click(option);
    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "POST",
        url: `/api/collaboration/teams/${ALPHA}/members`,
        body: { sub: "bob" },
      }),
    );
    expect(await screen.findByText("Added Bob Builder to Alpha.")).toBeInTheDocument();
  });

  it("leaves out who is already a member", async () => {
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await screen.findByRole("list", { name: "Members" });
    await user.type(screen.getByRole("combobox", { name: "Add a member" }), "ornl");
    await screen.findByRole("option", { name: /Bob Builder/ });
    expect(screen.queryByRole("option", { name: /^Me/ })).not.toBeInTheDocument();
  });

  it("makes a team and picks it to manage, selecting nothing", async () => {
    const gamma = "dddddddd-0000-4000-8000-000000000004";
    routes["POST /api/collaboration/teams"] = () => {
      current = status({ teams: [...TEAMS, team(gamma, "Gamma")] });
      return json(team(gamma, "Gamma"), 201);
    };
    routes[`GET /api/collaboration/teams/${gamma}/members`] = () => json([]);
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await user.type(screen.getByRole("textbox", { name: "New team name" }), "Gamma");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/Made the team Gamma/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Team" })).toHaveValue(gamma));
    expect(calls).toContainEqual({ method: "POST", url: "/api/collaboration/teams", body: { name: "Gamma" } });
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("does not show another team while the one just made is not listed yet", async () => {
    const gamma = "dddddddd-0000-4000-8000-000000000004";
    // The status, asked again, does not list it yet.
    routes["POST /api/collaboration/teams"] = () => json(team(gamma, "Gamma"), 201);
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await screen.findByRole("list", { name: "Members" });
    await user.type(screen.getByRole("textbox", { name: "New team name" }), "Gamma");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/Gamma is made, but your list of teams could not be read again/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Add a member" })).not.toBeInTheDocument();
  });

  it("takes Escape for itself, after closing the people list first", async () => {
    const onClose = vi.fn();
    const below = vi.fn();
    // As Settings, under it, listens: only an Escape it did not handle closes Settings.
    const settings = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) below();
    };
    window.addEventListener("keydown", settings);
    try {
      const user = userEvent.setup();
      render(<TeamsModal onClose={onClose} />);
      await screen.findByRole("list", { name: "Members" });
      await user.type(screen.getByRole("combobox", { name: "Add a member" }), "bo");
      await screen.findByRole("option", { name: /Bob Builder/ });
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("listbox", { name: "People" })).not.toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();

      await user.keyboard("{Escape}");
      expect(onClose).toHaveBeenCalledOnce();
      expect(below).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", settings);
    }
  });

  it("keeps Escape from Settings even with focus outside it", async () => {
    const onClose = vi.fn();
    const below = vi.fn();
    const settings = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) below();
    };
    window.addEventListener("keydown", settings);
    try {
      render(<TeamsModal onClose={onClose} />);
      await screen.findByRole("list", { name: "Members" });
      // As when the focused button goes disabled while busy: focus falls to the page.
      (document.activeElement as HTMLElement | null)?.blur();
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
      expect(onClose).toHaveBeenCalledOnce();
      expect(below).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", settings);
    }
  });

  it("is drawn at the top of the page, not inside what opened it", async () => {
    const { container } = render(<TeamsModal onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog", { name: "Teams" });
    expect(container).not.toContainElement(dialog);
    expect(dialog).toHaveFocus();
  });

  it("shows the backend's sentence when a name is taken", async () => {
    routes["POST /api/collaboration/teams"] = () =>
      json({ detail: "A team named alpha already exists.", code: "collaboration_refused" }, 502);
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await user.type(screen.getByRole("textbox", { name: "New team name" }), "alpha");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A team named alpha already exists.");
  });

  it("leaves the team", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    routes[`DELETE /api/collaboration/teams/${ALPHA}/members/me`] = () => {
      current = status({ teams: [team(PUBLIC_TEAM_ID, "public", true), team(BETA, "Beta")] });
      return new Response(null, { status: 204 });
    };
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Leave Alpha" }));
    expect(await screen.findByText("You left Alpha.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Team" })).toHaveValue(BETA));
  });
});

describe("the projects page", () => {
  function project(name: string, teamId: string | null): ProjectPublic {
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
      team_id: teamId,
    };
  }

  function kb(slug: string, teamId: string | null) {
    return { id: slug, slug, name: `KB ${slug}`, description: "", publications: [], team_id: teamId };
  }

  beforeEach(() => {
    routes["GET /api/projects"] = () => json([project("alpha-work", ALPHA)]);
    routes["GET /api/skills"] = () =>
      json([
        { slug: "alpha-skill", name: "alpha-skill", description: "", teamId: ALPHA },
        { slug: "public-skill", name: "public-skill", description: "", teamId: PUBLIC_TEAM_ID },
        { slug: "private-skill", name: "private-skill", description: "", teamId: null },
        { slug: "beta-skill", name: "beta-skill", description: "", teamId: BETA },
      ]);
    routes["GET /api/knowledge-bases"] = () =>
      json([
        kb("alpha-kb", ALPHA),
        kb("public-kb", PUBLIC_TEAM_ID),
        kb("private-kb", null),
        kb("beta-kb", BETA),
      ]);
  });

  it("offers only what an edited project's team can use, and says why", async () => {
    // Collaboration off: the rule is the backend's, so it applies regardless.
    current = status({ reachable: false, signed_in: false, teams: [], reason: "Off." });
    await refreshProjects();
    const user = userEvent.setup();
    render(<ProjectsPage />);
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit project" });
    await within(dialog).findByText("alpha-skill");
    for (const other of ["public-skill", "private-skill", "beta-skill", "KB public-kb", "KB private-kb", "KB beta-kb"]) {
      expect(within(dialog).queryByText(other)).not.toBeInTheDocument();
    }
    expect(within(dialog).getByText("KB alpha-kb")).toBeInTheDocument();
    expect(within(dialog).getByText(/3 skills are not listed, being private or in another team/)).toBeInTheDocument();
  });

  it("offers a new project, which is private, every team's", async () => {
    await refreshProjects();
    const user = userEvent.setup();
    render(<ProjectsPage />);
    await user.click(await screen.findByRole("button", { name: /new project/i }));
    const dialog = await screen.findByRole("dialog", { name: "Create project" });
    await within(dialog).findByText("private-skill");
    for (const offered of ["public-skill", "alpha-skill", "beta-skill", "KB private-kb", "KB public-kb", "KB beta-kb"]) {
      expect(within(dialog).getByText(offered)).toBeInTheDocument();
    }
    expect(within(dialog).queryByText(/not listed/)).not.toBeInTheDocument();
  });

  it("lists every project, whatever its team", async () => {
    routes["GET /api/projects"] = () =>
      json([
        project("alpha-work", ALPHA),
        project("shared-work", PUBLIC_TEAM_ID),
        project("own-work", null),
        project("beta-work", BETA),
      ]);
    await refreshProjects();
    render(<ProjectsPage />);
    for (const name of ["alpha-work", "shared-work", "own-work", "beta-work"]) {
      expect(await screen.findByText(name)).toBeInTheDocument();
    }
    expect(screen.queryByText(/in another\s+team/)).not.toBeInTheDocument();
  });

  it("publishes a private project into a team picked for it", async () => {
    routes["GET /api/projects"] = () => json([project("own-work", null)]);
    await refreshProjects();
    const user = userEvent.setup();
    render(<ProjectsPage />);
    await user.click(await screen.findByRole("button", { name: "Publish…" }));
    expect(await screen.findByRole("dialog", { name: "Publish project" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Copy to team/ })).not.toBeInTheDocument();
  });

  it("publishes a team's project to its team, with no team to pick", async () => {
    await refreshProjects();
    render(<ProjectsPage />);
    expect(await screen.findByRole("button", { name: "Publish" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish…" })).not.toBeInTheDocument();
  });
});

describe("the Skill Hub", () => {
  function remote(id: string, name: string, teamId: string | null) {
    return {
      id,
      team_id: teamId,
      name,
      head_version: 1,
      created_by_name: null,
      created_at: "2026-10-01T00:00:00Z",
      published_by_name: null,
      published_at: null,
      manifest: null,
      local: null,
      name_taken: false,
    };
  }

  it("lists every team's skills, and narrows to one", async () => {
    routes["GET /api/collaboration/skills"] = () =>
      json([remote("1", "alpha-hub", ALPHA), remote("2", "public-hub", PUBLIC_TEAM_ID), remote("3", "beta-hub", BETA)]);
    const user = userEvent.setup();
    render(<SkillHubPage />);
    expect(await screen.findByText("alpha-hub")).toBeInTheDocument();
    expect(screen.getByText("public-hub")).toBeInTheDocument();
    expect(screen.getByText("beta-hub")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Team" }), "Beta");
    expect(screen.queryByText("alpha-hub")).not.toBeInTheDocument();
    expect(screen.queryByText("public-hub")).not.toBeInTheDocument();
    expect(screen.getByText("beta-hub")).toBeInTheDocument();
  });

  it("lists every team again once the one picked is left", async () => {
    routes["GET /api/collaboration/skills"] = () =>
      json([remote("1", "alpha-hub", ALPHA), remote("2", "public-hub", PUBLIC_TEAM_ID)]);
    current = status({ teams: [team(PUBLIC_TEAM_ID, "public", true), team(BETA, "Beta")] });
    const user = userEvent.setup();
    render(<SkillHubPage />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "Team" }), "Beta");
    expect(await screen.findByText("No skills match your filters.")).toBeInTheDocument();

    // Beta left, from Settings: one team is left, so the filter goes, and narrows nothing.
    current = status({ teams: [team(PUBLIC_TEAM_ID, "public", true)] });
    await act(() => refreshCollaborationStatus());
    expect(await screen.findByText("public-hub")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Team" })).not.toBeInTheDocument();
  });
});

describe("publishing a private one into a team", () => {
  it("offers every team, the public one first, and picks none", async () => {
    const user = userEvent.setup();
    render(<PublishToTeamModal kind="skill" name="salt" onClose={() => {}} onChanged={() => {}} />);
    const select = await screen.findByRole("combobox", { name: "Team" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a team…",
      "Public",
      "Alpha",
      "Beta",
    ]);
    expect(select).toHaveValue("");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.queryByText(/visible to everyone/)).not.toBeInTheDocument();

    await user.selectOptions(select, "Public");
    expect(screen.getByText(/visible to everyone/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled();
  });

  it("says a project's skills and knowledge bases come along, and lists what was published", async () => {
    routes["POST /api/projects/salt-work/publish-to-team"] = () =>
      json({
        team_id: ALPHA,
        published: [
          { kind: "skill", name: "salt-density", local_name: "salt-density", how: "published" },
          { kind: "knowledge-base", name: "salt-papers", local_name: "salt-papers-2", how: "imported", kept_for: ["old-lab"] },
          { kind: "project", name: "salt-work", local_name: "salt-work", how: "published" },
        ],
      });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<PublishToTeamModal kind="project" name="salt-work" onClose={() => {}} onChanged={onChanged} />);
    expect(await screen.findByText(/published into the\s+same team with it/)).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Team" }), "Alpha");
    expect(screen.getByText(/team Alpha: only its members/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Publish" }));

    const list = await screen.findByRole("list", { name: "Published" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[1]).toHaveTextContent("knowledge base salt-papers");
    expect(items[1]).toHaveTextContent("here as salt-papers-2");
    expect(items[1]).toHaveTextContent("for old-lab, in a team you have left, which uses it");
    expect(items[0]).not.toHaveTextContent("here as");
    expect(screen.getByRole("status")).toHaveTextContent("Published to Alpha");
    expect(calls).toContainEqual({
      method: "POST",
      url: "/api/projects/salt-work/publish-to-team",
      body: { team_id: ALPHA },
    });
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("says when a published project's forum could not be linked", async () => {
    routes["POST /api/projects/salt-work/publish-to-team"] = () =>
      json({
        team_id: ALPHA,
        published: [{ kind: "project", name: "salt-work", local_name: "salt-work", how: "published" }],
        forum_error: "The project is published, but its forum was not linked to it: boom",
      });
    const user = userEvent.setup();
    render(<PublishToTeamModal kind="project" name="salt-work" onClose={() => {}} onChanged={() => {}} />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "Team" }), "Alpha");
    await user.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Published to Alpha");
    expect(screen.getByRole("alert")).toHaveTextContent("its forum was not linked to it: boom");
  });

  it("does not explain a project's skills for a skill", async () => {
    render(<PublishToTeamModal kind="skill" name="salt" onClose={() => {}} onChanged={() => {}} />);
    await screen.findByRole("combobox", { name: "Team" });
    expect(screen.queryByText(/same team with it/)).not.toBeInTheDocument();
  });

  it("shows the backend's sentence when it is refused, and has the page and teams asked again", async () => {
    routes["POST /api/knowledge-bases/salt-papers/publish-to-team"] = () =>
      json({ detail: "The knowledge base salt-papers is in a team already." }, 400);
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(
      <PublishToTeamModal kind="knowledge-base" name="salt-papers" label="Salt papers" onClose={() => {}} onChanged={onChanged} />,
    );
    await user.selectOptions(await screen.findByRole("combobox", { name: "Team" }), "Alpha");
    await user.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("is in a team already.");
    // It may have left something in the team part way: the page lists again.
    expect(onChanged).toHaveBeenCalledOnce();
    await waitFor(() =>
      expect(calls.filter((c) => c.url === "/api/collaboration/status").length).toBeGreaterThan(1),
    );
  });
});
