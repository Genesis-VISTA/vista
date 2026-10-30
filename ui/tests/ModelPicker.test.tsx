import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { ModelPicker } from "@/components/ModelPicker";
import { RoleModelSelects } from "@/components/RoleModelSelects";
import { noRoleModels } from "@/lib/debates";
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

const { updateSettingsMock } = vi.hoisted(() => ({ updateSettingsMock: vi.fn() }));
vi.mock("@/lib/settings", () => ({ updateSettings: updateSettingsMock }));

const { savePersistedChatSessionMock } = vi.hoisted(() => ({
  savePersistedChatSessionMock: vi.fn(),
}));
vi.mock("@/lib/chat-session", () => ({
  savePersistedChatSession: savePersistedChatSessionMock,
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
  originServer: null,
  remoteId: null,
  remoteVersion: null,
  modified: true,
};

const PROVIDERS = [
  { id: "mag", name: "AmSC MAG", takes_url: false, default_model: "claude-sonnet" },
  { id: "olcf", name: "OLCF Inference", takes_url: false, default_model: "gpt-oss-120b" },
  { id: "custom", name: "Custom", takes_url: true, default_model: null },
];

function view(overrides: Record<string, unknown> = {}) {
  return {
    providers: PROVIDERS,
    provider: "mag",
    source: "default",
    base_url: "https://mag.genesis.americansciencecloud.org/v1",
    model: "openai:claude-sonnet",
    model_is_default: true,
    has_credential: true,
    keys_set: { mag: true, olcf: false, custom: false },
    config_key_set: false,
    ...overrides,
  };
}

const CHOSEN_OPUS = { model: "openai:claude-opus", model_is_default: false };
/** Custom with no model chosen: the one provider that has no default. */
const CUSTOM_NO_MODEL = {
  provider: "custom",
  source: "user",
  base_url: "https://custom.example/v1",
  model: null,
  model_is_default: false,
  keys_set: { mag: true, olcf: false, custom: true },
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
  updateSettingsMock.mockReset();
  savePersistedChatSessionMock.mockReset();
  savePersistedChatSessionMock.mockImplementation(
    async (_project: string, update: { inferenceModel: string | null }) => ({
      id: "c1",
      inference_model: update.inferenceModel,
    }),
  );
  updateSettingsMock.mockImplementation(async (update: { inference_model: string }) => {
    inference = { ...inference, model: update.inference_model, model_is_default: false };
    return {};
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/settings/inference") return json(200, inference);
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
    inference = view(CUSTOM_NO_MODEL);
    render(<ModelPicker />);
    expect(await screen.findByRole("button", { name: /Model: No model/ })).toBeInTheDocument();
  });

  it("marks a chosen model the provider does not list", async () => {
    inference = view({ model: "openai:retired-model", model_is_default: false });
    render(<ModelPicker />);
    expect(
      await screen.findByRole("button", {
        name: /Model: retired-model, not listed by AmSC MAG/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("not listed by AmSC MAG")).toBeInTheDocument();
  });

  it("follows a provider change without a remount, and refetches the list", async () => {
    inference = view(CHOSEN_OPUS);
    render(<ModelPicker />);
    await screen.findByRole("button", { name: /^Model: claude-opus\. / });
    const before = modelFetches;

    // What Settings does after saving a provider: the backend has cleared
    // the model, and Settings refreshes the shared store.
    inference = view(CUSTOM_NO_MODEL);
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

    expect(updateSettingsMock).toHaveBeenCalledWith({ inference_model: "openai:claude-opus" });
    expect(await screen.findByRole("button", { name: /^Model: claude-opus\. / })).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("a typed name is saved as openai:<name>, colons included", async () => {
    render(<ModelPicker />);
    await openPicker();
    await userEvent.click(screen.getByRole("button", { name: "Use another model…" }));
    await userEvent.type(screen.getByLabelText("Model name"), "anthropic.claude-sonnet-v1:0");
    await userEvent.click(screen.getByRole("button", { name: "Use" }));

    expect(updateSettingsMock).toHaveBeenCalledWith({
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

  it("opens on a search box that filters the list, ignoring case", async () => {
    render(<ModelPicker />);
    await openPicker();
    const search = await screen.findByRole("searchbox", { name: "Search models" });
    await waitFor(() => expect(search).toHaveFocus());

    await userEvent.type(search, "OPUS");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["claude-opus"]);

    await userEvent.clear(search);
    await userEvent.type(search, "gpt");
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText("No models match “gpt”.")).toBeInTheDocument();
  });

  it("Enter in the search box chooses the first match", async () => {
    render(<ModelPicker />);
    await openPicker();
    await userEvent.type(await screen.findByRole("searchbox"), "claude{Enter}");

    expect(updateSettingsMock).toHaveBeenCalledWith({ inference_model: "openai:claude-opus" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("clears the search when it closes", async () => {
    render(<ModelPicker />);
    await openPicker();
    await userEvent.type(await screen.findByRole("searchbox"), "opus");
    await userEvent.keyboard("{Escape}");
    await openPicker();
    expect(await screen.findByRole("searchbox")).toHaveValue("");
    expect(await screen.findAllByRole("option")).toHaveLength(2);
  });

  it("shows each model's full id, and the label's, on hover", async () => {
    const long = "meta-llama/Llama-3.3-70B-Instruct-FP8-dynamic-long-context";
    models = { status: 200, body: { supported: true, models: [{ id: long, owned_by: "openai" }] } };
    inference = view({ model: `openai:${long}`, model_is_default: false });
    render(<ModelPicker />);
    expect(await screen.findByTitle(long)).toHaveClass("model-picker-label");
    await openPicker();
    expect(await screen.findByRole("option", { name: long })).toHaveAttribute("title", long);
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
    inference = view(CUSTOM_NO_MODEL);
    render(<ModelPicker />);
    await screen.findByRole("button", { name: /Model: No model/ });

    let held = false;
    act(() => {
      held = holdSendWithoutModel();
    });

    expect(held).toBe(true);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Choose a model first. Custom has no default model.",
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

describe("ModelPicker in a chat", () => {
  /** The page's side of it: it holds the chat's model and hears of a change. */
  function OpenChat({ initial = null }: { initial?: string | null }) {
    const [model, setModel] = useState<string | null>(initial);
    return <ModelPicker chat={{ id: "c1", model, onModelChange: setModel }} />;
  }

  it("follows the default without saying 'this chat'", async () => {
    render(<OpenChat />);
    expect(
      await screen.findByRole("button", { name: /^Model: Default \(claude-sonnet\)\. / }),
    ).toBeInTheDocument();
  });

  it("shows the chat's own model with '· this chat'", async () => {
    inference = view(CHOSEN_OPUS);
    render(<OpenChat initial="openai:claude-sonnet" />);
    expect(
      await screen.findByRole("button", { name: /^Model: claude-sonnet · this chat\. / }),
    ).toBeInTheDocument();
  });

  it("offers 'Use default' only while the chat has a model of its own", async () => {
    const { unmount } = render(<OpenChat />);
    await openPicker();
    expect(screen.queryByRole("button", { name: /Use default/ })).not.toBeInTheDocument();
    unmount();

    render(<OpenChat initial="openai:claude-opus" />);
    await openPicker();
    expect(screen.getByRole("button", { name: "Use default (claude-sonnet)" })).toBeInTheDocument();
  });

  it("selecting a model patches the chat and leaves the default alone", async () => {
    render(<OpenChat />);
    await openPicker();
    await userEvent.click(await screen.findByRole("option", { name: /claude-opus/ }));

    await waitFor(() =>
      expect(savePersistedChatSessionMock).toHaveBeenCalledWith("molten-salt", {
        chatSessionId: "c1",
        inferenceModel: "openai:claude-opus",
      }),
    );
    expect(updateSettingsMock).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("button", { name: /^Model: claude-opus · this chat\. / }),
    ).toBeInTheDocument();
  });

  it("clearing the chat's model restores 'Default (<model>)'", async () => {
    render(<OpenChat initial="openai:claude-opus" />);
    await openPicker();
    await userEvent.click(screen.getByRole("button", { name: "Use default (claude-sonnet)" }));

    await waitFor(() =>
      expect(savePersistedChatSessionMock).toHaveBeenCalledWith("molten-salt", {
        chatSessionId: "c1",
        inferenceModel: null,
      }),
    );
    expect(updateSettingsMock).not.toHaveBeenCalled();
    expect(
      await screen.findByRole("button", { name: /^Model: Default \(claude-sonnet\)\. / }),
    ).toBeInTheDocument();
  });

  it("a held send is answered by choosing a model for the chat", async () => {
    inference = view(CUSTOM_NO_MODEL);
    render(<OpenChat />);
    await screen.findByRole("button", { name: /Model: No model/ });

    act(() => {
      holdSendWithoutModel(null);
    });
    await userEvent.click(await screen.findByRole("option", { name: /claude-opus/ }));

    await waitFor(() => expect(savePersistedChatSessionMock).toHaveBeenCalled());
    expect(updateSettingsMock).not.toHaveBeenCalled();
    expect(holdSendWithoutModel("openai:claude-opus")).toBe(false);
  });

  /**
   * The page's shape around a slow save: one model state for whichever chat is
   * open, a conversation list between chats, and A's save still in flight.
   */
  function Chats() {
    const [open, setOpen] = useState<string | null>("A");
    const [model, setModel] = useState<string | null>(null);
    const show = (id: string | null) => {
      setOpen(id);
      setModel(null); // the page's hydration: the next chat follows the default
    };
    return (
      <>
        <button onClick={() => show("B")}>open chat B</button>
        <button onClick={() => show(null)}>back to the list</button>
        <output data-testid="open">{`${open}:${model ?? "default"}`}</output>
        <ModelPicker
          chat={open ? { id: open, model, onModelChange: setModel } : undefined}
        />
      </>
    );
  }

  async function chooseOpusAndHoldTheSave() {
    let finish!: (row: { id: string; inference_model: string }) => void;
    savePersistedChatSessionMock.mockImplementation(
      () => new Promise((resolve) => (finish = resolve)),
    );
    render(<Chats />);
    await openPicker();
    await userEvent.click(await screen.findByRole("option", { name: /claude-opus/ }));
    expect(savePersistedChatSessionMock).toHaveBeenCalledWith("molten-salt", {
      chatSessionId: "A",
      inferenceModel: "openai:claude-opus",
    });
    return () => act(async () => finish({ id: "A", inference_model: "openai:claude-opus" }));
  }

  it("a save that returns after another chat opened leaves that chat alone", async () => {
    const release = await chooseOpusAndHoldTheSave();
    await userEvent.click(screen.getByRole("button", { name: "open chat B" }));
    await release();

    expect(screen.getByTestId("open")).toHaveTextContent("B:default");
    expect(
      screen.getByRole("button", { name: /^Model: Default \(claude-sonnet\)\. / }),
    ).toBeInTheDocument();
  });

  it("so does one that returns after a trip through the conversation list", async () => {
    const release = await chooseOpusAndHoldTheSave();
    await userEvent.click(screen.getByRole("button", { name: "back to the list" }));
    await userEvent.click(screen.getByRole("button", { name: "open chat B" }));
    await release();

    expect(screen.getByTestId("open")).toHaveTextContent("B:default");
  });
});

describe("RoleModelSelects", () => {
  it("lists the models once for its three menus", async () => {
    render(<RoleModelSelects value={noRoleModels()} onChange={() => {}} />);
    await screen.findByRole("button", { name: /^Referee model: Default \(claude-sonnet\)/ });
    await waitFor(() => expect(modelFetches).toBe(1));

    // Opening one refreshes the shared list, once.
    await userEvent.click(screen.getByRole("button", { name: /^Reviewer model: / }));
    expect(await screen.findByRole("option", { name: /claude-opus/ })).toBeInTheDocument();
    expect(modelFetches).toBe(2);
  });
});
