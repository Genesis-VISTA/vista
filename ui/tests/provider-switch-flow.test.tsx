import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelPicker } from "@/components/ModelPicker";
import { UserSettingsModal } from "@/components/UserSettingsModal";
import { resetAgentSettingsForTests } from "@/lib/agent-settings";
import { resetHpcStatusForTests } from "@/lib/hpc-status";
import type { Project } from "@/lib/projects";

/*
 * Settings and the model picker together, with every store real and only
 * the network faked. The fake backend keeps one user row and answers
 * `/settings/inference` from it the way `resolve_inference_target` does, so
 * this covers the whole loop: a choice in Settings saves, the backend clears
 * the model, the store refreshes, and the picker's label follows.
 */

const { useActiveProjectMock } = vi.hoisted(() => ({ useActiveProjectMock: vi.fn() }));
vi.mock("@/lib/projects", () => ({ useActiveProject: useActiveProjectMock }));

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

const PRESETS = [
  { id: "mag", name: "AmSC MAG", takes_url: false, default_model: "claude-sonnet", key: "inference_mag_api_key" },
  { id: "olcf", name: "OLCF Inference", takes_url: false, default_model: "gpt-oss-120b", key: "inference_olcf_api_key" },
  { id: "custom", name: "Custom", takes_url: true, default_model: null, key: "inference_custom_api_key" },
] as const;

let row: Record<string, unknown>;
let puts: Array<Record<string, unknown>>;

function effectiveProvider(): string {
  return (row.inference_provider as string | null) ?? "mag";
}

function inferenceView() {
  const preset = PRESETS.find((p) => p.id === effectiveProvider())!;
  const model =
    (row.inference_model as string | null) ?? (preset.default_model ? `openai:${preset.default_model}` : null);
  return {
    providers: PRESETS.map(({ id, name, takes_url, default_model }) => ({ id, name, takes_url, default_model })),
    provider: preset.id,
    source: row.inference_provider ? "user" : "default",
    base_url: "https://gateway.example",
    model,
    model_is_default: row.inference_model == null && model !== null,
    has_credential: Boolean(row[preset.key]),
    keys_set: Object.fromEntries(PRESETS.map((p) => [p.id, Boolean(row[p.key])])),
    config_key_set: Boolean(row.inference_api_key),
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  resetAgentSettingsForTests();
  resetHpcStatusForTests();
  useActiveProjectMock.mockReturnValue(PROJECT);
  puts = [];
  row = {
    install_id: "00000000-0000-4000-8000-000000000001",
    display_name: null,
    email: "researcher@ornl.gov",
    hpc_hidden_clusters: [],
    inference_provider: null,
    // Chosen on MAG before the switch: it must not follow the researcher to Custom.
    inference_model: "openai:claude-opus",
    inference_api_key: "legacy-key",
    inference_mag_api_key: "mag-token",
    inference_custom_api_key: "custom-key",
    inference_base_url: "https://custom.example/v1",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/settings" && init?.method !== "PUT") return json(row);
      if (url === "/api/settings" && init?.method === "PUT") {
        const update = JSON.parse(String(init.body)) as Record<string, unknown>;
        puts.push(update);
        // As `PUT /settings` does: a new provider clears the chosen model.
        if (
          "inference_provider" in update &&
          !("inference_model" in update) &&
          update.inference_provider !== effectiveProvider()
        ) {
          row.inference_model = null;
        }
        row = { ...row, ...update };
        return json(row);
      }
      if (url === "/api/settings/inference") return json(inferenceView());
      if (url === "/api/projects/molten-salt/models") return json({ supported: true, models: [] });
      if (url.startsWith("/api/settings/hpc-status")) return json({ clusters: [] });
      if (url === "/api/collaboration/status") {
        return json({
          configured: false,
          reachable: false,
          reason: "No collaboration server is set.",
          url: null,
          source: null,
          server_version: null,
        });
      }
      throw new Error(`unexpected fetch ${init?.method ?? "GET"} ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("switching provider in Settings", () => {
  it("clears the picker to no model on Custom, and back on MAG shows its default with the token intact", async () => {
    render(
      <>
        <ModelPicker />
        <UserSettingsModal onClose={() => {}} />
      </>,
    );
    expect(await screen.findByRole("button", { name: /^Model: claude-opus\b/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^Models and providers\b/ }));
    const agent = await screen.findByRole("region", { name: "Models & providers" });
    await userEvent.click(within(agent).getByRole("radio", { name: /^Custom/ }));
    expect(await screen.findByRole("button", { name: /^Model: No model/ })).toBeInTheDocument();
    expect(within(agent).getByLabelText(/^Custom endpoint API key/)).toHaveValue("custom-key");

    await userEvent.click(within(agent).getByRole("radio", { name: /^AmSC MAG/ }));
    expect(await screen.findByRole("button", { name: /^Model: Default \(claude-sonnet\)/ })).toBeInTheDocument();
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toHaveValue("mag-token");

    // Only the choice was sent, never a key: switching leaves every key as it was.
    await waitFor(() => expect(puts).toEqual([{ inference_provider: "custom" }, { inference_provider: "mag" }]));
    expect(row.inference_api_key).toBe("legacy-key");
    expect(row.inference_mag_api_key).toBe("mag-token");
    expect(row.inference_custom_api_key).toBe("custom-key");
  });
});
