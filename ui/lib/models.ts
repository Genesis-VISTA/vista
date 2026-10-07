/**
 * Available-models model — the models a researcher's configured inference
 * endpoint actually offers, and a hook over it for the chat window's model
 * picker.
 *
 * Reached through `/api/projects/{name}/models`, which proxies the backend's
 * `GET /projects/{project_name}/models`. That endpoint already distinguishes
 * three outcomes, and this module keeps them distinct rather than collapsing
 * them into a single "empty list":
 *   - `ready`               — a real, possibly-empty list from the endpoint.
 *   - `unavailable`         — the configured provider doesn't support
 *                             listing (`{"supported": false}`); the caller
 *                             should fall back to letting the researcher
 *                             name a model directly (the picker's
 *                             "Use another model…" entry).
 *   - `missing-credential`  — the backend's 409 for no/rejected credential,
 *                             same condition `ui/lib/user.ts` already knows
 *                             how to surface elsewhere in the app.
 */

import { useEffect, useState } from "react";

/**
 * The only provider prefix the UI writes today (see `list_models`'s scoping
 * to `_CONFIGURED_ENDPOINT_PROVIDERS` on the backend) -- so it's an internal
 * routing detail, not something a researcher should have to type or read.
 * `qualifyModelInput` and `displayModelName` are the single place that
 * convention lives; `ModelPicker` uses them rather than hardcoding the prefix.
 */
const OPENAI_PREFIX = "openai:";

/** A bare id from the provider's model list (or the picker) as the qualified id `inference_model` stores. */
export function qualifyModelId(id: string): string {
  return `${OPENAI_PREFIX}${id}`;
}

/**
 * A typed model name as the qualified id `inference_model` stores.
 *
 * Always means the chosen provider, even when the name has a colon in it
 * (`anthropic.claude-sonnet-v1:0`): PydanticAI splits on the first colon
 * only, so the provider receives the name exactly as typed. Another provider,
 * such as `azure:`, is reachable only through `VISTA_BACKEND_MODEL`.
 */
export function qualifyModelInput(value: string): string {
  const trimmed = value.trim();
  if (trimmed === "") return trimmed;
  return qualifyModelId(trimmed);
}

/** A stored `inference_model` value as what a researcher should see. */
export function displayModelName(value: string): string {
  return value.startsWith(OPENAI_PREFIX) ? value.slice(OPENAI_PREFIX.length) : value;
}

export interface ModelInfo {
  id: string;
  ownedBy: string | null;
}

export type ModelsState =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "missing-credential"; detail: string }
  | { status: "error"; detail: string }
  | { status: "ready"; models: ModelInfo[] };

type ModelsListResponse = {
  supported: boolean;
  models: { id: string; owned_by: string | null }[];
};

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

/**
 * Fetch the models available through `projectName`'s configured inference
 * endpoint. Not cached: a stale list here means offering a model that no
 * longer exists, so every call is a fresh request.
 */
export async function fetchAvailableModels(projectName: string): Promise<ModelsState> {
  let res: Response;
  try {
    res = await fetch(`/api/projects/${encodeURIComponent(projectName)}/models`, {
      headers: { accept: "application/json" },
    });
  } catch (e) {
    return {
      status: "error",
      detail: e instanceof Error ? e.message : "Failed to load models",
    };
  }
  if (res.status === 409) {
    return { status: "missing-credential", detail: await extractError(res) };
  }
  if (!res.ok) {
    return { status: "error", detail: await extractError(res) };
  }
  const data = (await res.json()) as ModelsListResponse;
  if (!data.supported) return { status: "unavailable" };
  return {
    status: "ready",
    models: (data.models ?? []).map((m) => ({ id: m.id, ownedBy: m.owned_by ?? null })),
  };
}

export type UseAvailableModelsResult = ModelsState & { refresh: () => Promise<void> };

/**
 * Subscribe to `projectName`'s available models. Fetches on mount and
 * whenever `projectName` or `sourceKey` changes; `null` (no active project
 * yet) stays `loading` rather than firing a request that would 404.
 *
 * `sourceKey` names where the list comes from -- the provider, endpoint and
 * whether it has a key -- so a change in Settings refetches without a reload.
 */
export function useAvailableModels(
  projectName: string | null,
  sourceKey = "",
): UseAvailableModelsResult {
  const [state, setState] = useState<ModelsState>({ status: "loading" });

  async function refresh(): Promise<void> {
    if (!projectName) {
      setState({ status: "loading" });
      return;
    }
    setState({ status: "loading" });
    setState(await fetchAvailableModels(projectName));
  }

  useEffect(() => {
    void refresh();
    // `refresh` closes over `projectName`, which is already the dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectName, sourceKey]);

  return { ...state, refresh };
}
