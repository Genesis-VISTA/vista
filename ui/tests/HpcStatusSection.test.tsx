import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HpcStatusSection, STATE_LABELS } from "@/components/HpcStatusSection";
import type {
  HpcCheck,
  HpcCluster,
  HpcClusterStatus,
  HpcDisplayState,
  HpcState,
  HpcStatusView,
} from "@/lib/hpc-status";

const { useHpcStatusMock } = vi.hoisted(() => ({ useHpcStatusMock: vi.fn() }));
vi.mock("@/lib/hpc-status", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hpc-status")>()),
  useHpcStatus: useHpcStatusMock,
}));

const NOW = Date.parse("2026-09-25T15:00:00Z");
const OK: HpcCheck = { ok: true, reason: null, message: "ok" };

function status(
  cluster: HpcCluster,
  state: HpcState,
  checks: Partial<HpcClusterStatus["checks"]> = {},
): HpcClusterStatus {
  return {
    cluster,
    state,
    checked_at: "2026-09-25T14:58:00Z",
    checks: {
      facility: OK,
      credential: OK,
      globus: cluster === "perlmutter" ? null : OK,
      ...checks,
    },
  };
}

const recheck = vi.fn(async () => {});

function view(
  entries: Array<{ status: HpcClusterStatus; state?: HpcDisplayState; rechecking?: boolean }> | null,
  extra: Partial<HpcStatusView> = {},
): HpcStatusView {
  return {
    clusters:
      entries?.map((e) => ({
        cluster: e.status.cluster,
        state: e.state ?? e.status.state,
        status: e.status,
        rechecking: e.rechecking ?? false,
      })) ?? null,
    lastSuccessAt: entries ? NOW - 2 * 60_000 : null,
    failing: false,
    unavailable: false,
    now: NOW,
    recheck,
    ...extra,
  };
}

const onOpenSettings = vi.fn();

function renderSection(props: { collapsed?: boolean; visible?: HpcCluster[] } = {}) {
  return render(
    <nav className="nav-rail">
      <HpcStatusSection
        collapsed={props.collapsed ?? false}
        visibleClusters={props.visible ?? ["frontier", "odo", "perlmutter"]}
        onOpenSettings={onOpenSettings}
        tipProps={() => ({})}
      />
    </nav>,
  );
}

beforeEach(() => {
  useHpcStatusMock.mockReset();
  recheck.mockClear();
  onOpenSettings.mockClear();
});

describe("HpcStatusSection", () => {
  const every: HpcState[] = [
    "ready",
    "degraded",
    "unverifiable",
    "not_connected",
    "rejected",
    "wrong_project",
    "globus_not_connected",
    "globus_session_expired",
  ];

  it.each(every)("renders %s with its word and its own dot", (state) => {
    useHpcStatusMock.mockReturnValue(view([{ status: status("odo", state) }]));
    renderSection({ visible: ["odo"] });
    const card = screen.getByRole("button", { name: `Odo: ${STATE_LABELS[state]}` });
    expect(card).toHaveTextContent(STATE_LABELS[state]);
    expect(card.querySelector(".hpc-dot")).toHaveAttribute("data-state", state);
  });

  it("gives every state a distinct dot class", () => {
    const classes = new Set(every.map((s) => `hpc-dot--${s}`));
    expect(classes.size).toBe(every.length);
  });

  it("shows Checking before the first answer, and Couldn't verify if it failed", () => {
    useHpcStatusMock.mockReturnValue(view(null));
    const { unmount } = renderSection({ visible: ["frontier"] });
    expect(screen.getByRole("button", { name: "Frontier: Checking…" })).toBeInTheDocument();
    unmount();

    useHpcStatusMock.mockReturnValue(view(null, { failing: true, unavailable: true }));
    renderSection({ visible: ["frontier"] });
    expect(screen.getByRole("button", { name: "Frontier: Couldn't verify" })).toBeInTheDocument();
  });

  it("labels collapsed cards with cluster and state", () => {
    useHpcStatusMock.mockReturnValue(
      view([{ status: status("frontier", "ready") }, { status: status("perlmutter", "not_connected") }]),
    );
    renderSection({ collapsed: true, visible: ["frontier", "perlmutter"] });
    expect(screen.getByRole("button", { name: "Frontier: Ready" })).toHaveTextContent("Fr");
    expect(screen.getByRole("button", { name: "Perlmutter: Not connected" })).toHaveTextContent("Pm");
  });

  it("renders nothing when every cluster is hidden", () => {
    useHpcStatusMock.mockReturnValue(view([]));
    const { container } = renderSection({ visible: [] });
    expect(container.querySelector(".hpc-card, .hpc-section-head")).toBeNull();
  });

  it("opens details listing each check, with the S3M expiry, and rechecks", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("frontier", "ready", {
            credential: { ...OK, project: "chm243", expires_at: "2026-09-26T13:00:00Z" },
            globus: { ...OK, identity: "own" },
          }),
        },
      ]),
    );
    renderSection({ visible: ["frontier"] });
    await userEvent.click(screen.getByRole("button", { name: "Frontier: Ready" }));

    const dialog = screen.getByRole("dialog", { name: "Frontier connection details" });
    expect(within(dialog).getByText("Facility is up")).toBeInTheDocument();
    expect(within(dialog).getByText("S3M token accepted")).toBeInTheDocument();
    expect(within(dialog).getByText("Project chm243 · expires in 22 h")).toBeInTheDocument();
    expect(within(dialog).getByText("Your own identity")).toBeInTheDocument();
    expect(within(dialog).getByText("Checked 2 min ago")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Recheck" }));
    expect(recheck).toHaveBeenCalledWith("frontier");
  });

  it("says why a failing cluster is failing and links to its settings", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("frontier", "wrong_project", {
            credential: {
              ok: false,
              reason: "wrong_project",
              message: "This token is for project 'gen150-vista'; Frontier needs a token minted in 'chm243'.",
              project: "gen150-vista",
              expected_project: "chm243",
            },
          }),
        },
      ]),
    );
    renderSection({ visible: ["frontier"] });
    await userEvent.click(screen.getByRole("button", { name: "Frontier: Wrong project" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Token is for another project")).toBeInTheDocument();
    expect(within(dialog).getByText(/Frontier needs a token minted in 'chm243'/)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Settings" }));
    expect(onOpenSettings).toHaveBeenCalledWith("frontier");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("a Not connected card still opens details with the facility status", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("perlmutter", "not_connected", {
            credential: { ok: false, reason: "not_connected", message: "No NERSC IRI token is saved for Perlmutter." },
          }),
        },
      ]),
    );
    renderSection({ visible: ["perlmutter"] });
    await userEvent.click(screen.getByRole("button", { name: "Perlmutter: Not connected" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Facility is up")).toBeInTheDocument();
    expect(within(dialog).getByText("No NERSC IRI token saved")).toBeInTheDocument();
  });

  it("never mentions an expiry for Perlmutter or Globus", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        { status: status("perlmutter", "ready") },
        { status: status("odo", "ready", { globus: { ...OK, identity: "deployment" } }) },
      ]),
    );
    renderSection({ visible: ["perlmutter", "odo"] });
    await userEvent.click(screen.getByRole("button", { name: "Perlmutter: Ready" }));
    expect(screen.getByRole("dialog")).not.toHaveTextContent(/expire/i);
    await userEvent.click(screen.getByRole("button", { name: "Odo: Ready" }));
    const odo = screen.getByRole("dialog", { name: "Odo connection details" });
    expect(within(odo).getByText("The deployment's shared identity")).toBeInTheDocument();
    expect(odo).not.toHaveTextContent(/expire|lapse|3 days/i);
  });

  it("notes a stale result in the section header", () => {
    useHpcStatusMock.mockReturnValue(
      view([{ status: status("odo", "ready") }], { failing: true, lastSuccessAt: NOW - 12 * 60_000 }),
    );
    renderSection({ visible: ["odo"] });
    expect(screen.getByText("checked 12 min ago")).toBeInTheDocument();
  });

  it("closes the details on Escape", async () => {
    useHpcStatusMock.mockReturnValue(view([{ status: status("odo", "ready") }]));
    renderSection({ visible: ["odo"] });
    await userEvent.click(screen.getByRole("button", { name: "Odo: Ready" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
