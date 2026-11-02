import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PUBLIC_TEAM_ID,
  effectiveTeam,
  emptyTeamNote,
  inSelectedTeam,
  publishAudience,
  resetCollaborationForTests,
  teamsView,
  usableInProject,
  type CollaborationStatus,
  type CollaborationTeam,
} from "@/lib/collaboration";
import { refreshProjects, type ProjectPublic } from "@/lib/projects";
import { TeamSwitcher } from "@/components/TeamSwitcher";
import { TeamsModal } from "@/components/TeamsModal";
import { CopyToTeamModal } from "@/components/CopyToTeamModal";
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
    signed_in: true,
    sign_in_required: false,
    sign_in_url: null,
    user: { sub: "me", email: "me@ornl.gov", name: "Me" },
    teams: TEAMS,
    team_id: null,
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
    "PUT /api/settings": (init) => {
      const body = JSON.parse(String(init?.body)) as { collaboration_team_id: string | null };
      current = { ...current, team_id: body.collaboration_team_id };
      return json({ install_id: "i1", collaboration_team_id: body.collaboration_team_id });
    },
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

describe("which resources a team shows", () => {
  it("shows exactly the selected team's, the public team's not mixed in", () => {
    expect(inSelectedTeam(ALPHA, ALPHA, KNOWN)).toBe(true);
    expect(inSelectedTeam(PUBLIC_TEAM_ID, ALPHA, KNOWN)).toBe(false);
    expect(inSelectedTeam(null, ALPHA, KNOWN)).toBe(false);
    expect(inSelectedTeam(BETA, ALPHA, KNOWN)).toBe(false);
  });

  it("shows only the public team's with the public team selected", () => {
    expect(inSelectedTeam(PUBLIC_TEAM_ID, PUBLIC_TEAM_ID, KNOWN)).toBe(true);
    expect(inSelectedTeam(null, PUBLIC_TEAM_ID, KNOWN)).toBe(false);
    expect(inSelectedTeam(ALPHA, PUBLIC_TEAM_ID, KNOWN)).toBe(false);
  });

  it("shows only private ones with Private selected", () => {
    expect(inSelectedTeam(null, null, KNOWN)).toBe(true);
    expect(inSelectedTeam(PUBLIC_TEAM_ID, null, KNOWN)).toBe(false);
    expect(inSelectedTeam(ALPHA, null, KNOWN)).toBe(false);
  });

  it("counts a team the researcher is not in as private", () => {
    expect(effectiveTeam(GONE, KNOWN)).toBeNull();
    expect(inSelectedTeam(GONE, null, KNOWN)).toBe(true);
    expect(inSelectedTeam(GONE, ALPHA, KNOWN)).toBe(false);
  });

  it("filters nothing without a signed-in server", () => {
    const view = teamsView({ status: status({ signed_in: false, teams: [] }), error: null });
    expect(view.shown).toBe(false);
    expect(view.show(BETA)).toBe(true);
    expect(view.show(null)).toBe(true);
    expect(teamsView({ status: null, error: null }).show(BETA)).toBe(true);
  });

  it("filters by the selected team with one", () => {
    const view = teamsView({ status: status({ team_id: ALPHA }), error: null });
    expect(view.shown).toBe(true);
    expect(view.selected?.name).toBe("Alpha");
    expect(view.show(ALPHA)).toBe(true);
    expect(view.show(null)).toBe(false);
    expect(view.show(PUBLIC_TEAM_ID)).toBe(false);
    expect(view.teamName(BETA)).toBe("Beta");
    expect(view.teamName(PUBLIC_TEAM_ID)).toBe("Public");
    expect(view.teamName(null)).toBeNull();
  });

  it("selects Private when the selected team is null", () => {
    const view = teamsView({ status: status(), error: null });
    expect(view.selectedId).toBeNull();
    expect(view.selected).toBeNull();
  });

  it("says why a server listing is empty for the team", () => {
    expect(emptyTeamNote(teamsView({ status: status(), error: null }), "skills")).toMatch(
      /^Private is this computer only/,
    );
    expect(
      emptyTeamNote(teamsView({ status: status({ team_id: ALPHA }), error: null }), "skills"),
    ).toBe("There are no skills in Alpha on the server.");
  });
});

describe("what a project can use", () => {
  it("is exactly its own team's, as the backend enforces", () => {
    expect(usableInProject(null, null)).toBe(true);
    expect(usableInProject(ALPHA, ALPHA)).toBe(true);
    expect(usableInProject(null, ALPHA)).toBe(false);
    expect(usableInProject(PUBLIC_TEAM_ID, ALPHA)).toBe(false);
    expect(usableInProject(ALPHA, null)).toBe(false);
    // Unlike the listings, a team the researcher left is still another team.
    expect(usableInProject(GONE, null)).toBe(false);
  });
});

describe("publishing", () => {
  it("says which team a publish goes to, and that a private one is not published", () => {
    const view = teamsView({ status: status(), error: null });
    expect(publishAudience(PUBLIC_TEAM_ID, view)).toMatch(/public team, visible to everyone/);
    expect(publishAudience(ALPHA, view)).toMatch(/team Alpha: only its members/);
    expect(publishAudience(null, view)).toMatch(/private, on this computer only: copy it to a team/);
  });
});

describe("the team switcher", () => {
  it("is hidden without a server", async () => {
    current = status({
      configured: false,
      reachable: false,
      reason: "No collaboration server is set.",
      url: null,
      signed_in: false,
      user: null,
      teams: [],
    });
    const { container } = render(<TeamSwitcher />);
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/collaboration/status")).toBe(true),
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("is hidden when not signed in", async () => {
    current = status({ signed_in: false, sign_in_required: true, reason: "Sign in.", teams: [], user: null });
    const { container } = render(<TeamSwitcher />);
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/collaboration/status")).toBe(true),
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists Private, then Public, then the researcher's teams", async () => {
    const user = userEvent.setup();
    render(<TeamSwitcher />);
    await user.click(await screen.findByRole("button", { name: "Team: Private. Switch team" }));
    const options = within(screen.getByRole("listbox", { name: "Teams" })).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Private", "Public", "Alpha", "Beta"]);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Manage teams…" })).toBeInTheDocument();
  });

  it("selects a team in Settings", async () => {
    const user = userEvent.setup();
    render(<TeamSwitcher />);
    await user.click(await screen.findByRole("button", { name: /Switch team/ }));
    await user.click(screen.getByRole("option", { name: "Alpha" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Team: Alpha. Switch team" })).toBeInTheDocument(),
    );
    expect(calls).toContainEqual({
      method: "PUT",
      url: "/api/settings",
      body: { collaboration_team_id: ALPHA },
    });
    // And asks the backend again for its word on it.
    expect(calls.filter((c) => c.url === "/api/collaboration/status").length).toBeGreaterThan(1);
  });

  it("selects Private as null", async () => {
    current = status({ team_id: BETA });
    const user = userEvent.setup();
    render(<TeamSwitcher />);
    await user.click(await screen.findByRole("button", { name: "Team: Beta. Switch team" }));
    await user.click(screen.getByRole("option", { name: "Private" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "PUT",
        url: "/api/settings",
        body: { collaboration_team_id: null },
      }),
    );
  });

  it("selects the public team by its id", async () => {
    const user = userEvent.setup();
    render(<TeamSwitcher />);
    await user.click(await screen.findByRole("button", { name: /Switch team/ }));
    await user.click(screen.getByRole("option", { name: "Public" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Team: Public. Switch team" })).toBeInTheDocument(),
    );
    expect(calls).toContainEqual({
      method: "PUT",
      url: "/api/settings",
      body: { collaboration_team_id: PUBLIC_TEAM_ID },
    });
  });
});

describe("managing teams", () => {
  beforeEach(() => {
    current = status({ team_id: ALPHA });
    routes[`GET /api/collaboration/teams/${ALPHA}/members`] = () =>
      json([{ sub: "me", email: "me@ornl.gov", name: "Me", added_by: "me", added_at: "x" }]);
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

  it("lists the selected team's members", async () => {
    render(<TeamsModal onClose={() => {}} />);
    const list = await screen.findByRole("list", { name: "Members" });
    expect(within(list).getByText("Me (you)")).toBeInTheDocument();
  });

  it("searches people as you type, by name and email, and adds the one picked", async () => {
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await screen.findByRole("list", { name: "Members" });

    await user.type(screen.getByRole("combobox"), "bo");
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
    await user.type(screen.getByRole("combobox"), "ornl");
    await screen.findByRole("option", { name: /Bob Builder/ });
    expect(screen.queryByRole("option", { name: /^Me/ })).not.toBeInTheDocument();
  });

  it("makes a team and selects it", async () => {
    routes["POST /api/collaboration/teams"] = () => json(team(BETA, "Beta"), 201);
    current = status();
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await user.type(screen.getByRole("textbox", { name: "New team name" }), "Beta");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "PUT",
        url: "/api/settings",
        body: { collaboration_team_id: BETA },
      }),
    );
    expect(calls).toContainEqual({ method: "POST", url: "/api/collaboration/teams", body: { name: "Beta" } });
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
      current = status();
      return new Response(null, { status: 204 });
    };
    const user = userEvent.setup();
    render(<TeamsModal onClose={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Leave Alpha" }));
    expect(await screen.findByText("You left Alpha. Private is selected.")).toBeInTheDocument();
  });
});

describe("a project's skill and knowledge base pickers", () => {
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

  it("offers only what an edited project's team can use", async () => {
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
  });

  it("offers a new project what the team selected in Settings can use", async () => {
    routes["GET /api/settings"] = () => json({ install_id: "i1", collaboration_team_id: BETA });
    await refreshProjects();
    const user = userEvent.setup();
    render(<ProjectsPage />);
    await user.click(await screen.findByRole("button", { name: /new project/i }));
    const dialog = await screen.findByRole("dialog", { name: "Create project" });
    await within(dialog).findByText("beta-skill");
    expect(within(dialog).queryByText("public-skill")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("alpha-skill")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("KB alpha-kb")).not.toBeInTheDocument();
    expect(within(dialog).getByText("KB beta-kb")).toBeInTheDocument();
  });

  it("offers a private project only private ones", async () => {
    routes["GET /api/settings"] = () => json({ install_id: "i1", collaboration_team_id: null });
    await refreshProjects();
    const user = userEvent.setup();
    render(<ProjectsPage />);
    await user.click(await screen.findByRole("button", { name: /new project/i }));
    const dialog = await screen.findByRole("dialog", { name: "Create project" });
    await within(dialog).findByText("private-skill");
    expect(within(dialog).queryByText("public-skill")).not.toBeInTheDocument();
    expect(within(dialog).getByText("KB private-kb")).toBeInTheDocument();
    expect(within(dialog).queryByText("KB public-kb")).not.toBeInTheDocument();
  });

  it("lists only the selected team's projects", async () => {
    current = status({ team_id: BETA });
    routes["GET /api/projects"] = () =>
      json([
        project("alpha-work", ALPHA),
        project("shared-work", PUBLIC_TEAM_ID),
        project("own-work", null),
        project("beta-work", BETA),
      ]);
    await refreshProjects();
    render(<ProjectsPage />);
    expect(await screen.findByText("beta-work")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("alpha-work")).not.toBeInTheDocument());
    expect(screen.queryByText("shared-work")).not.toBeInTheDocument();
    expect(screen.queryByText("own-work")).not.toBeInTheDocument();
    expect(screen.getByText(/3 projects are in another/)).toBeInTheDocument();
  });

  it("offers Copy to team, not Publish, on a private project", async () => {
    routes["GET /api/projects"] = () => json([project("own-work", null)]);
    await refreshProjects();
    render(<ProjectsPage />);
    expect(await screen.findByRole("button", { name: "Copy to team…" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Publish/ })).not.toBeInTheDocument();
  });

  it("keeps Publish on a team's project", async () => {
    current = status({ team_id: ALPHA });
    await refreshProjects();
    render(<ProjectsPage />);
    expect(await screen.findByRole("button", { name: "Publish" })).toBeInTheDocument();
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

  it("lists only the selected team's skills", async () => {
    current = status({ team_id: ALPHA });
    routes["GET /api/collaboration/skills"] = () =>
      json([remote("1", "alpha-hub", ALPHA), remote("2", "public-hub", PUBLIC_TEAM_ID), remote("3", "beta-hub", BETA)]);
    render(<SkillHubPage />);
    expect(await screen.findByText("alpha-hub")).toBeInTheDocument();
    expect(screen.queryByText("public-hub")).not.toBeInTheDocument();
    expect(screen.queryByText("beta-hub")).not.toBeInTheDocument();
  });

  it("says there is nothing in the team when the filter hides everything", async () => {
    current = status({ team_id: BETA });
    routes["GET /api/collaboration/skills"] = () => json([remote("1", "alpha-hub", ALPHA)]);
    render(<SkillHubPage />);
    expect(await screen.findByText("There are no skills in Beta on the server.")).toBeInTheDocument();
    expect(screen.queryByText(/published yet/)).not.toBeInTheDocument();
  });
});

describe("copying to a team", () => {
  it("offers every team but the resource's own", async () => {
    render(<CopyToTeamModal kind="skill" name="salt" teamId={ALPHA} onClose={() => {}} onCopied={() => {}} />);
    const select = await screen.findByRole("combobox", { name: "Team" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Public", "Beta"]);
  });

  it("says a project's skills and knowledge bases come along, and lists what was copied", async () => {
    routes["POST /api/projects/salt-work/copy"] = () =>
      json({
        team_id: ALPHA,
        copied: [
          { kind: "skill", name: "salt-density", local_name: "salt-density", how: "published" },
          { kind: "knowledge-base", name: "salt-papers", local_name: "salt-papers-2", how: "imported" },
          { kind: "project", name: "salt-work", local_name: "salt-work", how: "published" },
        ],
      });
    const onCopied = vi.fn();
    const user = userEvent.setup();
    render(<CopyToTeamModal kind="project" name="salt-work" teamId={null} onClose={() => {}} onCopied={onCopied} />);
    expect(await screen.findByText(/its skills and knowledge bases are copied with it/)).toBeInTheDocument();
    expect(screen.getByText(/imported beside it under a new name/)).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Team" }), "Alpha");
    await user.click(screen.getByRole("button", { name: "Copy" }));

    const list = await screen.findByRole("list", { name: "Copied" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[1]).toHaveTextContent("knowledge base salt-papers");
    expect(items[1]).toHaveTextContent("here as salt-papers-2");
    expect(items[0]).not.toHaveTextContent("here as");
    expect(screen.getByRole("status")).toHaveTextContent("Copied to Alpha");
    expect(calls).toContainEqual({
      method: "POST",
      url: "/api/projects/salt-work/copy",
      body: { team_id: ALPHA },
    });
    expect(onCopied).toHaveBeenCalledOnce();
  });

  it("does not explain a project's skills for a skill", async () => {
    render(<CopyToTeamModal kind="skill" name="salt" teamId={null} onClose={() => {}} onCopied={() => {}} />);
    await screen.findByRole("combobox", { name: "Team" });
    expect(screen.queryByText(/copied with it/)).not.toBeInTheDocument();
  });

  it("shows the backend's sentence when the copy is refused", async () => {
    routes["POST /api/knowledge-bases/salt-papers/copy"] = () =>
      json({ detail: "Salt papers is in that team already." }, 400);
    const onCopied = vi.fn();
    const user = userEvent.setup();
    render(
      <CopyToTeamModal kind="knowledge-base" name="salt-papers" label="Salt papers" teamId={null} onClose={() => {}} onCopied={onCopied} />,
    );
    await user.click(await screen.findByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Salt papers is in that team already.");
    expect(onCopied).not.toHaveBeenCalled();
  });
});
