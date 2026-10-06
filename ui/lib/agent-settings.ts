"use client";

import { useSyncExternalStore } from "react";

/**
 * The researcher's inference provider and model in effect, from
 * `GET /api/users/me/inference`.
 *
 * One store for the whole page, the same pattern as `lib/hpc-status.ts`, so
 * the chat-header picker and Settings always show the same answer: whichever
 * of them changes the provider, key, endpoint or model calls
 * `refreshAgentSettings()`, and every subscriber re-renders. The view carries
 * no secrets -- Settings reads key values through the full user view.
 *
 * The provider options and their default models come from the backend's
 * presets; the interface keeps no copy of its own.
 */

export type ProviderOption = {
  id: string;
  name: string;
  /** Only Custom asks for an endpoint. */
  takesUrl: boolean;
  /** Bare model name used when none is chosen; null means one must be. */
  defaultModel: string | null;
};

export type AgentSettings = {
  providers: ProviderOption[];
  provider: string;
  /** The researcher's choice, the installation's configuration, or i2 by default. */
  source: "user" | "config" | "default";
  baseUrl: string;
  /** The model in effect as stored (`openai:<name>`); null when there is none. */
  model: string | null;
  modelIsDefault: boolean;
  /** The provider in effect has a key, from any source. */
  hasCredential: boolean;
  /** Whether the researcher saved a key for each provider. */
  keysSet: Record<string, boolean>;
};

type InferenceView = {
  providers: { id: string; name: string; takes_url: boolean; default_model: string | null }[];
  provider: string;
  source: AgentSettings["source"];
  base_url: string;
  model: string | null;
  model_is_default: boolean;
  has_credential: boolean;
  keys_set: Record<string, boolean>;
};

type Snapshot = {
  /** Null until the first answer. */
  settings: AgentSettings | null;
  /** Why the last request failed, if it did. */
  error: string | null;
  /** Bumped by `requestModelChoice`, so the picker opens asking for a model. */
  modelChoiceRequest: number;
};

const initial = (): Snapshot => ({ settings: null, error: null, modelChoiceRequest: 0 });

let snapshot: Snapshot = initial();
let request = 0;
let loaded = false;
const listeners = new Set<() => void>();

function set(update: Partial<Snapshot>): void {
  snapshot = { ...snapshot, ...update };
  listeners.forEach((cb) => cb());
}

function fromView(v: InferenceView): AgentSettings {
  return {
    providers: v.providers.map((p) => ({
      id: p.id,
      name: p.name,
      takesUrl: p.takes_url,
      defaultModel: p.default_model,
    })),
    provider: v.provider,
    source: v.source,
    baseUrl: v.base_url,
    model: v.model,
    modelIsDefault: v.model_is_default,
    hasCredential: v.has_credential,
    keysSet: v.keys_set,
  };
}

/**
 * Fetch the view again and update every subscriber. Call after saving a
 * provider, key, endpoint or model.
 */
export async function refreshAgentSettings(): Promise<void> {
  const id = ++request;
  loaded = true;
  try {
    const res = await fetch("/api/users/me/inference", {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`Agent settings request failed (${res.status})`);
    const view = (await res.json()) as InferenceView;
    // A slower, older request must not overwrite a newer answer.
    if (id !== request) return;
    set({ settings: fromView(view), error: null });
  } catch (e) {
    if (id !== request) return;
    set({ error: e instanceof Error ? e.message : "Failed to load agent settings" });
  }
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!loaded) void refreshAgentSettings();
  return () => {
    listeners.delete(cb);
  };
}

const getSnapshot = () => snapshot;
const SERVER_SNAPSHOT: Snapshot = initial();
const getServerSnapshot = () => SERVER_SNAPSHOT;

/** Ask the model picker to open and say a model must be chosen first. */
export function requestModelChoice(): void {
  set({ modelChoiceRequest: snapshot.modelChoiceRequest + 1 });
}

/**
 * Whether a send has to wait for a model: the provider in effect has no
 * default and none is chosen. When it does, the picker is asked to open.
 * Before the first answer this says no, and the backend's own refusal is the
 * backstop.
 */
export function holdSendWithoutModel(): boolean {
  const s = snapshot.settings;
  if (!s || s.model !== null) return false;
  requestModelChoice();
  return true;
}

/** The display name of the provider in effect. */
export function providerName(s: AgentSettings): string {
  return s.providers.find((p) => p.id === s.provider)?.name ?? s.provider;
}

/** For tests: forget everything, as a fresh page load would. */
export function resetAgentSettingsForTests(): void {
  request++;
  loaded = false;
  snapshot = initial();
}

export type AgentSettingsView = Snapshot & { refresh: () => Promise<void> };

export function useAgentSettings(): AgentSettingsView {
  const s = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return { ...s, refresh: refreshAgentSettings };
}
