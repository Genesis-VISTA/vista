"use client";

import { useEffect, useRef, useState } from "react";
import { useActiveProject } from "@/lib/projects";
import {
  useAvailableModels,
  qualifyModelId,
  qualifyModelInput,
  displayModelName,
  type UseAvailableModelsResult,
} from "@/lib/models";
import { providerName, useAgentSettings } from "@/lib/agent-settings";

/**
 * A model menu that chooses for whatever it is given: it shows `value`, offers
 * the provider's models, and reports a choice through `onChange`. It writes
 * nothing itself, so the same menu serves the header (the researcher's model),
 * an open chat (that chat's model) and a Hypothesis Lab role.
 *
 * Follows `ProjectSwitcher`'s pattern (local `open` state, a `pointerdown`
 * listener for click-outside, `Escape` to close, listbox roles) since it sits
 * right beside it.
 *
 * `value` is the model chosen here, or null when this follows the default;
 * `defaultModel` is what null resolves to. The label reads "Default (<model>)"
 * for a followed default, "No model" when there is none, and a chosen model the
 * provider's list leaves out is marked as not listed. The list is fetched again
 * each time the menu opens and whenever the provider, endpoint or key changes.
 *
 * Providers like MAG list many long, similar ids, so the menu opens on a
 * search box (substring match, Enter takes the first match), and every option
 * and the label carry the full id as a tooltip for when it's cut off.
 *
 * `useAvailableModels` only lists the chosen provider's OpenAI-compatible
 * endpoint, so a listed id, and a typed one, always belongs to the `openai`
 * provider. `qualifyModelId` persists that; `displayModelName` hides it again,
 * since `openai:` is an internal routing detail, not something a researcher
 * picked.
 */
export type ModelMenuProps = {
  /** The model chosen here, as stored (`openai:<name>`); null follows the default. */
  value: string | null;
  /** The model `value: null` resolves to; null when there is none. */
  defaultModel: string | null;
  /** Choose a model, or null to follow the default. A rejection is shown in the menu. */
  onChange: (model: string | null) => Promise<void>;
  /** Added to the label while `value` is set, e.g. "this chat". */
  scope?: string;
  /** Offer "Use default (<model>)" while `value` is set. */
  offerDefault?: boolean;
  /** Names the control for assistive technology; "Model" by default. */
  name?: string;
  /** A list shared with the menus beside this one (`useProviderModels`); fetched here otherwise. */
  models?: UseAvailableModelsResult;
};

/**
 * The researcher's provider's model list for the active project, fetched again
 * whenever the provider, endpoint or key changes. Menus that sit together share
 * one (see `RoleModelSelects`); with `enabled` false this fetches nothing.
 */
export function useProviderModels(enabled = true): UseAvailableModelsResult {
  const activeProject = useActiveProject();
  const { settings } = useAgentSettings();
  const sourceKey = settings
    ? `${settings.provider}|${settings.baseUrl}|${settings.hasCredential}`
    : "";
  // Wait for the settings: listing before them would fetch for a source that
  // is about to change.
  return useAvailableModels(
    enabled && settings ? (activeProject?.name ?? null) : null,
    sourceKey,
  );
}

export function ModelMenu({
  value,
  defaultModel,
  onChange,
  scope,
  offerDefault = false,
  name = "Model",
  models,
}: ModelMenuProps) {
  const { settings, modelChoiceRequest } = useAgentSettings();
  const ownModels = useProviderModels(!models);
  const modelsState = models ?? ownModels;
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const typedRef = useRef<HTMLInputElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const seenRequest = useRef(modelChoiceRequest);

  function close() {
    setOpen(false);
    setAsking(false);
    setTyping(false);
    setQuery("");
  }

  function openMenu() {
    setOpen(true);
    void modelsState.refresh();
  }

  // A send held for want of a model opens the picker asking for one.
  useEffect(() => {
    if (modelChoiceRequest === seenRequest.current) return;
    seenRequest.current = modelChoiceRequest;
    setAsking(true);
    openMenu();
    // `openMenu` only reads the latest `modelsState.refresh`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelChoiceRequest]);

  useEffect(() => {
    if (typing) typedRef.current?.focus();
  }, [typing]);

  const searchable = modelsState.status === "ready" && modelsState.models.length > 0;
  useEffect(() => {
    if (open && searchable && !typing) searchRef.current?.focus();
  }, [open, searchable, typing]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) close();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        close();
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  /** A model, or null for the default. */
  async function choose(qualified: string | null) {
    close();
    buttonRef.current?.focus();
    if (qualified === "" || qualified === value) return;
    setSaving(true);
    setSaveError(null);
    try {
      await onChange(qualified);
      setTyped("");
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to update model.");
    } finally {
      setSaving(false);
    }
  }

  // What this resolves to: its own model, else the default it follows.
  const current = value ?? defaultModel;
  const listed =
    modelsState.status === "ready"
      ? modelsState.models.some((m) => qualifyModelId(m.id) === value)
      : true;
  const unlistedBy = settings && value !== null && !listed ? providerName(settings) : null;

  const label = saving
    ? "Saving…"
    : !settings
      ? name
      : value !== null
        ? `${displayModelName(value)}${scope ? ` · ${scope}` : ""}`
        : defaultModel !== null
          ? `Default (${displayModelName(defaultModel)})`
          : "No model";

  const typedHint = "Type its name under “Use another model…” below.";

  const needle = query.trim().toLowerCase();
  const shown =
    modelsState.status === "ready"
      ? [...modelsState.models]
          .sort((a, b) => a.id.localeCompare(b.id))
          .filter((m) => m.id.toLowerCase().includes(needle))
      : [];

  return (
    // The tour's anchor is the whole picker, so the menu it opens is inside it.
    <div className="model-picker" ref={rootRef} data-tour="model:pick">
      <button
        ref={buttonRef}
        type="button"
        className="model-picker-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${name}: ${label}${unlistedBy ? `, not listed by ${unlistedBy}` : ""}. Change model`}
        disabled={saving}
        onClick={() => (open ? close() : openMenu())}
      >
        <span className="model-picker-label" title={label}>
          {label}
        </span>
        {unlistedBy && (
          <span className="model-picker-unlisted">not listed by {unlistedBy}</span>
        )}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="model-picker-menu">
          {asking && (
            <div className="model-picker-hint" data-tone="ask" role="status">
              Choose a model first.
              {settings ? ` ${providerName(settings)} has no default model.` : ""}
            </div>
          )}

          {modelsState.status === "loading" && (
            <div className="model-picker-empty">Loading models&hellip;</div>
          )}

          {modelsState.status === "missing-credential" && (
            <div className="model-picker-hint">{modelsState.detail}</div>
          )}

          {modelsState.status === "error" && (
            <div className="model-picker-hint">
              {modelsState.detail} {typedHint}
            </div>
          )}

          {modelsState.status === "unavailable" && (
            <div className="model-picker-hint">
              This inference endpoint doesn&apos;t report which models it offers.{" "}
              {typedHint}
            </div>
          )}

          {modelsState.status === "ready" && modelsState.models.length === 0 && (
            <div className="model-picker-empty">No models reported.</div>
          )}

          {offerDefault && value !== null && (
            <button
              type="button"
              className="model-picker-option"
              onClick={() => void choose(null)}
            >
              <span className="model-picker-option-id">
                Use default ({defaultModel !== null ? displayModelName(defaultModel) : "no model"})
              </span>
            </button>
          )}

          {searchable && (
            <div className="model-picker-search">
              <input
                ref={searchRef}
                type="search"
                aria-label="Search models"
                placeholder="Search models"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && shown.length > 0) {
                    event.preventDefault();
                    void choose(qualifyModelId(shown[0].id));
                  }
                }}
                spellCheck={false}
                autoComplete="off"
              />
            </div>
          )}

          {searchable && shown.length === 0 && (
            <div className="model-picker-empty">No models match “{query.trim()}”.</div>
          )}

          <div className="model-picker-options" role="listbox" aria-label="Models">
            {shown.map((model) => {
              const isActive = qualifyModelId(model.id) === current;
              return (
                <button
                  key={model.id}
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  className="model-picker-option"
                  data-active={isActive ? "true" : "false"}
                  title={model.id}
                  onClick={() => void choose(qualifyModelId(model.id))}
                >
                  <span className="model-picker-dot" aria-hidden="true" />
                  <span className="model-picker-option-id">{model.id}</span>
                </button>
              );
            })}
          </div>

          <div className="model-picker-separator" aria-hidden="true" />
          {typing ? (
            <form
              className="model-picker-other-form"
              onSubmit={(event) => {
                event.preventDefault();
                void choose(qualifyModelInput(typed));
              }}
            >
              <input
                ref={typedRef}
                aria-label="Model name"
                placeholder="Model name"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
              <button type="submit" disabled={typed.trim() === ""}>
                Use
              </button>
            </form>
          ) : (
            <button
              type="button"
              className="model-picker-option"
              onClick={() => setTyping(true)}
            >
              <span className="model-picker-option-id">Use another model…</span>
            </button>
          )}

          {saveError && <div className="model-picker-hint">{saveError}</div>}
        </div>
      )}
    </div>
  );
}
