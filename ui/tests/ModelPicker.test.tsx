import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelPicker } from "@/components/ModelPicker";
import type { UseAvailableModelsResult } from "@/lib/models";
import type { Project } from "@/lib/projects";
import type { UserPublicWithConfig } from "@/lib/user";

const { useActiveProjectMock } = vi.hoisted(() => ({ useActiveProjectMock: vi.fn() }));
vi.mock("@/lib/projects", () => ({ useActiveProject: useActiveProjectMock }));

const { useAvailableModelsMock } = vi.hoisted(() => ({ useAvailableModelsMock: vi.fn() }));
vi.mock("@/lib/models", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/models")>()),
  useAvailableModels: useAvailableModelsMock,
}));

const { fetchCurrentUserWithConfigMock, updateCurrentUserMock } = vi.hoisted(() => ({
  fetchCurrentUserWithConfigMock: vi.fn(),
  updateCurrentUserMock: vi.fn(),
}));
vi.mock("@/lib/user", () => ({
  fetchCurrentUserWithConfig: fetchCurrentUserWithConfigMock,
  updateCurrentUser: updateCurrentUserMock,
}));

const PROJECT: Project = {
  id: "p1",
  name: "molten-salt",
  description: "",
  systemPrompt: "",
  skills: [],
  knowledgeBases: [],
  forumRepoUrl: "",
  tools: [],
  usageLimits: {},
};

function userWithModel(model: string | null): UserPublicWithConfig {
  return {
    id: "u1",
    email: "researcher@ornl.gov",
    is_admin: false,
    inference_model: model,
    inference_base_url: null,
    inference_api_key: null,
    nersc_account: null,
    nersc_remote_dir: null,
    odo_s3m_token: null,
    frontier_s3m_token: null,
    nersc_iri_token: null,
    globus_token: null,
    odo_globus_token: null,
    frontier_globus_token: null,
    globus_https_token: null,
    odo_globus_https_token: null,
    frontier_globus_https_token: null,
  };
}

function mockModels(state: UseAvailableModelsResult) {
  useAvailableModelsMock.mockReturnValue(state);
}

const READY_TWO_MODELS: UseAvailableModelsResult = {
  status: "ready",
  models: [
    { id: "claude-sonnet", ownedBy: "openai" },
    { id: "claude-opus", ownedBy: "openai" },
  ],
  refresh: vi.fn(),
};

beforeEach(() => {
  useActiveProjectMock.mockReturnValue(PROJECT);
  fetchCurrentUserWithConfigMock.mockResolvedValue(userWithModel("claude-sonnet"));
  updateCurrentUserMock.mockResolvedValue(userWithModel("claude-opus"));
  mockModels(READY_TWO_MODELS);
});

describe("ModelPicker", () => {
  it("shows the researcher's current model once loaded", async () => {
    render(<ModelPicker />);
    expect(
      await screen.findByRole("button", { name: /Model: claude-sonnet/ }),
    ).toBeInTheDocument();
  });

  it("lists the fetched models when opened", async () => {
    render(<ModelPicker />);
    await userEvent.click(await screen.findByRole("button", { name: /Model:/ }));
    expect(screen.getByRole("option", { name: /claude-sonnet/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /claude-opus/ })).toBeInTheDocument();
  });

  it("selecting an option updates the button and writes inference_model", async () => {
    render(<ModelPicker />);
    await userEvent.click(await screen.findByRole("button", { name: /Model: claude-sonnet/ }));
    await userEvent.click(screen.getByRole("option", { name: /claude-opus/ }));

    await waitFor(() =>
      expect(updateCurrentUserMock).toHaveBeenCalledWith({
        inference_model: "openai:claude-opus",
      }),
    );
    expect(
      await screen.findByRole("button", { name: /Model: claude-opus/ }),
    ).toBeInTheDocument();
    // The dropdown closes and returns focus to the trigger, same as ProjectSwitcher.
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("shows missing-credential guidance instead of a list", async () => {
    mockModels({
      status: "missing-credential",
      detail: "No inference API key is configured. Add one in Settings.",
      refresh: vi.fn(),
    });
    render(<ModelPicker />);
    await userEvent.click(await screen.findByRole("button", { name: /Model:/ }));

    expect(screen.getByText(/No inference API key is configured/)).toBeInTheDocument();
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });

  it("does not present a live-looking list when discovery is unavailable", async () => {
    mockModels({ status: "unavailable", refresh: vi.fn() });
    render(<ModelPicker />);
    await userEvent.click(await screen.findByRole("button", { name: /Model:/ }));

    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText(/doesn't report which models/)).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    render(<ModelPicker />);
    await userEvent.click(await screen.findByRole("button", { name: /Model:/ }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes when a click lands outside the picker", async () => {
    render(
      <div>
        <ModelPicker />
        <button type="button">Outside</button>
      </div>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Model:/ }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
