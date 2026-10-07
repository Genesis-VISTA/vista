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
 * `/users/me/inference` from it the way `resolve_inference_target` does, so
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
};

const PRESETS = [
  { id: "i2", name: "AmSC i2", takes_url: false, default_model: "claude-sonnet", key: "inference_api_key" },
  { id: "mag", name: "AmSC MAG", takes_url: false, default_model: null, key: "inference_mag_api_key" },
  { id: "olcf", name: "OLCF Inference", takes_url: false, default_model: "gpt-oss-120b", key: "inference_olcf_api_key" },
  { id: "custom", name: "Custom", takes_url: true, default_model: null, key: "inference_custom_api_key" },
] as const;

let row: Record<string, unknown>;
let puts: Array<Record<string, unknown>>;

function effectiveProvider(): string {
  return (row.inference_provider as string | null) ?? "i2";
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
    id: "u1",
    email: "researcher@ornl.gov",
    is_admin: false,
    hpc_hidden_clusters: [],
    inference_provider: null,
    // Chosen on i2 before the switch: it must not follow the researcher to MAG.
    inference_model: "openai:claude-opus",
    inference_api_key: "i2-key",
    inference_mag_api_key: "mag-token",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/users/me?config=true") return json(row);
      if (url === "/api/users/me" && init?.method === "PUT") {
        const update = JSON.parse(String(init.body)) as Record<string, unknown>;
        puts.push(update);
        // As `PUT /users/me` does: a new provider clears the chosen model.
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
      if (url === "/api/users/me/inference") return json(inferenceView());
      if (url === "/api/projects/molten-salt/models") return json({ supported: true, models: [] });
      if (url.startsWith("/api/users/me/hpc-status")) return json({ clusters: [] });
      throw new Error(`unexpected fetch ${init?.method ?? "GET"} ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("switching provider in Settings", () => {
  it("clears the picker to no model on MAG, and back on i2 shows its default with the key intact", async () => {
    render(
      <>
        <ModelPicker />
        <UserSettingsModal onClose={() => {}} />
      </>,
    );
    expect(await screen.findByRole("button", { name: /^Model: claude-opus\b/ })).toBeInTheDocument();

    const agent = await screen.findByRole("region", { name: "Agent" });
    const select = within(agent).getByLabelText(/^Inference provider/);
    await userEvent.selectOptions(select, "mag");
    expect(await screen.findByRole("button", { name: /^Model: No model/ })).toBeInTheDocument();
    expect(within(agent).getByLabelText(/^AmSC MAG project access token/)).toHaveValue("mag-token");

    await userEvent.selectOptions(select, "i2");
    expect(await screen.findByRole("button", { name: /^Model: Default \(claude-sonnet\)/ })).toBeInTheDocument();
    expect(within(agent).getByLabelText(/^AmSC i2 API key/)).toHaveValue("i2-key");

    // Only the choice was sent, never a key: switching leaves every key as it was.
    await waitFor(() => expect(puts).toEqual([{ inference_provider: "mag" }, { inference_provider: "i2" }]));
    expect(row.inference_api_key).toBe("i2-key");
    expect(row.inference_mag_api_key).toBe("mag-token");
  });
});
