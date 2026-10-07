import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserSettingsModal } from "@/components/UserSettingsModal";
import { setThemeChoice } from "@/lib/theme";
import type { AgentSettings } from "@/lib/agent-settings";
import type { HpcCluster, HpcClusterStatus, HpcStatusView } from "@/lib/hpc-status";
import type { UserPublicWithConfig } from "@/lib/user";

const { fetchCurrentUserWithConfigMock, updateCurrentUserMock } = vi.hoisted(() => ({
  fetchCurrentUserWithConfigMock: vi.fn(),
  updateCurrentUserMock: vi.fn(),
}));
vi.mock("@/lib/user", () => ({
  fetchCurrentUserWithConfig: fetchCurrentUserWithConfigMock,
  updateCurrentUser: updateCurrentUserMock,
  startGlobusLogin: vi.fn(),
  completeGlobusLogin: vi.fn(),
}));

const { recheckMock, refreshMock, useHpcStatusMock } = vi.hoisted(() => ({
  recheckMock: vi.fn(async () => {}),
  refreshMock: vi.fn(async () => {}),
  useHpcStatusMock: vi.fn(),
}));
vi.mock("@/lib/hpc-status", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hpc-status")>()),
  recheckHpcStatus: recheckMock,
  refreshHpcStatus: refreshMock,
  useHpcStatus: useHpcStatusMock,
}));

const { useAgentSettingsMock, refreshAgentSettingsMock } = vi.hoisted(() => ({
  useAgentSettingsMock: vi.fn(),
  refreshAgentSettingsMock: vi.fn(async () => {}),
}));
vi.mock("@/lib/agent-settings", () => ({
  useAgentSettings: useAgentSettingsMock,
  refreshAgentSettings: refreshAgentSettingsMock,
}));

function user(overrides: Partial<UserPublicWithConfig> = {}): UserPublicWithConfig {
  return {
    id: "u1",
    email: "researcher@ornl.gov",
    is_admin: false,
    inference_model: null,
    inference_base_url: null,
    inference_api_key: null,
    nersc_account: null,
    nersc_remote_dir: null,
    odo_remote_dir: null,
    frontier_remote_dir: null,
    lux_remote_dir: null,
    lux_account: null,
    odo_s3m_token: null,
    frontier_s3m_token: null,
    nersc_iri_token: null,
    globus_token: null,
    odo_globus_token: null,
    frontier_globus_token: null,
    globus_https_token: null,
    odo_globus_https_token: null,
    frontier_globus_https_token: null,
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
      { id: "i2", name: "AmSC i2", takesUrl: false, defaultModel: "claude-sonnet" },
      { id: "mag", name: "AmSC MAG", takesUrl: false, defaultModel: null },
      { id: "olcf", name: "OLCF Inference", takesUrl: false, defaultModel: "gpt-oss-120b" },
      { id: "custom", name: "Custom", takesUrl: true, defaultModel: null },
    ],
    provider: "i2",
    source: "default",
    baseUrl: "https://api.i2-core.american-science-cloud.org",
    model: "openai:claude-sonnet",
    modelIsDefault: true,
    hasCredential: true,
    keysSet: { i2: true, mag: false, olcf: false, custom: false },
    ...overrides,
  };
}

beforeEach(() => {
  fetchCurrentUserWithConfigMock.mockReset();
  updateCurrentUserMock.mockReset();
  updateCurrentUserMock.mockResolvedValue(user());
  recheckMock.mockClear();
  refreshMock.mockClear();
  refreshAgentSettingsMock.mockClear();
  useHpcStatusMock.mockReturnValue(statusView());
  useAgentSettingsMock.mockReturnValue({ settings: agentSettings(), error: null, modelChoiceRequest: 0 });
});

const nav = () => screen.getByRole("navigation", { name: "Settings sections" });

/** A navigation entry, by the start of its accessible name. */
function entry(name: string) {
  return within(nav()).getByRole("button", { name: new RegExp(`^${name}\\b`) });
}

async function region(name: string) {
  return screen.findByRole("region", { name });
}

async function openOn(name: string) {
  await userEvent.click(entry(name));
  return region(name);
}

describe("UserSettingsModal navigation", () => {
  it("lists Appearance, Agent and the resource tree, and opens on Agent", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");

    const names = within(nav())
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label")?.split(",")[0]);
    expect(names).toEqual(["Appearance", "Agent", "Odo", "Frontier", "Lux", "Perlmutter"]);

    const ornl = within(nav()).getByRole("group", { name: "ORNL" });
    const olcf = within(ornl).getByRole("group", { name: "OLCF" });
    expect(within(olcf).getAllByRole("button").map((b) => b.textContent)).toEqual([
      expect.stringContaining("Odo"),
      expect.stringContaining("Frontier"),
      expect.stringContaining("Lux"),
    ]);
    const nersc = within(within(nav()).getByRole("group", { name: "LBNL" })).getByRole("group", { name: "NERSC" });
    expect(within(nersc).getByRole("button", { name: /^Perlmutter/ })).toBeInTheDocument();

    expect(entry("Agent")).toHaveAttribute("aria-current", "page");
    for (const cluster of ["Odo", "Frontier", "Lux", "Perlmutter"]) {
      expect(screen.queryByRole("region", { name: cluster })).toBeNull();
    }
  });

  it("shows each resource's status in the tree, as the rail does", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");
    expect(entry("Frontier")).toHaveTextContent("Ready");
    expect(entry("Frontier")).toHaveAccessibleName("Frontier, Ready");
    expect(entry("Perlmutter")).toHaveTextContent("Not connected");
  });

  it("does not show the signed-in account", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ is_admin: true }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");
    expect(screen.queryByText(/Signed in as/)).toBeNull();
    expect(screen.queryByText("researcher@ornl.gov")).toBeNull();
  });

  it("opened for one cluster, shows that cluster's section only", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="frontier" />);
    const frontier = await region("Frontier");
    expect(entry("Frontier")).toHaveAttribute("aria-current", "page");
    expect(entry("Agent")).not.toHaveAttribute("aria-current");
    expect(frontier).toHaveTextContent("ORNL › OLCF");
    expect(screen.queryByRole("region", { name: "Agent" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Odo" })).toBeNull();
  });

  it("marks a hidden resource and still lists it under its facility", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ hpc_hidden_clusters: ["perlmutter"] }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");
    const nersc = within(nav()).getByRole("group", { name: "NERSC" });
    const perlmutter = within(nersc).getByRole("button", { name: /^Perlmutter/ });
    expect(perlmutter).toHaveTextContent("Hidden from sidebar");
    expect(perlmutter).toHaveAccessibleName(/hidden from sidebar/);
    expect(entry("Frontier")).not.toHaveTextContent("Hidden");
  });

  it("saves a field left for another section, and still shows it on return", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);
    await userEvent.type(await screen.findByLabelText(/Odo remote directory/), "/odo/vista");
    await openOn("Frontier");
    // Leaving the field saved it, without waiting for the pause.
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ odo_remote_dir: "/odo/vista" });
    await openOn("Odo");
    expect(screen.getByLabelText(/Odo remote directory/)).toHaveValue("/odo/vista");
  });
});

describe("UserSettingsModal cluster sections", () => {
  it("keeps each field in its own cluster's section", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({
        odo_s3m_token: "odo-tok",
        frontier_s3m_token: "fr-tok",
        nersc_account: "m1234",
        odo_remote_dir: "/odo/proj/vista",
        frontier_remote_dir: "/frontier/proj/vista",
      }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");

    const odo = await openOn("Odo");
    expect(within(odo).getByLabelText(/Odo S3M token/)).toHaveValue("odo-tok");
    expect(within(odo).getByText("Odo", { selector: ".user-settings-globus-cluster" })).toBeInTheDocument();
    expect(within(odo).queryByLabelText(/Frontier S3M token/)).toBeNull();
    expect(within(odo).getByLabelText(/Odo remote directory/)).toHaveValue("/odo/proj/vista");
    const hint = within(odo).getByText(/writable by the project's group/);
    expect(hint).toHaveTextContent("<dir>.<user>.jobs");
    expect(hint).toHaveTextContent("<dir>.out");
    expect(within(odo).getByRole("switch", { name: "Show Odo in sidebar" })).toBeChecked();

    const frontier = await openOn("Frontier");
    expect(within(frontier).getByLabelText(/Frontier S3M token/)).toHaveValue("fr-tok");
    expect(within(frontier).getByLabelText(/Frontier remote directory/)).toHaveValue("/frontier/proj/vista");
    expect(within(frontier).queryByLabelText(/Odo remote directory/)).toBeNull();
    expect(within(frontier).getByText("Frontier", { selector: ".user-settings-globus-cluster" })).toBeInTheDocument();
    expect(frontier).toHaveTextContent("Ready");

    const perlmutter = await openOn("Perlmutter");
    expect(perlmutter).toHaveTextContent("LBNL › NERSC");
    expect(within(perlmutter).getByLabelText(/NERSC account/)).toHaveValue("m1234");
    expect(within(perlmutter).getByLabelText(/NERSC IRI token/)).toBeInTheDocument();
    // No Globus for Perlmutter, and no expiry claims for its token.
    expect(within(perlmutter).queryByText(/Globus/, { selector: ".user-settings-globus-cluster" })).toBeNull();
    expect(perlmutter).not.toHaveTextContent(/expire/i);
    expect(within(perlmutter).getByRole("switch", { name: "Show Perlmutter in sidebar" })).toBeChecked();
  });
});

describe("UserSettingsModal autosave", () => {
  it("has no Save or Cancel, only Close", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("saves a typed remote directory after a pause, once, trimmed, and rechecks nothing", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="frontier" />);

    await userEvent.type(
      await screen.findByLabelText(/Frontier remote directory/),
      " /lustre/orion/abc123/proj-shared/vista ",
    );
    expect(updateCurrentUserMock).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(updateCurrentUserMock).toHaveBeenCalledWith({
        frontier_remote_dir: "/lustre/orion/abc123/proj-shared/vista",
      }),
    );
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
    // The cards check credentials and the facility, not the folder.
    expect(recheckMock).not.toHaveBeenCalled();
  });

  it("saves a pasted token once, and rechecks only that cluster", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ frontier_s3m_token: "fr-tok" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);

    await userEvent.click(await screen.findByLabelText(/Odo S3M token/));
    await userEvent.paste("new-odo");
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ odo_s3m_token: "new-odo" }));
    await userEvent.tab(); // leaving it changes nothing more
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).toHaveBeenCalledWith("odo");
    expect(refreshAgentSettingsMock).not.toHaveBeenCalled();
  });

  it("saves nothing of a token typed by hand until the field loses focus", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);

    await userEvent.type(await screen.findByLabelText(/Odo S3M token/), "abc");
    await new Promise((r) => setTimeout(r, 1000)); // past the text pause
    expect(updateCurrentUserMock).not.toHaveBeenCalled();
    await userEvent.tab();
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ odo_s3m_token: "abc" }));
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
  });

  it("saves a cleared token as not set", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ odo_s3m_token: "odo-tok", frontier_s3m_token: "fr-tok" }),
    );
    render(<UserSettingsModal onClose={() => {}} initialCluster="frontier" />);

    await userEvent.clear(await screen.findByLabelText(/Frontier S3M token/));
    await userEvent.tab();
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ frontier_s3m_token: null }));
    expect(recheckMock).toHaveBeenCalledWith("frontier");
  });

  it("still saves a change made straight before closing", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    const onClose = vi.fn();
    render(<UserSettingsModal onClose={onClose} initialCluster="frontier" />);

    await userEvent.type(await screen.findByLabelText(/Frontier remote directory/), "/frontier/vista");
    expect(updateCurrentUserMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ frontier_remote_dir: "/frontier/vista" });
  });

  it("hiding a cluster saves the list at once, leaves its token alone, and refreshes the rail", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ nersc_iri_token: "iri-tok" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="perlmutter" />);

    const toggle = await screen.findByRole("switch", { name: "Show Perlmutter in sidebar" });
    await userEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(entry("Perlmutter")).toHaveTextContent("Hidden from sidebar");

    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ hpc_hidden_clusters: ["perlmutter"] }));
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).not.toHaveBeenCalled();
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("showing a hidden cluster again sends an empty list", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ hpc_hidden_clusters: ["odo"] }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Odo in sidebar" }));
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ hpc_hidden_clusters: [] }));
  });

  it("an unchanged field triggers no save and no recheck", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ odo_remote_dir: "/odo" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);
    await userEvent.click(await screen.findByLabelText(/Odo remote directory/));
    await userEvent.tab();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(updateCurrentUserMock).not.toHaveBeenCalled();
    expect(recheckMock).not.toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
  });
});

describe("UserSettingsModal save marks", () => {
  /** The save mark drawn on a field, read from the field's own label. */
  const markOf = (el: HTMLElement) =>
    el.closest("label, .user-settings-switch-row")?.querySelector(".settings-mark")?.getAttribute("data-mark") ??
    null;
  const announced = () => document.querySelector(".visually-hidden[role=status]")?.textContent ?? "";

  it("has no save indicator by the title", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Odo in sidebar" }));
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalled());
    expect(screen.queryByText("Saving…")).toBeNull();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("checks the saved field only, shows nothing while it saves, and announces it", async () => {
    let release: (value: UserPublicWithConfig) => void = () => {};
    updateCurrentUserMock.mockImplementation(() => new Promise((resolve) => (release = resolve)));
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);

    const dir = await screen.findByLabelText(/Odo remote directory/);
    const token = screen.getByLabelText(/Odo S3M token/);
    await userEvent.type(dir, "/odo/vista");
    await userEvent.tab();
    expect(markOf(dir)).toBeNull(); // in flight: nothing to see
    await act(async () => release(user()));
    await waitFor(() => expect(markOf(dir)).toBe("saved"));
    expect(markOf(token)).toBeNull();
    expect(announced()).toBe("Odo remote directory saved.");
  });

  it("marks a switch beside it", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);
    const toggle = await screen.findByRole("switch", { name: "Show Odo in sidebar" });
    await userEvent.click(toggle);
    await waitFor(() => expect(markOf(toggle)).toBe("saved"));
    expect(announced()).toBe("Show Odo in sidebar saved.");
  });

  it("shows a slow save as still saving", async () => {
    let release: (value: UserPublicWithConfig) => void = () => {};
    updateCurrentUserMock.mockImplementation(() => new Promise((resolve) => (release = resolve)));
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);

    const account = await screen.findByLabelText(/Lux account/);
    await userEvent.type(account, "abc123");
    await userEvent.tab();
    await waitFor(() => expect(markOf(account)).toBe("slow"), { timeout: 2000 });
    await act(async () => release(user()));
    await waitFor(() => expect(markOf(account)).toBe("saved"));
  });

  it("marks a failure with a cross and the reason, keeping what was typed", async () => {
    updateCurrentUserMock.mockRejectedValueOnce(new Error("The remote directory must be absolute."));
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="perlmutter" />);

    const dir = await screen.findByLabelText(/NERSC remote directory/);
    await userEvent.type(dir, "relative/dir");
    await userEvent.tab();
    await waitFor(() => expect(markOf(dir)).toBe("failed"));
    expect(screen.getByText("The remote directory must be absolute.")).toBeInTheDocument();
    expect(dir).toHaveValue("relative/dir");
    expect(dir).toHaveAttribute("aria-invalid", "true");
    expect(announced()).toBe("Couldn't save NERSC remote directory: The remote directory must be absolute.");
  });

  it("marks the section in the navigation when its save fails after leaving it, and focuses the field on return", async () => {
    let reject: (e: Error) => void = () => {};
    updateCurrentUserMock.mockImplementationOnce(() => new Promise((_, r) => (reject = r)));
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="perlmutter" />);

    await userEvent.type(await screen.findByLabelText(/NERSC remote directory/), "relative/dir");
    await openOn("Agent"); // leaving the field sends it
    await act(async () => reject(new Error("The remote directory must be absolute.")));

    await waitFor(() => expect(entry("Perlmutter")).toHaveAccessibleName(/couldn't save$/));
    expect(entry("Agent")).not.toHaveAccessibleName(/couldn't save/);
    await userEvent.click(entry("Perlmutter"));
    const again = within(await region("Perlmutter")).getByLabelText(/NERSC remote directory/);
    expect(again).toHaveValue("relative/dir");
    await waitFor(() => expect(again).toHaveFocus());
  });

  it("clears the cross, the reason and the navigation mark once the field saves", async () => {
    updateCurrentUserMock.mockRejectedValueOnce(new Error("The remote directory must be absolute."));
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="perlmutter" />);

    const dir = await screen.findByLabelText(/NERSC remote directory/);
    await userEvent.type(dir, "relative");
    await userEvent.tab();
    await waitFor(() => expect(markOf(dir)).toBe("failed"));

    await userEvent.clear(dir);
    await userEvent.type(dir, "/pscratch/vista");
    await userEvent.tab();
    await waitFor(() => expect(markOf(dir)).toBe("saved"));
    expect(screen.queryByText("The remote directory must be absolute.")).toBeNull();
    expect(entry("Perlmutter")).not.toHaveAccessibleName(/couldn't save/);
    expect(updateCurrentUserMock).toHaveBeenLastCalledWith({ nersc_remote_dir: "/pscratch/vista" });
  });
});

describe("UserSettingsModal: Agent", () => {
  it("offers the backend's providers in order, with i2's key and no Model or endpoint field", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ inference_api_key: "i2-key" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await region("Agent");

    const select = within(agent).getByLabelText(/^Inference provider/);
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "AmSC i2",
      "AmSC MAG",
      "OLCF Inference",
      "Custom",
    ]);
    expect(select).toHaveValue("i2");
    expect(within(agent).getByLabelText(/^AmSC i2 API key/)).toHaveValue("i2-key");
    expect(agent).toHaveTextContent("Uses claude-sonnet");
    expect(within(agent).queryByLabelText(/^Model/)).toBeNull();
    expect(within(agent).queryByLabelText(/^Inference endpoint/)).toBeNull();
  });

  it("switching provider shows that provider's own key field", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ inference_api_key: "i2-key", inference_mag_api_key: "mag-token", inference_olcf_api_key: "s3m" }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await region("Agent");
    const select = within(agent).getByLabelText(/^Inference provider/);

    await userEvent.selectOptions(select, "mag");
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toHaveValue("mag-token");
    expect(within(agent).queryByLabelText(/^AmSC i2 API key/)).toBeNull();
    expect(within(agent).queryByLabelText(/^Inference endpoint/)).toBeNull();
    expect(agent).toHaveTextContent("no default model");

    await userEvent.selectOptions(select, "olcf");
    const olcf = within(agent).getByLabelText(/^OLCF Inference S3M token/);
    expect(olcf).toHaveValue("s3m");
    expect(agent).toHaveTextContent("separate from Odo's and Frontier's");
    expect(within(agent).queryByLabelText(/^Inference endpoint/)).toBeNull();

    await userEvent.selectOptions(select, "i2");
    expect(within(agent).getByLabelText(/^AmSC i2 API key/)).toHaveValue("i2-key");
  });

  it("only Custom shows an endpoint field; the choice saves at once, then the endpoint and key", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await region("Agent");

    await userEvent.selectOptions(within(agent).getByLabelText(/^Inference provider/), "custom");
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ inference_provider: "custom" }));
    await userEvent.type(within(agent).getByLabelText(/^Inference endpoint/), "https://gw.example/v1");
    await userEvent.type(within(agent).getByLabelText(/^Custom endpoint API key/), "c-key");
    await userEvent.tab();

    await waitFor(() =>
      expect(updateCurrentUserMock).toHaveBeenCalledWith({ inference_custom_api_key: "c-key" }),
    );
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ inference_base_url: "https://gw.example/v1" });
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(3);
    // Each of the three refreshes the picker's view of the provider.
    await waitFor(() => expect(refreshAgentSettingsMock).toHaveBeenCalledTimes(3));
    expect(recheckMock).not.toHaveBeenCalled();
  });

  it("shows a provider from the installation's configuration as such, until one is chosen", async () => {
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({ provider: "custom", source: "config", baseUrl: "https://dev.example/v1" }),
      error: null,
      modelChoiceRequest: 0,
    });
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ inference_api_key: "row-key" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await region("Agent");

    const select = within(agent).getByLabelText(/^Inference provider/);
    expect(within(select).getByRole("option", { selected: true })).toHaveTextContent("Custom, from configuration");
    expect(agent).toHaveTextContent("https://dev.example/v1");
    // The configured endpoint uses the saved key.
    expect(within(agent).getByLabelText(/^API key/)).toHaveValue("row-key");

    await userEvent.selectOptions(select, "mag");
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toBeInTheDocument();
  });

  it("names a missing key in the navigation", async () => {
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({ hasCredential: false, keysSet: { i2: false, mag: false, olcf: false, custom: false } }),
      error: null,
      modelChoiceRequest: 0,
    });
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Agent");
    expect(entry("Agent")).toHaveTextContent("No key");
  });
});

describe("UserSettingsModal: Lux", () => {
  it("opened from the Lux card, shows the sidebar switch, the account and the remote directory", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ lux_remote_dir: "/lux/vista", lux_account: "abc123" }),
    );
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    const lux = await region("Lux");
    expect(entry("Lux")).toHaveAttribute("aria-current", "page");
    expect(lux).toHaveTextContent("Ready");
    expect(within(lux).getByRole("switch", { name: "Show Lux in sidebar" })).toBeChecked();
    // The account and the folder are its only fields: no credential is stored for Lux.
    const fields = lux.querySelectorAll("input:not([role=switch]), textarea, a");
    expect(fields).toHaveLength(2);
    expect(within(lux).getByLabelText(/Lux account/)).toHaveValue("abc123");
    expect(within(lux).getByLabelText(/Lux remote directory/)).toHaveValue("/lux/vista");
    expect(lux).not.toHaveTextContent(/credentials/i); // it has none to keep
  });

  it("saving the Lux account sends only that field", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    await userEvent.type(await screen.findByLabelText(/Lux account/), "abc123");
    await userEvent.tab();
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ lux_account: "abc123" }));
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
  });

  it("clearing the Lux remote directory sends null", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ lux_remote_dir: "/lux/vista" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    await userEvent.clear(await screen.findByLabelText(/Lux remote directory/));
    await userEvent.tab();
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ lux_remote_dir: null }));
  });

  it("hiding Lux saves the list and refreshes the rail without a recheck", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Lux in sidebar" }));
    await waitFor(() => expect(updateCurrentUserMock).toHaveBeenCalledWith({ hpc_hidden_clusters: ["lux"] }));
    expect(recheckMock).not.toHaveBeenCalled();
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });
});

describe("UserSettingsModal appearance", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => setThemeChoice("system"));

  function radio(name: string) {
    return within(screen.getByRole("radiogroup", { name: "Appearance" })).getByRole("radio", { name });
  }

  async function openAppearance() {
    await userEvent.click(entry("Appearance"));
  }

  it("starts on System, and says what System means", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await openAppearance();
    expect(radio("System")).toBeChecked();
    expect(radio("Light")).not.toBeChecked();
    expect(radio("Dark")).not.toBeChecked();
    expect(screen.getByText(/System matches your computer's light or dark setting/)).toBeInTheDocument();
  });

  it.each([
    ["Dark", "dark"],
    ["Light", "light"],
  ])("selecting %s applies and stores it at once, without Save", async (name, value) => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await openAppearance();
    await userEvent.click(radio(name));
    expect(radio(name)).toBeChecked();
    expect(document.documentElement.getAttribute("data-theme")).toBe(value);
    expect(window.localStorage.getItem("vista.theme")).toBe(value);
    expect(updateCurrentUserMock).not.toHaveBeenCalled();
  });

  it("selecting System clears the override", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    setThemeChoice("dark");
    render(<UserSettingsModal onClose={() => {}} />);
    await openAppearance();
    expect(radio("Dark")).toBeChecked();
    await userEvent.click(radio("System"));
    expect(radio("System")).toBeChecked();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem("vista.theme")).toBeNull();
  });

  it("arrow keys move and select together, with one tab stop", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await openAppearance();
    expect(radio("System")).toHaveAttribute("tabindex", "0");
    expect(radio("Dark")).toHaveAttribute("tabindex", "-1");

    radio("System").focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(radio("Light")).toBeChecked();
    expect(radio("Light")).toHaveFocus();

    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(radio("Dark")).toBeChecked();
    expect(radio("Dark")).toHaveFocus();
  });

  it("is available before the user record loads", async () => {
    fetchCurrentUserWithConfigMock.mockReturnValue(new Promise(() => {}));
    render(<UserSettingsModal onClose={() => {}} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await openAppearance();
    expect(radio("System")).toBeChecked();
  });
});
