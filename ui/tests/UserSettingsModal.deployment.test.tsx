import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserSettingsModal } from "@/components/UserSettingsModal";
import type { AgentSettings } from "@/lib/agent-settings";
import type { CollaborationView } from "@/lib/collaboration";
import type { HpcCluster, HpcClusterStatus, HpcStatusView } from "@/lib/hpc-status";
import type { LocalSettings } from "@/lib/settings";
import type { VersionInfo } from "@/lib/version";

/**
 * The settings modal under each deployment (openspec change
 * `multi-user-hosted`): a hosted VISTA hides in-app updates, NERSC and the
 * Custom provider; desktop shows them. Kept apart from
 * `UserSettingsModal.test.tsx` so the two do not collide.
 */

const { fetchSettingsMock, updateSettingsMock } = vi.hoisted(() => ({
  fetchSettingsMock: vi.fn(),
  updateSettingsMock: vi.fn(),
}));
vi.mock("@/lib/settings", () => ({
  fetchSettings: fetchSettingsMock,
  updateSettings: updateSettingsMock,
  startGlobusLogin: vi.fn(),
  completeGlobusLogin: vi.fn(),
}));

const { recheckMock, useHpcStatusMock } = vi.hoisted(() => ({
  recheckMock: vi.fn(async () => {}),
  useHpcStatusMock: vi.fn(),
}));
vi.mock("@/lib/hpc-status", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hpc-status")>()),
  recheckHpcStatus: recheckMock,
  refreshHpcStatus: vi.fn(async () => {}),
  useHpcStatus: useHpcStatusMock,
}));

const { useAgentSettingsMock } = vi.hoisted(() => ({ useAgentSettingsMock: vi.fn() }));
vi.mock("@/lib/agent-settings", () => ({
  useAgentSettings: useAgentSettingsMock,
  refreshAgentSettings: vi.fn(async () => {}),
}));

const { useCollaborationMock } = vi.hoisted(() => ({ useCollaborationMock: vi.fn() }));
vi.mock("@/lib/collaboration", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/collaboration")>();
  return {
    useCollaboration: useCollaborationMock,
    refreshCollaborationStatus: vi.fn(async () => {}),
    teamLabel: actual.teamLabel,
    useTeams: () => actual.teamsView({ status: null, error: null }),
  };
});

const { useVersionMock } = vi.hoisted(() => ({ useVersionMock: vi.fn() }));
vi.mock("@/lib/version", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/version")>()),
  useVersion: useVersionMock,
  checkNow: vi.fn(async () => {}),
  refreshVersion: vi.fn(async () => {}),
  startDownload: vi.fn(async () => {}),
}));

function versionView(info: Partial<VersionInfo> = {}) {
  return {
    info: {
      version: "0.2.3",
      commit: "abc1234def",
      checks: "on" as const,
      checked_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
      latest: null,
      notes_url: null,
      data_reset: null,
      ...info,
    },
    error: null,
    checking: false,
    checkedAt: 0,
    download: null,
  };
}

function collaborationView(): CollaborationView {
  return {
    status: null,
    checking: false,
    enabled: true,
    shown: false,
    default_server: false,
    available: false,
    reason: "Checking the collaboration server…",
    error: null,
    refresh: vi.fn(async () => {}),
  };
}

function settingsRow(overrides: Partial<LocalSettings> = {}): LocalSettings {
  return {
    install_id: "00000000-0000-4000-8000-000000000001",
    display_name: null,
    collaboration_server_url: null,
    email: "researcher@ornl.gov",
    inference_model: null,
    inference_base_url: null,
    nersc_account: null,
    nersc_remote_dir: null,
    odo_remote_dir: null,
    frontier_remote_dir: null,
    lux_remote_dir: null,
    hpc_hidden_clusters: [],
    ...overrides,
  };
}

function statusView(): HpcStatusView {
  const ok = { ok: true, reason: null, message: "ok" };
  const entry = (
    cluster: HpcCluster,
    state: "ready" | "not_connected",
    checks: Partial<HpcClusterStatus["checks"]> = {},
  ) => ({
    cluster,
    state,
    rechecking: false,
    status: {
      cluster,
      state,
      checked_at: "2026-09-25T15:00:00Z",
      checks: { facility: ok, credential: ok, globus: null, settings: ok, ...checks },
    },
  });
  return {
    clusters: [
      entry("frontier", "ready"),
      entry("odo", "ready"),
      entry("perlmutter", "not_connected"),
      entry("lux", "ready"),
    ],
    lastSuccessAt: 0,
    failing: false,
    unavailable: false,
    now: 0,
    recheck: recheckMock,
  };
}

function agentSettings(overrides: Partial<AgentSettings> = {}): AgentSettings {
  return {
    providers: [
      { id: "mag", name: "AmSC MAG", takesUrl: false, defaultModel: "claude-sonnet" },
      { id: "olcf", name: "OLCF Inference", takesUrl: false, defaultModel: "gpt-oss-120b" },
      { id: "custom", name: "Custom", takesUrl: true, defaultModel: null },
    ],
    provider: "mag",
    source: "default",
    baseUrl: "https://mag.genesis.americansciencecloud.org/v1",
    model: "openai:claude-sonnet",
    modelIsDefault: true,
    hasCredential: true,
    keysSet: { mag: true, olcf: false, custom: false },
    configKeySet: false,
    ...overrides,
  };
}

beforeEach(() => {
  fetchSettingsMock.mockReset();
  fetchSettingsMock.mockResolvedValue(settingsRow());
  useCollaborationMock.mockReturnValue(collaborationView());
  useHpcStatusMock.mockReturnValue(statusView());
  useAgentSettingsMock.mockReturnValue({ settings: agentSettings(), error: null, modelChoiceRequest: 0 });
});

it("has no Sign out when hosted with no sign-out address configured", () => {
  useVersionMock.mockReturnValue(versionView({ deployment: "hosted", sign_out_url: null }));
  render(<UserSettingsModal onClose={() => {}} />);
  expect(within(nav()).queryByRole("link", { name: "Sign out" })).toBeNull();
});

const nav = () => screen.getByRole("navigation", { name: "Settings sections" });
const entry = (name: string) => within(nav()).getByRole("button", { name: new RegExp(`^${name}\\b`) });

describe.each(["desktop", "hosted"] as const)("UserSettingsModal on a %s deployment", (deployment) => {
  const hosted = deployment === "hosted";

  beforeEach(() => {
    useVersionMock.mockReturnValue(versionView({ deployment }));
  });

  it(hosted ? "hides in-app updates" : "shows in-app updates", async () => {
    render(<UserSettingsModal onClose={() => {}} />);
    await userEvent.click(entry("About"));
    const about = await screen.findByRole("region", { name: "About" });

    expect(within(about).getByRole("textbox", { name: "Installed version" })).toBeInTheDocument();
    for (const name of ["Check for updates", "Include prereleases"]) {
      const control = within(about).queryByRole("switch", { name });
      if (hosted) expect(control).toBeNull();
      else expect(control).toBeInTheDocument();
    }
    const check = within(about).queryByRole("button", { name: "Check now" });
    if (hosted) expect(check).toBeNull();
    else expect(check).toBeInTheDocument();
  });

  it(hosted ? "offers Sign out under Research profile" : "has no Sign out", async () => {
    useVersionMock.mockReturnValue(versionView({ deployment, sign_out_url: "https://gw.example/logout" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const signOut = within(nav()).queryByRole("link", { name: "Sign out" });
    if (!hosted) {
      expect(signOut).toBeNull();
      return;
    }
    expect(signOut).toHaveAttribute("href", "https://gw.example/logout");
    // Right after Research profile in the section list.
    const items = Array.from(nav().querySelectorAll(".settings-nav-entry"));
    const profile = items.indexOf(entry("Research profile"));
    expect(items[profile + 1]).toBe(signOut);
  });

  it(hosted ? "does not offer NERSC" : "offers NERSC", async () => {
    render(<UserSettingsModal onClose={() => {}} />);
    await userEvent.click(entry("Compute"));
    const compute = await screen.findByRole("region", { name: "Compute" });

    const perlmutter = within(compute).queryByRole("button", { name: /^Perlmutter/ });
    if (hosted) expect(perlmutter).toBeNull();
    else expect(perlmutter).toBeInTheDocument();
    expect(within(compute).getByRole("button", { name: /^Frontier/ })).toBeInTheDocument();
  });

  it(hosted ? "does not offer the Custom provider" : "offers the Custom provider", async () => {
    render(<UserSettingsModal onClose={() => {}} />);
    await userEvent.click(entry("Models and providers"));
    const models = await screen.findByRole("region", { name: "Models & providers" });

    const custom = within(models).queryByRole("radio", { name: /^Custom/ });
    if (hosted) expect(custom).toBeNull();
    else expect(custom).toBeInTheDocument();
    expect(within(models).getByRole("radio", { name: /^AmSC MAG/ })).toBeInTheDocument();
  });
});
