import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserSettingsModal } from "@/components/UserSettingsModal";
import { setThemeChoice } from "@/lib/theme";
import type { AgentSettings } from "@/lib/agent-settings";
import type { CollaborationStatus, CollaborationView } from "@/lib/collaboration";
import type { HpcCluster, HpcClusterStatus, HpcStatusView } from "@/lib/hpc-status";
import type { LocalSettings } from "@/lib/settings";
import { DEBOUNCE_MS } from "@/lib/settings-autosave";

const { fetchSettingsMock, updateSettingsMock, startGlobusLoginMock } = vi.hoisted(() => ({
  fetchSettingsMock: vi.fn(),
  updateSettingsMock: vi.fn(),
  startGlobusLoginMock: vi.fn(),
}));
vi.mock("@/lib/settings", () => ({
  fetchSettings: fetchSettingsMock,
  updateSettings: updateSettingsMock,
  startGlobusLogin: startGlobusLoginMock,
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

const { useCollaborationMock, refreshCollaborationMock } = vi.hoisted(() => ({
  useCollaborationMock: vi.fn(),
  refreshCollaborationMock: vi.fn(async () => {}),
}));
vi.mock("@/lib/collaboration", () => ({
  useCollaboration: useCollaborationMock,
  refreshCollaborationStatus: refreshCollaborationMock,
}));

const NOT_SET =
  "No collaboration server is set. Add its address under Settings → Collaboration to publish and import.";

function collaborationView(
  status: Partial<CollaborationStatus> | null = {},
  error: string | null = null,
): CollaborationView {
  const full: CollaborationStatus | null =
    status === null
      ? null
      : {
          configured: false,
          reachable: false,
          reason: NOT_SET,
          url: null,
          source: null,
          server_version: null,
          signed_in: Boolean(status.reachable),
          sign_in_required: false,
          // As the backend does: none for a server that signed in without a token.
          sign_in_url: status.url && !status.reachable ? `${status.url}/sign-in` : null,
          ...status,
        };
  const available = Boolean(full?.reachable && full.signed_in);
  return {
    status: full,
    checking: false,
    available,
    reason: available ? null : (full?.reason ?? "Checking the collaboration server…"),
    error,
    refresh: refreshCollaborationMock,
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
  updateSettingsMock.mockReset();
  updateSettingsMock.mockResolvedValue(settingsRow());
  recheckMock.mockClear();
  refreshMock.mockClear();
  refreshAgentSettingsMock.mockClear();
  refreshCollaborationMock.mockClear();
  useCollaborationMock.mockReturnValue(collaborationView());
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
  if (["Odo", "Frontier", "Lux", "Perlmutter"].includes(name)) {
    if (!screen.queryByRole("region", { name: "Compute" })) await userEvent.click(entry("Compute"));
    const compute = await region("Compute");
    await userEvent.click(within(compute).getByRole("button", { name: new RegExp(`^${name}\\b`) }));
  } else {
    await userEvent.click(entry(name));
  }
  return region(name === "Models and providers" ? "Models & providers" : name);
}

describe("UserSettingsModal navigation", () => {
  it("lists six focused sections and opens on Research profile", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");

    const names = within(nav())
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label")?.split(",")[0]);
    expect(names).toEqual([
      "Appearance",
      "Research profile",
      "Models and providers",
      "Repositories",
      "Collaboration",
      "Compute",
    ]);

    expect(entry("Research profile")).toHaveAttribute("aria-current", "page");
    for (const cluster of ["Odo", "Frontier", "Lux", "Perlmutter"]) {
      expect(screen.queryByRole("region", { name: cluster })).toBeNull();
    }
  });

  it("shows each resource's status in the Compute section", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const compute = await openOn("Compute");
    expect(within(compute).getByRole("button", { name: /^Frontier/ })).toHaveTextContent("Ready");
    expect(within(compute).getByRole("button", { name: /^Frontier/ })).toHaveAccessibleName(
      "Frontier, ORNL · OLCF, Ready",
    );
    expect(within(compute).getByRole("button", { name: /^Perlmutter/ })).toHaveTextContent("Not connected");
  });

  it("does not show an account", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(screen.queryByText(/Signed in as/)).toBeNull();
    expect(screen.queryByText("researcher@ornl.gov")).toBeNull();
  });

  it("opened for one cluster, shows that cluster's section only", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="frontier" />);
    const frontier = await region("Frontier");
    expect(entry("Compute")).toHaveAttribute("aria-current", "page");
    expect(entry("Models and providers")).not.toHaveAttribute("aria-current");
    expect(frontier).toHaveTextContent("ORNL › OLCF");
    expect(screen.queryByRole("region", { name: "Models & providers" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Odo" })).toBeNull();
  });

  it("opened at the model provider, shows it and marks it current", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="agent" />);
    await region("Models & providers");
    expect(screen.getByText("Model provider")).toBeInTheDocument();
    expect(entry("Models and providers")).toHaveAttribute("aria-current", "page");
    expect(entry("Research profile")).not.toHaveAttribute("aria-current");
  });

  it("opened at a section, starts on the section pane, which is what a narrow window shows", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    const { container } = render(<UserSettingsModal onClose={() => {}} initialSection="agent" />);
    await region("Models & providers");
    expect(container.querySelector(".settings-layout")).toHaveAttribute("data-pane", "section");
  });

  it("opened plainly, starts on the list pane", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    const { container } = render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(container.querySelector(".settings-layout")).toHaveAttribute("data-pane", "nav");
  });

  it("marks a hidden resource and still lists it under its facility", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ hpc_hidden_clusters: ["perlmutter"] }));
    render(<UserSettingsModal onClose={() => {}} />);
    const compute = await openOn("Compute");
    const perlmutter = within(compute).getByRole("button", { name: /^Perlmutter/ });
    expect(perlmutter).toHaveTextContent("Hidden");
    expect(perlmutter).toHaveAccessibleName("Perlmutter, LBNL · NERSC, Hidden");
    expect(within(compute).getByRole("button", { name: /^Frontier/ })).not.toHaveTextContent("Hidden");
  });

  it("saves a field left for another section, and still shows it on return", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);
    await userEvent.type(await screen.findByLabelText(/Odo remote directory/), "/odo/vista");
    await openOn("Frontier");
    // Leaving the field saved it, without waiting for the pause.
    expect(updateSettingsMock).toHaveBeenCalledWith({ odo_remote_dir: "/odo/vista" });
    await openOn("Odo");
    expect(screen.getByLabelText(/Odo remote directory/)).toHaveValue("/odo/vista");
  });
});

describe("UserSettingsModal collaboration", () => {
  const OBSIDIAN = "https://obsidian.ccs.ornl.gov/vista/api";
  const GET_ONE = `Get a token from ${OBSIDIAN}/sign-in and paste it under Settings → Collaboration.`;

  it("asks for a token when the server wants one, and saves the pasted one", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({
        configured: true,
        reachable: false,
        reason: GET_ONE,
        url: OBSIDIAN,
        source: "settings",
        sign_in_required: true,
      }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: OBSIDIAN }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Collaboration")).toHaveAccessibleName("Collaboration, sign in");
    const section = await openOn("Collaboration");
    expect(section).toHaveTextContent(GET_ONE);
    expect(within(section).getByRole("link", { name: "Open the server's sign-in page" })).toHaveAttribute(
      "href",
      `${OBSIDIAN}/sign-in`,
    );
    await userEvent.type(within(section).getByLabelText("Token"), "eyJ.abc.def");
    await userEvent.tab();
    expect(updateSettingsMock).toHaveBeenCalledWith({ collaboration_token: "eyJ.abc.def" });
    // The same server: its last answer stands while the token is checked, and
    // keeps the field where it is.
    await waitFor(() => expect(refreshCollaborationMock).toHaveBeenCalledWith());
    expect(refreshCollaborationMock).not.toHaveBeenCalledWith({ forget: true });
    expect(within(section).getByLabelText("Token")).toHaveValue("");
  });

  it("shows a saved token, and removes it", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({ configured: true, reachable: true, reason: null, url: OBSIDIAN, source: "settings" }),
    );
    fetchSettingsMock.mockResolvedValue(
      settingsRow({ collaboration_server_url: OBSIDIAN, has_collaboration_token: true }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    expect(within(section).getByLabelText("Token")).toHaveAttribute("placeholder", "Saved. Paste a new one to replace it");
    await userEvent.click(within(section).getByRole("button", { name: "Remove" }));
    expect(updateSettingsMock).toHaveBeenCalledWith({ collaboration_token: "" });
  });

  it("asks for a token when the gateway answers with a redirect", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({
        configured: true,
        reachable: false,
        reason: `The collaboration server at ${OBSIDIAN} redirects to https://login.example.org.`,
        url: OBSIDIAN,
        source: "settings",
      }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: OBSIDIAN }));
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    expect(within(section).getByLabelText("Token")).toBeInTheDocument();
  });

  it("asks for no token over plain http to another machine", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({
        configured: true,
        reachable: false,
        reason: "VISTA sends its token only over https.",
        url: "http://collab.example.org",
        source: "settings",
        sign_in_required: true,
        sign_in_url: null,
      }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: "http://collab.example.org" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    expect(within(section).queryByLabelText("Token")).toBeNull();
  });

  it("asks for no token from a server that needs none", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({ configured: true, reachable: true, reason: null, url: "http://127.0.0.1:8002", source: "settings" }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: "http://127.0.0.1:8002" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    expect(within(section).queryByLabelText("Token")).toBeNull();
  });

  it("says no server is set, and why Publish and Import are off", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Collaboration")).toHaveAccessibleName("Collaboration, not set");

    const section = await openOn("Collaboration");
    expect(within(section).getByLabelText(/^Collaboration server/)).toHaveValue("");
    expect(section).toHaveTextContent(NOT_SET);
  });

  it("saves the address and checks the server again", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    await userEvent.type(within(section).getByLabelText(/^Collaboration server/), "http://127.0.0.1:8002");
    // Not on a pause in typing: half an address would be saved and checked.
    await new Promise((resolve) => setTimeout(resolve, DEBOUNCE_MS + 200));
    expect(updateSettingsMock).not.toHaveBeenCalled();
    await userEvent.tab();
    expect(updateSettingsMock).toHaveBeenCalledWith({ collaboration_server_url: "http://127.0.0.1:8002" });
    // The answer about the old address is dropped, not shown as current.
    await waitFor(() => expect(refreshCollaborationMock).toHaveBeenCalledWith({ forget: true }));
  });

  it("says when a check could not be made, beside the last answer", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView(
        {
          configured: true,
          reachable: true,
          reason: null,
          url: "http://127.0.0.1:8002",
          source: "settings",
          server_version: "0.1.0",
        },
        "request failed (502)",
      ),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: "http://127.0.0.1:8002" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    expect(section).toHaveTextContent("Connected to http://127.0.0.1:8002 (server 0.1.0).");
    expect(section).toHaveTextContent("Couldn't check again: request failed (502); this is the last answer.");
  });

  it("shows a connected server", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({
        configured: true,
        reachable: true,
        reason: null,
        url: "http://127.0.0.1:8002",
        source: "settings",
        server_version: "0.1.0",
      }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: "http://127.0.0.1:8002" }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Collaboration")).toHaveAccessibleName("Collaboration, connected");
    const section = await openOn("Collaboration");
    expect(within(section).getByLabelText(/^Collaboration server/)).toHaveValue("http://127.0.0.1:8002");
    expect(section).toHaveTextContent("Connected to http://127.0.0.1:8002 (server 0.1.0).");
  });

  it("shows why a configured server is unreachable, and checks again on request", async () => {
    const reason = "The collaboration server at http://x:8002 is not answering (Connection refused).";
    useCollaborationMock.mockReturnValue(
      collaborationView({ configured: true, reason, url: "http://x:8002", source: "settings" }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow({ collaboration_server_url: "http://x:8002" }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Collaboration")).toHaveAccessibleName("Collaboration, unreachable");
    const section = await openOn("Collaboration");
    expect(section).toHaveTextContent(reason);
    await userEvent.click(within(section).getByRole("button", { name: "Check again" }));
    expect(refreshCollaborationMock).toHaveBeenCalled();
  });

  it("names the installation's own server when the researcher has set none", async () => {
    useCollaborationMock.mockReturnValue(
      collaborationView({
        configured: true,
        reachable: true,
        reason: null,
        url: "http://127.0.0.1:8002",
        source: "config",
      }),
    );
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Collaboration");
    const field = within(section).getByLabelText(/^Collaboration server/);
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("placeholder", "http://127.0.0.1:8002");
    expect(section).toHaveTextContent("This installation's configuration uses http://127.0.0.1:8002");
  });
});

describe("UserSettingsModal repositories", () => {
  it("says a token is saved without showing it, and where tokens are used", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ has_github_token: true }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Repositories")).toHaveAccessibleName("Repositories, 1 saved");

    const section = await openOn("Repositories");
    const github = within(section).getByLabelText("GitHub token");
    expect(github).toHaveValue("");
    expect(github).toHaveAttribute("placeholder", expect.stringMatching(/^Saved/));
    expect(within(section).getByLabelText("gitlab.com token")).toHaveAttribute("placeholder", "Not set");

    const note = within(section).getByText(/always use the token here/);
    expect(note.textContent).toMatch(/Hypothesis Lab forum on github\.com or gitlab\.com/);
    expect(note.textContent).toMatch(/any other host uses your system git.s credentials/);
    expect(note.textContent).not.toMatch(/project.s own token/);
  });

  it("saves a pasted token once, then shows it as saved and empties the field", async () => {
    fetchSettingsMock
      .mockResolvedValueOnce(settingsRow())
      .mockResolvedValueOnce(settingsRow({ has_github_token: true }));
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Repositories");

    await userEvent.click(within(section).getByLabelText("GitHub token"));
    await userEvent.paste("ghp_pasted");
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ github_token: "ghp_pasted" }));
    await waitFor(() => expect(within(section).getByLabelText("GitHub token")).toHaveValue(""));
    expect(within(section).getByLabelText("GitHub token")).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/^Saved/),
    );
    await userEvent.tab();
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
  });

  it("leaves an untouched token alone, and only Remove sends a blank", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ has_github_token: true }));
    render(<UserSettingsModal onClose={() => {}} />);
    const section = await openOn("Repositories");

    // Focusing and leaving the empty field is not a removal.
    await userEvent.click(within(section).getByLabelText("GitHub token"));
    await userEvent.tab();
    expect(updateSettingsMock).not.toHaveBeenCalled();

    await userEvent.click(within(section).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ github_token: "" }));
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
  });

  it("never sends a token with another setting's save", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ has_github_token: true, has_gitlab_token: true }));
    render(<UserSettingsModal onClose={() => {}} />);
    const profile = await region("Research profile");

    await userEvent.type(within(profile).getByLabelText(/^Role/), "chemist");
    await userEvent.tab();
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ research_role: "chemist" }));
    for (const [update] of updateSettingsMock.mock.calls) {
      expect(update).not.toHaveProperty("github_token");
      expect(update).not.toHaveProperty("gitlab_token");
    }
  });
});

describe("UserSettingsModal cluster sections", () => {
  it("keeps each field in its own cluster's section", async () => {
    fetchSettingsMock.mockResolvedValue(
      settingsRow({
        odo_s3m_token: "odo-tok",
        frontier_s3m_token: "fr-tok",
        nersc_account: "m1234",
        odo_remote_dir: "/odo/proj/vista",
        frontier_remote_dir: "/frontier/proj/vista",
      }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");

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

describe("UserSettingsModal research profile", () => {
  it("shows saved scientific context and preferences", async () => {
    fetchSettingsMock.mockResolvedValue(
      settingsRow({
        research_role: "Computational materials scientist",
        research_institution: "Oak Ridge National Laboratory",
        research_interests: ["Molten salts", "Thermophysical properties"],
        preferred_units: "source",
        technical_depth: "expert",
        evidence_preference: "always_cite",
      }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    const profile = await region("Research profile");

    expect(within(profile).getByLabelText(/^Role/)).toHaveValue("Computational materials scientist");
    expect(within(profile).getByLabelText(/^Institution or laboratory/)).toHaveValue(
      "Oak Ridge National Laboratory",
    );
    expect(within(profile).getByText("Molten salts")).toBeInTheDocument();
    expect(within(profile).getByText("Thermophysical properties")).toBeInTheDocument();
    expect(within(profile).getByLabelText(/^Units/)).toHaveValue("source");
    expect(within(profile).getByLabelText(/^Technical depth/)).toHaveValue("expert");
    expect(within(profile).getByLabelText(/^Evidence/)).toHaveValue("always_cite");
  });

  it("autosaves profile text, interests, and scientific preferences", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const profile = await region("Research profile");

    await userEvent.type(within(profile).getByLabelText(/^Role/), "Physical chemist");
    await userEvent.tab();
    await userEvent.type(within(profile).getByLabelText("Add research interest"), "Phase equilibria");
    await userEvent.click(within(profile).getByRole("button", { name: "Add" }));
    await userEvent.selectOptions(within(profile).getByLabelText(/^Technical depth/), "expert");

    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ research_role: "Physical chemist" }));
    expect(updateSettingsMock).toHaveBeenCalledWith({ research_interests: ["Phase equilibria"] });
    expect(updateSettingsMock).toHaveBeenCalledWith({ technical_depth: "expert" });
    expect(refreshAgentSettingsMock).not.toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("shows and autosaves the name and email", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ display_name: "Ada" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const profile = await region("Research profile");

    const name = within(profile).getByLabelText(/^Name/);
    expect(name).toHaveValue("Ada");
    await userEvent.clear(name);
    await userEvent.type(name, "Ada Lovelace");
    await userEvent.tab();
    const email = within(profile).getByLabelText(/^Email/);
    expect(email).toHaveAttribute("type", "email");
    await userEvent.clear(email);
    await userEvent.type(email, "ada@ornl.gov");
    await userEvent.tab();

    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ display_name: "Ada Lovelace" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ email: "ada@ornl.gov" }));
  });

  it("links to the documentation, in a new tab", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const profile = await region("Research profile");
    const link = within(profile).getByRole("link", { name: "Documentation" });
    expect(link).toHaveAttribute("href", "https://genesis-vista.github.io/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("Replay tutorial restarts it at the welcome step and closes Settings", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    updateSettingsMock.mockResolvedValue(settingsRow());
    const onClose = vi.fn();
    render(<UserSettingsModal onClose={onClose} />);
    const profile = await region("Research profile");

    await userEvent.click(within(profile).getByRole("button", { name: "Replay tutorial" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(updateSettingsMock).toHaveBeenCalledWith({
      onboarding_tour: { status: "in_progress", step: "welcome" },
    });
  });

  it("keeps Settings open and says why when Replay cannot be saved", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    updateSettingsMock.mockRejectedValue(new Error("backend down"));
    const onClose = vi.fn();
    render(<UserSettingsModal onClose={onClose} />);
    const profile = await region("Research profile");

    await userEvent.click(within(profile).getByRole("button", { name: "Replay tutorial" }));

    expect(await within(profile).findByText("backend down")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("can turn personalization off without deleting the profile", async () => {
    fetchSettingsMock.mockResolvedValue(
      settingsRow({ research_role: "Nuclear engineer", research_interests: ["Corrosion"] }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    const profile = await region("Research profile");
    const toggle = within(profile).getByRole("switch", { name: "Personalize responses with research profile" });

    await userEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(entry("Research profile")).toHaveTextContent("Off");
    expect(within(profile).getByLabelText(/^Role/)).toHaveValue("Nuclear engineer");
    expect(within(profile).getByText("Corrosion")).toBeInTheDocument();
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ personalize_responses: false }));
  });
});

describe("UserSettingsModal autosave", () => {
  it("has no Save or Cancel, only Close", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.getByRole("button", { name: "Close settings" })).toBeInTheDocument();
  });

  it("saves a typed remote directory after a pause, once, trimmed, and refreshes the cards", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="frontier" />);

    await userEvent.type(
      await screen.findByLabelText(/Frontier remote directory/),
      " /lustre/orion/abc123/proj-shared/vista ",
    );
    expect(updateSettingsMock).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(updateSettingsMock).toHaveBeenCalledWith({
        frontier_remote_dir: "/lustre/orion/abc123/proj-shared/vista",
      }),
    );
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
    // The folder is part of the card's settings check, which the backend
    // recomputes on any request: a refresh, not a fresh probe of the facility.
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
    expect(recheckMock).not.toHaveBeenCalled();
  });

  it("saves a pasted token once, and rechecks only that cluster", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ frontier_s3m_token: "fr-tok" }));
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);

    await userEvent.click(await screen.findByLabelText(/Odo S3M token/));
    await userEvent.paste("new-odo");
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ odo_s3m_token: "new-odo" }));
    await userEvent.tab(); // leaving it changes nothing more
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).toHaveBeenCalledWith("odo");
    expect(refreshAgentSettingsMock).not.toHaveBeenCalled();
  });

  it("saves nothing of a token typed by hand until the field loses focus", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);

    await userEvent.type(await screen.findByLabelText(/Odo S3M token/), "abc");
    await new Promise((r) => setTimeout(r, 1000)); // past the text pause
    expect(updateSettingsMock).not.toHaveBeenCalled();
    await userEvent.tab();
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ odo_s3m_token: "abc" }));
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
  });

  it("saves a cleared token as not set", async () => {
    fetchSettingsMock.mockResolvedValue(
      settingsRow({ odo_s3m_token: "odo-tok", frontier_s3m_token: "fr-tok" }),
    );
    render(<UserSettingsModal onClose={() => {}} initialSection="frontier" />);

    await userEvent.clear(await screen.findByLabelText(/Frontier S3M token/));
    await userEvent.tab();
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ frontier_s3m_token: null }));
    expect(recheckMock).toHaveBeenCalledWith("frontier");
  });

  it("still saves a change made straight before closing", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    const onClose = vi.fn();
    render(<UserSettingsModal onClose={onClose} initialSection="frontier" />);

    await userEvent.type(await screen.findByLabelText(/Frontier remote directory/), "/frontier/vista");
    expect(updateSettingsMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close settings" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(updateSettingsMock).toHaveBeenCalledWith({ frontier_remote_dir: "/frontier/vista" });
  });

  it("hiding a cluster saves the list at once, leaves its token alone, and refreshes the rail", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ nersc_iri_token: "iri-tok" }));
    render(<UserSettingsModal onClose={() => {}} initialSection="perlmutter" />);

    const toggle = await screen.findByRole("switch", { name: "Show Perlmutter in sidebar" });
    await userEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(entry("Compute")).toHaveTextContent("3 of 3");
    const compute = await openOn("Compute");
    expect(within(compute).getByRole("button", { name: /^Perlmutter/ })).toHaveTextContent("Hidden");

    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ hpc_hidden_clusters: ["perlmutter"] }));
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
    expect(recheckMock).not.toHaveBeenCalled();
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("showing a hidden cluster again sends an empty list", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ hpc_hidden_clusters: ["odo"] }));
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Odo in sidebar" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ hpc_hidden_clusters: [] }));
  });

  it("an unchanged field triggers no save and no recheck", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ odo_remote_dir: "/odo" }));
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);
    await userEvent.click(await screen.findByLabelText(/Odo remote directory/));
    await userEvent.tab();
    await userEvent.click(screen.getByRole("button", { name: "Close settings" }));
    expect(updateSettingsMock).not.toHaveBeenCalled();
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
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Odo in sidebar" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalled());
    expect(screen.queryByText("Saving…")).toBeNull();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("checks the saved field only, shows nothing while it saves, and announces it", async () => {
    let release: (value: LocalSettings) => void = () => {};
    updateSettingsMock.mockImplementation(() => new Promise((resolve) => (release = resolve)));
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);

    const dir = await screen.findByLabelText(/Odo remote directory/);
    const token = screen.getByLabelText(/Odo S3M token/);
    await userEvent.type(dir, "/odo/vista");
    await userEvent.tab();
    expect(markOf(dir)).toBeNull(); // in flight: nothing to see
    await act(async () => release(settingsRow()));
    await waitFor(() => expect(markOf(dir)).toBe("saved"));
    expect(markOf(token)).toBeNull();
    expect(announced()).toBe("Odo remote directory saved.");
  });

  it("marks a switch beside it", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);
    const toggle = await screen.findByRole("switch", { name: "Show Odo in sidebar" });
    await userEvent.click(toggle);
    await waitFor(() => expect(markOf(toggle)).toBe("saved"));
    expect(announced()).toBe("Show Odo in sidebar saved.");
  });

  it("shows a slow save as still saving", async () => {
    let release: (value: LocalSettings) => void = () => {};
    updateSettingsMock.mockImplementation(() => new Promise((resolve) => (release = resolve)));
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="lux" />);

    const account = await screen.findByLabelText(/Lux account/);
    await userEvent.type(account, "abc123");
    await userEvent.tab();
    await waitFor(() => expect(markOf(account)).toBe("slow"), { timeout: 2000 });
    await act(async () => release(settingsRow()));
    await waitFor(() => expect(markOf(account)).toBe("saved"));
  });

  it("marks a failure with a cross and the reason, keeping what was typed", async () => {
    updateSettingsMock.mockRejectedValueOnce(new Error("The remote directory must be absolute."));
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="perlmutter" />);

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
    updateSettingsMock.mockImplementationOnce(() => new Promise((_, r) => (reject = r)));
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="perlmutter" />);

    await userEvent.type(await screen.findByLabelText(/NERSC remote directory/), "relative/dir");
    await openOn("Models and providers"); // leaving the field sends it
    await act(async () => reject(new Error("The remote directory must be absolute.")));

    await waitFor(() => expect(entry("Compute")).toHaveAccessibleName(/couldn't save$/));
    expect(entry("Models and providers")).not.toHaveAccessibleName(/couldn't save/);
    const again = within(await openOn("Perlmutter")).getByLabelText(/NERSC remote directory/);
    expect(again).toHaveValue("relative/dir");
    await waitFor(() => expect(again).toHaveFocus());
  });

  it("clears the cross, the reason and the navigation mark once the field saves", async () => {
    updateSettingsMock.mockRejectedValueOnce(new Error("The remote directory must be absolute."));
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="perlmutter" />);

    const dir = await screen.findByLabelText(/NERSC remote directory/);
    await userEvent.type(dir, "relative");
    await userEvent.tab();
    await waitFor(() => expect(markOf(dir)).toBe("failed"));

    await userEvent.clear(dir);
    await userEvent.type(dir, "/pscratch/vista");
    await userEvent.tab();
    await waitFor(() => expect(markOf(dir)).toBe("saved"));
    expect(screen.queryByText("The remote directory must be absolute.")).toBeNull();
    expect(entry("Compute")).not.toHaveAccessibleName(/couldn't save/);
    expect(updateSettingsMock).toHaveBeenLastCalledWith({ nersc_remote_dir: "/pscratch/vista" });
  });
});

describe("UserSettingsModal: Models & providers", () => {
  async function providerRegion() {
    return openOn("Models and providers");
  }

  function provider(agent: HTMLElement, name: string) {
    return within(agent).getByRole("radio", { name: new RegExp(`^${name}`) });
  }

  it("offers the backend's providers in order, with MAG's token and no Model or endpoint field", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ inference_mag_api_key: "mag-token" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await providerRegion();

    const choices = within(agent).getByRole("radiogroup", { name: "Inference provider" });
    expect(within(choices).getAllByRole("radio").map((choice) => choice.querySelector(".settings-provider-name")?.textContent)).toEqual([
      "AmSC MAG", "OLCF Inference", "Custom",
    ]);
    expect(provider(agent, "AmSC MAG")).toBeChecked();
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toHaveValue("mag-token");
    expect(agent).toHaveTextContent("Uses claude-sonnet");
    expect(within(agent).queryByLabelText(/^Model/)).toBeNull();
    expect(within(agent).queryByLabelText(/^Inference endpoint/)).toBeNull();
  });

  it("neither offers nor links to the retired AmSC i2", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await providerRegion();

    expect(within(agent).queryByRole("radio", { name: /AmSC i2/ })).toBeNull();
    expect(agent.querySelector('a[href*="i2-core"]')).toBeNull();
    expect(agent).not.toHaveTextContent("i2");
  });

  it("switching provider shows that provider's own key field", async () => {
    fetchSettingsMock.mockResolvedValue(
      settingsRow({ inference_mag_api_key: "mag-token", inference_olcf_api_key: "s3m" }),
    );
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await providerRegion();

    const mag = provider(agent, "AmSC MAG");
    expect(mag).toHaveAttribute("tabindex", "0");
    mag.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(provider(agent, "OLCF Inference")).toBeChecked();
    expect(provider(agent, "OLCF Inference")).toHaveFocus();
    expect(within(agent).getByLabelText(/^OLCF Inference S3M token/)).toHaveValue("s3m");
    expect(within(agent).queryByLabelText(/^AmSC MAG project access token/)).toBeNull();
    expect(within(agent).queryByLabelText(/^Inference endpoint/)).toBeNull();
    expect(agent).toHaveTextContent("separate from Odo's and Frontier's");

    await userEvent.click(provider(agent, "Custom"));
    expect(agent).toHaveTextContent("no default model");

    await userEvent.click(provider(agent, "AmSC MAG"));
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toHaveValue("mag-token");
  });

  it("only Custom shows an endpoint field; the choice saves at once, then the endpoint and key", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await providerRegion();

    await userEvent.click(provider(agent, "Custom"));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ inference_provider: "custom" }));
    await userEvent.type(within(agent).getByLabelText(/^Inference endpoint/), "https://gw.example/v1");
    await userEvent.type(within(agent).getByLabelText(/^Custom endpoint API key/), "c-key");
    await userEvent.tab();

    await waitFor(() =>
      expect(updateSettingsMock).toHaveBeenCalledWith({ inference_custom_api_key: "c-key" }),
    );
    expect(updateSettingsMock).toHaveBeenCalledWith({ inference_base_url: "https://gw.example/v1" });
    expect(updateSettingsMock).toHaveBeenCalledTimes(3);
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
    fetchSettingsMock.mockResolvedValue(settingsRow({ inference_api_key: "row-key" }));
    render(<UserSettingsModal onClose={() => {}} />);
    const agent = await providerRegion();

    expect(provider(agent, "Installation configuration")).toBeChecked();
    expect(agent).toHaveTextContent("https://dev.example/v1");
    // The configured endpoint uses the saved key.
    expect(within(agent).getByLabelText(/^API key/)).toHaveValue("row-key");

    await userEvent.click(provider(agent, "AmSC MAG"));
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toBeInTheDocument();
  });

  it("marks the section not ready when the effective provider has no key", async () => {
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({ hasCredential: false, keysSet: { mag: false, olcf: false, custom: false } }),
      error: null,
      modelChoiceRequest: 0,
    });
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Models and providers")).toHaveTextContent("Not ready");
  });

  it("does not treat an installation fallback key as this researcher's ready state", async () => {
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({
        source: "default",
        hasCredential: true,
        keysSet: { mag: false, olcf: false, custom: false },
      }),
      error: null,
      modelChoiceRequest: 0,
    });
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Models and providers")).toHaveTextContent("Not ready");
  });

  it("does not call a key-only provider ready before a model is chosen", async () => {
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({
        provider: "custom",
        baseUrl: "https://custom.example/v1",
        model: null,
        modelIsDefault: false,
        hasCredential: true,
        keysSet: { mag: false, olcf: false, custom: true },
      }),
      error: null,
      modelChoiceRequest: 0,
    });
    fetchSettingsMock.mockResolvedValue(settingsRow({ inference_provider: "custom" }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Models and providers")).toHaveTextContent("Not ready");
  });

  it("reads a configured endpoint's readiness from its own saved key", async () => {
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({
        provider: "custom",
        source: "config",
        baseUrl: "https://dev.example/v1",
        keysSet: { mag: false, olcf: false, custom: false },
        configKeySet: true,
      }),
      error: null,
      modelChoiceRequest: 0,
    });
    fetchSettingsMock.mockResolvedValue(settingsRow({ inference_api_key: "row-key" }));
    render(<UserSettingsModal onClose={() => {}} />);
    await region("Research profile");
    expect(entry("Models and providers")).toHaveTextContent("Ready");
    expect(entry("Models and providers")).not.toHaveTextContent("Not ready");
  });
});

describe("UserSettingsModal: Globus address", () => {
  const URL = "https://auth.globus.org/v2/oauth2/authorize?client_id=abc&scope=" + "x".repeat(300);

  beforeEach(() => {
    window.sessionStorage.clear();
    startGlobusLoginMock.mockReset();
    startGlobusLoginMock.mockResolvedValue({ authorize_url: URL });
  });

  async function startOdo() {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="odo" />);
    const odo = await region("Odo");
    await userEvent.click(within(odo).getByRole("button", { name: "Connect" }));
    return within(odo).findByRole("textbox", { name: "Odo Globus login address" });
  }

  it("shows the address on one line with a Copy button, beside Open in browser", async () => {
    const writeText = vi.fn(async () => {});
    const ue = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const line = await startOdo();
    expect(line).toHaveTextContent(URL);
    expect(line).toHaveClass("user-settings-globus-url");
    expect(screen.getByRole("link", { name: "Open in browser" })).toHaveAttribute("href", URL);

    await ue.click(screen.getByRole("button", { name: "Copy address" }));
    expect(writeText).toHaveBeenCalledWith(URL);
    expect(await screen.findByText("Copied")).toHaveClass("user-settings-globus-copied-flash");
  });

  it("selects the address when the clipboard refuses", async () => {
    const ue = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(async () => Promise.reject(new Error("denied"))) },
      configurable: true,
    });

    const line = await startOdo();
    await ue.click(screen.getByRole("button", { name: "Copy address" }));
    expect(await screen.findByText(/Couldn't copy here/)).toBeInTheDocument();
    expect(window.getSelection()?.toString()).toBe(line.textContent);
  });
});

describe("UserSettingsModal: Lux", () => {
  it("opened from the Lux card, shows the sidebar switch, the account and the remote directory", async () => {
    fetchSettingsMock.mockResolvedValue(
      settingsRow({ lux_remote_dir: "/lux/vista", lux_account: "abc123" }),
    );
    render(<UserSettingsModal onClose={() => {}} initialSection="lux" />);
    const lux = await region("Lux");
    expect(entry("Compute")).toHaveAttribute("aria-current", "page");
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
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="lux" />);
    await userEvent.type(await screen.findByLabelText(/Lux account/), "abc123");
    await userEvent.tab();
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ lux_account: "abc123" }));
    expect(updateSettingsMock).toHaveBeenCalledTimes(1);
    // The Lux card's settings check reads the account.
    await waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("clearing the Lux remote directory sends null", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow({ lux_remote_dir: "/lux/vista" }));
    render(<UserSettingsModal onClose={() => {}} initialSection="lux" />);
    await userEvent.clear(await screen.findByLabelText(/Lux remote directory/));
    await userEvent.tab();
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ lux_remote_dir: null }));
  });

  it("hiding Lux saves the list and refreshes the rail without a recheck", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} initialSection="lux" />);
    await userEvent.click(await screen.findByRole("switch", { name: "Show Lux in sidebar" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ hpc_hidden_clusters: ["lux"] }));
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
    fetchSettingsMock.mockResolvedValue(settingsRow());
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
    fetchSettingsMock.mockResolvedValue(settingsRow());
    render(<UserSettingsModal onClose={() => {}} />);
    await openAppearance();
    await userEvent.click(radio(name));
    expect(radio(name)).toBeChecked();
    expect(document.documentElement.getAttribute("data-theme")).toBe(value);
    expect(window.localStorage.getItem("vista.theme")).toBe(value);
    expect(updateSettingsMock).not.toHaveBeenCalled();
  });

  it("selecting System clears the override", async () => {
    fetchSettingsMock.mockResolvedValue(settingsRow());
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
    fetchSettingsMock.mockResolvedValue(settingsRow());
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
    fetchSettingsMock.mockReturnValue(new Promise(() => {}));
    render(<UserSettingsModal onClose={() => {}} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await openAppearance();
    expect(radio("System")).toBeChecked();
  });
});
