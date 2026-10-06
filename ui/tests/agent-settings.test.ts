import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  holdSendWithoutModel,
  refreshAgentSettings,
  resetAgentSettingsForTests,
  useAgentSettings,
} from "@/lib/agent-settings";
import { qualifyModelInput } from "@/lib/models";

const PROVIDERS = [
  { id: "i2", name: "AmSC i2", takes_url: false, default_model: "claude-sonnet" },
  { id: "mag", name: "AmSC MAG", takes_url: false, default_model: null },
  { id: "custom", name: "Custom", takes_url: true, default_model: null },
];

function inferenceView(overrides: Record<string, unknown> = {}) {
  return {
    providers: PROVIDERS,
    provider: "i2",
    source: "default",
    base_url: "https://api.i2-core.american-science-cloud.org",
    model: "openai:claude-sonnet",
    model_is_default: true,
    has_credential: true,
    keys_set: { i2: true, mag: false, custom: false },
    ...overrides,
  };
}

let current: Record<string, unknown>;
const fetchMock = vi.fn();

beforeEach(() => {
  resetAgentSettingsForTests();
  current = inferenceView();
  fetchMock.mockReset();
  fetchMock.mockImplementation(
    async () =>
      new Response(JSON.stringify(current), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("agent settings store", () => {
  it("loads on first subscribe and maps the view", async () => {
    const { result } = renderHook(() => useAgentSettings());
    await waitFor(() => expect(result.current.settings).not.toBeNull());
    expect(fetchMock).toHaveBeenCalledWith("/api/users/me/inference", expect.anything());
    expect(result.current.settings).toMatchObject({
      provider: "i2",
      model: "openai:claude-sonnet",
      modelIsDefault: true,
      keysSet: { i2: true, mag: false, custom: false },
    });
    expect(result.current.settings?.providers[0]).toEqual({
      id: "i2",
      name: "AmSC i2",
      takesUrl: false,
      defaultModel: "claude-sonnet",
    });
  });

  it("a refresh updates every subscriber", async () => {
    const a = renderHook(() => useAgentSettings());
    const b = renderHook(() => useAgentSettings());
    await waitFor(() => expect(a.result.current.settings?.provider).toBe("i2"));

    current = inferenceView({ provider: "mag", source: "user", model: null, model_is_default: false });
    await act(() => refreshAgentSettings());

    expect(a.result.current.settings?.provider).toBe("mag");
    expect(b.result.current.settings?.provider).toBe("mag");
    expect(b.result.current.settings?.model).toBeNull();
  });

  it("holds a send only when there is no model", async () => {
    const { result } = renderHook(() => useAgentSettings());
    await waitFor(() => expect(result.current.settings).not.toBeNull());
    expect(holdSendWithoutModel()).toBe(false);
    expect(result.current.modelChoiceRequest).toBe(0);

    current = inferenceView({ provider: "mag", model: null, model_is_default: false });
    await act(() => refreshAgentSettings());
    let held = false;
    act(() => {
      held = holdSendWithoutModel();
    });
    expect(held).toBe(true);
    // The picker watches this to open asking for a model.
    expect(result.current.modelChoiceRequest).toBe(1);
  });

  it("does not hold a send before the first answer", () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    renderHook(() => useAgentSettings());
    expect(holdSendWithoutModel()).toBe(false);
  });
});

describe("qualifyModelInput", () => {
  it("always means the chosen provider, colons included", () => {
    expect(qualifyModelInput("anthropic.claude-sonnet-v1:0")).toBe(
      "openai:anthropic.claude-sonnet-v1:0",
    );
    expect(qualifyModelInput("  gpt-oss ")).toBe("openai:gpt-oss");
    expect(qualifyModelInput("")).toBe("");
  });
});
