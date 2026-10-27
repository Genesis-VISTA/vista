import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SkillsPage from "@/app/skills/page";

/*
 * The Skills page under the first-boot tour: a skill counts as read when its
 * detail is closed, the detail stays open while it is read, and the page loads
 * nothing itself.
 */

const { tourPosition, tourReport } = vi.hoisted(() => ({
  tourPosition: { current: null as { step: string; subStep: string } | null },
  tourReport: vi.fn(),
}));
vi.mock("@/components/TourProvider", () => ({
  useTour: () => ({ position: tourPosition.current, report: tourReport }),
}));

const { updateProject } = vi.hoisted(() => ({ updateProject: vi.fn() }));
vi.mock("@/lib/projects", () => ({
  useActiveProject: () => ({ id: 1, name: "molten-salt", skills: ["loaded-one"] }),
  refreshProjects: vi.fn(),
  updateProject,
  toCreate: (p: unknown) => p,
}));
vi.mock("@/lib/collaboration", () => ({
  useCollaboration: () => ({ available: false, reason: "off", status: null }),
}));
vi.mock("@/components/AppTopBar", () => ({
  AppTopBar: ({ actions }: { actions?: React.ReactNode }) => <div>{actions}</div>,
}));
vi.mock("@/components/SkillImportModal", () => ({ SkillImportModal: () => null }));

const SKILLS = [
  { slug: "loaded-one", name: "Loaded One", description: "d1", bundled: true },
  { slug: "other-one", name: "Other One", description: "d2", bundled: true },
];

const fetchMock = vi.fn();

beforeEach(() => {
  tourPosition.current = null;
  tourReport.mockReset();
  updateProject.mockReset();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/skills") return new Response(JSON.stringify(SKILLS));
    if (url.startsWith("/api/skills/")) {
      return new Response(JSON.stringify({ slug: "other-one", frontmatter: {}, markdown: "# What it does" }));
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Skills page during the tour", () => {
  it("carries the tour's anchors on the first skill, the first Load and Import", async () => {
    render(<SkillsPage />);
    await screen.findByText("Other One");
    expect(document.querySelector('[data-tour="skills:open-skill"]')).toHaveTextContent("Loaded One");
    expect(document.querySelector('[data-tour="skills:load"]')?.textContent).toBe("Load");
    expect(document.querySelector('[data-tour="skills:import"]')).toHaveTextContent("Import…");
  });

  it("reports a skill as read only when its detail is closed, and loads nothing", async () => {
    tourPosition.current = { step: "skills", subStep: "open-skill" };
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("Other One"));
    await screen.findByText("What it does");
    expect(tourReport).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByText("What it does")).not.toBeInTheDocument());
    expect(tourReport).toHaveBeenCalledExactlyOnceWith("skill-opened");
    expect(updateProject).not.toHaveBeenCalled();
  });

  it("keeps the detail open while the skill is read", async () => {
    tourPosition.current = { step: "skills", subStep: "open-skill" };
    const view = render(<SkillsPage />);
    await userEvent.click(await screen.findByText("Other One"));
    await screen.findByText("What it does");
    act(() => view.rerender(<SkillsPage />));
    expect(screen.getByText("What it does")).toBeInTheDocument();
  });

  it("leaves the detail open when no tour is running", async () => {
    const view = render(<SkillsPage />);
    await userEvent.click(await screen.findByText("Other One"));
    await screen.findByText("What it does");
    act(() => view.rerender(<SkillsPage />));
    expect(screen.getByText("What it does")).toBeInTheDocument();
    expect(tourReport).not.toHaveBeenCalled();
  });
});
