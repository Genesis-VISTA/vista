import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserSettingsModal } from "@/components/UserSettingsModal";
import { setThemeChoice } from "@/lib/theme";
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
  const luxEntry = entry("lux", "ready");
  return {
    clusters: [
      entry("frontier", "ready"),
      entry("odo", "ready"),
      entry("perlmutter", "not_connected"),
      luxEntry,
    ],
    lastSuccessAt: 0,
    failing: false,
    unavailable: false,
    now: 0,
    recheck: recheckMock,
  };
}

beforeEach(() => {
  fetchCurrentUserWithConfigMock.mockReset();
  updateCurrentUserMock.mockReset();
  updateCurrentUserMock.mockResolvedValue(user());
  recheckMock.mockClear();
  refreshMock.mockClear();
  useHpcStatusMock.mockReturnValue(statusView());
});

async function section(name: string) {
  return screen.findByRole("region", { name });
}

function header(name: string) {
  return screen.getByRole("button", { name: new RegExp(`^${name}\\b`) });
}

describe("UserSettingsModal cluster sections", () => {
  it("starts with every cluster collapsed, each showing its status", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await section("Frontier");
    for (const name of ["Frontier", "Odo", "Perlmutter"]) {
      expect(header(name)).toHaveAttribute("aria-expanded", "false");
    }
    expect(header("Frontier")).toHaveTextContent("Ready");
    expect(header("Perlmutter")).toHaveTextContent("Not connected");
    expect(screen.queryByLabelText(/S3M token/)).toBeNull();
  });

  it("opened for one cluster, expands only that one", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="frontier" />);
    await section("Frontier");
    expect(header("Frontier")).toHaveAttribute("aria-expanded", "true");
    expect(header("Odo")).toHaveAttribute("aria-expanded", "false");
    expect(header("Perlmutter")).toHaveAttribute("aria-expanded", "false");
  });

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
    await section("Odo");
    for (const name of ["Frontier", "Odo", "Perlmutter"]) await userEvent.click(header(name));

    const odo = await section("Odo");
    expect(within(odo).getByLabelText(/Odo S3M token/)).toHaveValue("odo-tok");
    expect(within(odo).getByText("Odo", { selector: ".user-settings-globus-cluster" })).toBeInTheDocument();
    expect(within(odo).queryByLabelText(/Frontier S3M token/)).toBeNull();
    expect(within(odo).getByLabelText(/Odo remote directory/)).toHaveValue("/odo/proj/vista");
    const hint = within(odo).getByText(/writable by the project's group/);
    expect(hint).toHaveTextContent("<dir>.<user>.jobs");
    expect(hint).toHaveTextContent("<dir>.out");

    const frontier = await section("Frontier");
    expect(within(frontier).getByLabelText(/Frontier S3M token/)).toHaveValue("fr-tok");
    expect(within(frontier).getByLabelText(/Frontier remote directory/)).toHaveValue(
      "/frontier/proj/vista",
    );
    expect(within(frontier).queryByLabelText(/Odo remote directory/)).toBeNull();

    const perlmutter = await section("Perlmutter");
    expect(within(perlmutter).getByLabelText(/NERSC account/)).toHaveValue("m1234");
    expect(within(perlmutter).getByLabelText(/NERSC IRI token/)).toBeInTheDocument();
    // No Globus for Perlmutter, and no expiry claims for its token.
    expect(within(perlmutter).queryByText(/Globus/, { selector: ".user-settings-globus-cluster" })).toBeNull();
    expect(perlmutter).not.toHaveTextContent(/expire/i);

    for (const name of ["Frontier", "Odo", "Perlmutter"]) {
      expect(within(await section(name)).getByRole("switch", { name: `Show ${name} in sidebar` })).toBeChecked();
    }
  });
});

describe("UserSettingsModal saving", () => {
  it("saving one cluster's token sends only that field and rechecks only that cluster", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ frontier_s3m_token: "fr-tok" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);

    await userEvent.type(await screen.findByLabelText(/Odo S3M token/), "new-odo");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ odo_s3m_token: "new-odo" });
    expect(recheckMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).toHaveBeenCalledWith("odo");
  });

  it("clearing one token sends null for it alone", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ odo_s3m_token: "odo-tok", frontier_s3m_token: "fr-tok" }),
    );
    render(<UserSettingsModal onClose={() => {}} initialCluster="frontier" />);

    await userEvent.clear(await screen.findByLabelText(/Frontier S3M token/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateCurrentUserMock).toHaveBeenCalledWith({ frontier_s3m_token: null });
    expect(recheckMock).toHaveBeenCalledWith("frontier");
  });

  it("saving a remote directory sends only that field, trimmed, and rechecks nothing", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="frontier" />);

    await userEvent.type(
      await screen.findByLabelText(/Frontier remote directory/),
      " /lustre/orion/abc123/proj-shared/vista ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateCurrentUserMock).toHaveBeenCalledWith({
      frontier_remote_dir: "/lustre/orion/abc123/proj-shared/vista",
    });
    // The cards check credentials and the facility, not the folder.
    expect(recheckMock).not.toHaveBeenCalled();
  });

  it("hiding a cluster saves the list, leaves its token alone, and refreshes the rail", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ nersc_iri_token: "iri-tok" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="perlmutter" />);

    const toggle = await screen.findByRole("switch", { name: "Show Perlmutter in sidebar" });
    await userEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(header("Perlmutter")).toHaveTextContent("Hidden from sidebar");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateCurrentUserMock).toHaveBeenCalledWith({ hpc_hidden_clusters: ["perlmutter"] });
    expect(recheckMock).not.toHaveBeenCalled();
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("showing a hidden cluster again sends an empty list", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ hpc_hidden_clusters: ["odo"] }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="odo" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Odo in sidebar" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ hpc_hidden_clusters: [] });
  });

  it("changing nothing about clusters triggers no recheck", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
    await userEvent.type(await screen.findByLabelText(/^Model/), "claude-opus");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).not.toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
  });
});

describe("UserSettingsModal: Lux", () => {
  it("opened from the Lux card, shows the sidebar switch, the account and the remote directory", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ lux_remote_dir: "/lux/vista", lux_account: "abc123" }),
    );
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    const lux = await section("Lux");
    expect(header("Lux")).toHaveAttribute("aria-expanded", "true");
    expect(header("Odo")).toHaveAttribute("aria-expanded", "false");
    expect(header("Lux")).toHaveTextContent("Ready");
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
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ lux_account: "abc123" });
  });

  it("clearing the Lux remote directory sends null", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user({ lux_remote_dir: "/lux/vista" }));
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    await userEvent.clear(await screen.findByLabelText(/Lux remote directory/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ lux_remote_dir: null });
  });

  it("hiding Lux saves the list and refreshes the rail without a recheck", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} initialCluster="lux" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Lux in sidebar" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ hpc_hidden_clusters: ["lux"] });
    expect(recheckMock).not.toHaveBeenCalled();
    expect(refreshMock).toHaveBeenCalledTimes(1);
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

  it("starts on System, and says what System means", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
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
    expect(radio("Dark")).toBeChecked();
    await userEvent.click(radio("System"));
    expect(radio("System")).toBeChecked();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem("vista.theme")).toBeNull();
  });

  it("arrow keys move and select together, with one tab stop", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(user());
    render(<UserSettingsModal onClose={() => {}} />);
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

  it("is available before the user record loads", () => {
    fetchCurrentUserWithConfigMock.mockReturnValue(new Promise(() => {}));
    render(<UserSettingsModal onClose={() => {}} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(radio("System")).toBeChecked();
  });
});
