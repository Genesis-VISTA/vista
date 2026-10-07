import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelPicker } from "@/components/ModelPicker";
import {
  holdSendWithoutModel,
  refreshAgentSettings,
  resetAgentSettingsForTests,
} from "@/lib/agent-settings";
import type { Project } from "@/lib/projects";

/*
 * The agent-settings store and the model list are the real ones; only the
 * network is faked, routed by URL, so these tests cover the picker and the
 * store together the way Settings and the picker share them.
 */

const { useActiveProjectMock } = vi.hoisted(() => ({ useActiveProjectMock: vi.fn() }));
vi.mock("@/lib/projects", () => ({ useActiveProject: useActiveProjectMock }));

const { updateCurrentUserMock } = vi.hoisted(() => ({ updateCurrentUserMock: vi.fn() }));
vi.mock("@/lib/user", () => ({ updateCurrentUser: updateCurrentUserMock }));

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

const PROVIDERS = [
  { id: "i2", name: "AmSC i2", takes_url: false, default_model: "claude-sonnet" },
  { id: "mag", name: "AmSC MAG", takes_url: false, default_model: null },
  { id: "olcf", name: "OLCF Inference", takes_url: false, default_model: "gpt-oss-120b" },
  { id: "custom", name: "Custom", takes_url: true, default_model: null },
];

function view(overrides: Record<string, unknown> = {}) {
  return {
    providers: PROVIDERS,
    provider: "i2",
    source: "default",
    base_url: "https://api.i2-core.american-science-cloud.org",
    model: "openai:claude-sonnet",
    model_is_default: true,
    has_credential: true,
    keys_set: { i2: true, mag: false, olcf: false, custom: false },
    ...overrides,
  };
}

const CHOSEN_OPUS = { model: "openai:claude-opus", model_is_default: false };
const MAG_NO_MODEL = {
  provider: "mag",
  source: "user",
  base_url: "https://i2-api.staging.american-science-cloud.org/v1",
  model: null,
  model_is_default: false,
  keys_set: { i2: true, mag: true, olcf: false, custom: false },
};

let inference: Record<string, unknown>;
let models: { status: number; body: unknown };
let modelFetches = 0;

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  resetAgentSettingsForTests();
  useActiveProjectMock.mockReturnValue(PROJECT);
  inference = view();
  models = {
    status: 200,
    body: {
      supported: true,
      models: [
        { id: "claude-sonnet", owned_by: "openai" },
        { id: "claude-opus", owned_by: "openai" },
      ],
    },
  };
  modelFetches = 0;
  updateCurrentUserMock.mockReset();
  updateCurrentUserMock.mockImplementation(async (update: { inference_model: string }) => {
    inference = { ...inference, model: update.inference_model, model_is_default: false };
    return {};
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/users/me/inference") return json(200, inference);
      if (url === "/api/projects/molten-salt/models") {
        modelFetches++;
        return json(models.status, models.body);
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openPicker() {
  await userEvent.click(await screen.findByRole("button", { name: /Model: (?!Model\.)/ }));
}

describe("ModelPicker label", () => {
  it("names the provider's default", async () => {
    render(<ModelPicker />);
    expect(
      await screen.findByRole("button", { name: /Model: Default \(claude-sonnet\)/ }),
    ).toBeInTheDocument();
  });

  it("shows a chosen model without its prefix", async () => {
    inference = view(CHOSEN_OPUS);
    render(<ModelPicker />);
    expect(await screen.findByRole("button", { name: /^Model: claude-opus\. / })).toBeInTheDocument();
  });

  it("says there is no model on a provider without a default", async () => {
    inference = view(MAG_NO_MODEL);
    render(<ModelPicker />);
    expect(await screen.findByRole("button", { name: /Model: No model/ })).toBeInTheDocument();
  });

  it("marks a chosen model the provider does not list", async () => {
    inference = view({ model: "openai:retired-model", model_is_default: false });
    render(<ModelPicker />);
    expect(
      await screen.findByRole("button", {
        name: /Model: retired-model, not listed by AmSC i2/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("not listed by AmSC i2")).toBeInTheDocument();
  });

  it("follows a provider change without a remount, and refetches the list", async () => {
    inference = view(CHOSEN_OPUS);
    render(<ModelPicker />);
    await screen.findByRole("button", { name: /^Model: claude-opus\. / });
    const before = modelFetches;

    // What Settings does after saving a provider: the backend has cleared
    // the model, and Settings refreshes the shared store.
    inference = view(MAG_NO_MODEL);
    await act(() => refreshAgentSettings());

    expect(await screen.findByRole("button", { name: /Model: No model/ })).toBeInTheDocument();
    await waitFor(() => expect(modelFetches).toBe(before + 1));
  });
});

describe("ModelPicker menu", () => {
  it("lists the fetched models, sorted, and marks the current one", async () => {
    render(<ModelPicker />);
    await openPicker();
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["claude-opus", "claude-sonnet"]);
    expect(screen.getByRole("option", { name: "claude-sonnet" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("refetches the list each time it opens", async () => {
    render(<ModelPicker />);
    await screen.findByRole("button", { name: /Default/ });
    await waitFor(() => expect(modelFetches).toBe(1));
    await openPicker();
    await waitFor(() => expect(modelFetches).toBe(2));
  });

  it("selecting a model saves it and the label follows from the store", async () => {
    render(<ModelPicker />);
    await openPicker();
    await userEvent.click(await screen.findByRole("option", { name: "claude-opus" }));

    expect(updateCurrentUserMock).toHaveBeenCalledWith({ inference_model: "openai:claude-opus" });
    expect(await screen.findByRole("button", { name: /^Model: claude-opus\. / })).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("a typed name is saved as openai:<name>, colons included", async () => {
    render(<ModelPicker />);
    await openPicker();
    await userEvent.click(screen.getByRole("button", { name: "Use another model…" }));
    await userEvent.type(screen.getByLabelText("Model name"), "anthropic.claude-sonnet-v1:0");
    await userEvent.click(screen.getByRole("button", { name: "Use" }));

    expect(updateCurrentUserMock).toHaveBeenCalledWith({
      inference_model: "openai:anthropic.claude-sonnet-v1:0",
    });
    expect(
      await screen.findByRole("button", { name: /Model: anthropic\.claude-sonnet-v1:0, not listed/ }),
    ).toBeInTheDocument();
  });

  it("offers a typed name when listing is unavailable, pointing to it", async () => {
    models = { status: 200, body: { supported: false, models: [] } };
    render(<ModelPicker />);
    await openPicker();
    expect(await screen.findByText(/doesn't report which models/)).toHaveTextContent(
      "Use another model…",
    );
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use another model…" })).toBeInTheDocument();
    expect(screen.queryByText(/bottom-left corner/)).not.toBeInTheDocument();
  });

  it("offers a typed name when listing fails, pointing to it", async () => {
    models = { status: 502, body: { detail: "Gateway unreachable" } };
    render(<ModelPicker />);
    await openPicker();
    expect(await screen.findByText(/Gateway unreachable/)).toHaveTextContent(
      "Use another model…",
    );
    expect(screen.getByRole("button", { name: "Use another model…" })).toBeInTheDocument();
  });

  it("shows missing-credential guidance instead of a list", async () => {
    models = { status: 409, body: { detail: "No inference API key is configured." } };
    render(<ModelPicker />);
    await openPicker();
    expect(await screen.findByText(/No inference API key is configured/)).toBeInTheDocument();
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    render(<ModelPicker />);
    await openPicker();
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
    await openPicker();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});

describe("sending without a model", () => {
  it("holds the send and opens the picker asking for a model", async () => {
    inference = view(MAG_NO_MODEL);
    render(<ModelPicker />);
    await screen.findByRole("button", { name: /Model: No model/ });

    let held = false;
    act(() => {
      held = holdSendWithoutModel();
    });

    expect(held).toBe(true);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Choose a model first. AmSC MAG has no default model.",
    );
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("does not hold a send on a provider with a default", async () => {
    render(<ModelPicker />);
    await screen.findByRole("button", { name: /Default/ });
    expect(holdSendWithoutModel()).toBe(false);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
