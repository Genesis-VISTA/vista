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
      globus: cluster === "perlmutter" || cluster === "lux" ? null : OK,
      settings: { ...OK, message: `/proj-shared/${cluster}` },
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
            credential: { ...OK, project: "abc123", expires_at: "2026-09-26T13:00:00Z" },
            globus: OK,
          }),
        },
      ]),
    );
    renderSection({ visible: ["frontier"] });
    await userEvent.click(screen.getByRole("button", { name: "Frontier: Ready" }));

    const dialog = screen.getByRole("dialog", { name: "Frontier connection details" });
    expect(within(dialog).getByText("Facility is up")).toBeInTheDocument();
    expect(within(dialog).getByText("S3M token accepted")).toBeInTheDocument();
    expect(within(dialog).getByText("Project abc123 · expires in 22 h")).toBeInTheDocument();
    expect(within(dialog).getByText("Your own identity")).toBeInTheDocument();
    expect(within(dialog).getByText("Checked 2 min ago")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Recheck" }));
    expect(recheck).toHaveBeenCalledWith("frontier");
  });

  it("says why a failing cluster is failing and links to its settings", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("frontier", "rejected", {
            credential: {
              ok: false,
              reason: "rejected",
              message: "S3M rejected the Frontier token; it may have expired or been revoked.",
              http_status: 401,
            },
          }),
        },
      ]),
    );
    renderSection({ visible: ["frontier"] });
    await userEvent.click(screen.getByRole("button", { name: "Frontier: Token rejected" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/may have expired or been revoked/)).toBeInTheDocument();

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

  it("a cluster with a token but no remote directory is Not connected, and says so on hover", async () => {
    const missing = "No Odo remote directory is set.";
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("odo", "not_connected", {
            settings: { ok: false, reason: "not_connected", message: missing },
          }),
        },
      ]),
    );
    renderSection({ visible: ["odo"] });
    const card = screen.getByRole("button", { name: "Odo: Not connected" });
    expect(card).toHaveAttribute("title", missing);

    await userEvent.click(card);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("S3M token accepted")).toBeInTheDocument();
    expect(within(dialog).getByText("Settings incomplete")).toBeInTheDocument();
    expect(within(dialog).getByText(missing)).toBeInTheDocument();
  });

  it("names an unusable setting and shows the folder once it is set", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("frontier", "not_connected", {
            settings: {
              ok: false,
              reason: "invalid",
              message: "The Frontier remote directory /lustre/orion/<project>/vista can't be used.",
            },
          }),
        },
        { status: status("odo", "ready") },
      ]),
    );
    renderSection({ visible: ["frontier", "odo"] });
    await userEvent.click(screen.getByRole("button", { name: "Frontier: Not connected" }));
    expect(screen.getByText("A setting can't be used")).toBeInTheDocument();

    const odo = screen.getByRole("button", { name: "Odo: Ready" });
    expect(odo).not.toHaveAttribute("title");
    await userEvent.click(odo);
    const dialog = screen.getByRole("dialog", { name: "Odo connection details" });
    expect(within(dialog).getByText("Remote directory set")).toBeInTheDocument();
    expect(within(dialog).getByText("/proj-shared/odo")).toBeInTheDocument();
  });

  it("never mentions an expiry for Perlmutter or Globus", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        { status: status("perlmutter", "ready") },
        { status: status("odo", "ready", { globus: OK }) },
      ]),
    );
    renderSection({ visible: ["perlmutter", "odo"] });
    await userEvent.click(screen.getByRole("button", { name: "Perlmutter: Ready" }));
    expect(screen.getByRole("dialog")).not.toHaveTextContent(/expire/i);
    await userEvent.click(screen.getByRole("button", { name: "Odo: Ready" }));
    const odo = screen.getByRole("dialog", { name: "Odo connection details" });
    expect(within(odo).getByText("Your own identity")).toBeInTheDocument();
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

describe("HpcStatusSection: Lux", () => {
  const LUX_READY = status("lux", "ready", {
    facility: {
      ...OK,
      message: "The Lux hub hub.ccs.ornl.gov answered (SSH-2.0-OpenSSH_9.9).",
    },
    credential: {
      ...OK,
      message: "Sign in with PIN + RSA passcode when a chat first uses Lux.",
    },
  });

  it("shows a Ready Lux card in green, labelled Lx when collapsed", () => {
    useHpcStatusMock.mockReturnValue(view([{ status: LUX_READY }]));
    const { unmount } = renderSection({ visible: ["lux"] });
    const card = screen.getByRole("button", { name: "Lux: Ready" });
    expect(card.querySelector(".hpc-dot")).toHaveAttribute("data-state", "ready");
    unmount();

    renderSection({ collapsed: true, visible: ["lux"] });
    expect(screen.getByRole("button", { name: "Lux: Ready" })).toHaveTextContent("Lx");
  });

  it("details show the hub and how sign-in works, with no Globus or expiry", async () => {
    useHpcStatusMock.mockReturnValue(view([{ status: LUX_READY }]));
    renderSection({ visible: ["lux"] });
    await userEvent.click(screen.getByRole("button", { name: "Lux: Ready" }));

    const dialog = screen.getByRole("dialog", { name: "Lux connection details" });
    expect(within(dialog).getByText("OLCF · Slurm over SSH")).toBeInTheDocument();
    expect(within(dialog).getByText("Hub is reachable")).toBeInTheDocument();
    expect(within(dialog).getByText(/hub\.ccs\.ornl\.gov answered/)).toBeInTheDocument();
    expect(within(dialog).getByText("Sign in from a chat")).toBeInTheDocument();
    // No project: Lux jobs go to the researcher's default Slurm account.
    expect(
      within(dialog).getByText("Sign in with PIN + RSA passcode when a chat first uses Lux."),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/Project/)).toBeNull();
    expect(within(dialog).queryByText(/Globus/)).toBeNull();
    expect(within(dialog).queryByText(/expires/)).toBeNull();
    expect(within(dialog).queryByText("Facility is up")).toBeNull();
  });

  it("an unreachable hub reads Couldn't verify, not a facility outage", async () => {
    useHpcStatusMock.mockReturnValue(
      view([
        {
          status: status("lux", "unverifiable", {
            facility: {
              ok: false,
              reason: "unreachable",
              message: "The Lux hub hub.ccs.ornl.gov did not answer within 5 s.",
            },
            credential: LUX_READY.checks.credential,
          }),
        },
      ]),
    );
    renderSection({ visible: ["lux"] });
    await userEvent.click(screen.getByRole("button", { name: "Lux: Couldn't verify" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Couldn't reach the hub")).toBeInTheDocument();
    expect(within(dialog).getByText(/did not answer within 5 s/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/degraded/i)).toBeNull();
  });
});
