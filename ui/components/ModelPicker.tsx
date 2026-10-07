"use client";

import { useEffect, useRef, useState } from "react";
import { useActiveProject } from "@/lib/projects";
import {
  useAvailableModels,
  qualifyModelId,
  qualifyModelInput,
  displayModelName,
} from "@/lib/models";
import { updateCurrentUser } from "@/lib/user";
import { providerName, refreshAgentSettings, useAgentSettings } from "@/lib/agent-settings";

/**
 * The model crumb in the chat header, next to the project switcher, and the
 * one place a researcher chooses a model.
 *
 * Follows `ProjectSwitcher`'s pattern (local `open` state, a `pointerdown`
 * listener for click-outside, `Escape` to close, listbox roles) since it sits
 * right beside it. Selecting a model writes `inference_model` and refreshes
 * the shared agent-settings store, so the label and Settings never disagree.
 *
 * The label reads the store rather than a copy of its own: "Default (<model>)"
 * when the provider's default is in effect, "No model" when the provider has
 * none, and a chosen model the provider's list leaves out is marked as not
 * listed. The list is fetched again each time the menu opens and whenever the
 * provider, endpoint or key changes.
 *
 * `useAvailableModels` only lists the chosen provider's OpenAI-compatible
 * endpoint, so a listed id, and a typed one, always belongs to the `openai`
 * provider. `qualifyModelId` persists that; `displayModelName` hides it again,
 * since `openai:` is an internal routing detail, not something a researcher
 * picked.
 */
export function ModelPicker() {
  const activeProject = useActiveProject();
  const { settings, modelChoiceRequest } = useAgentSettings();
  const sourceKey = settings
    ? `${settings.provider}|${settings.baseUrl}|${settings.hasCredential}`
    : "";
  // Wait for the settings: listing before them would fetch for a source that
  // is about to change.
  const modelsState = useAvailableModels(
    settings ? (activeProject?.name ?? null) : null,
    sourceKey,
  );
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const typedRef = useRef<HTMLInputElement | null>(null);
  const seenRequest = useRef(modelChoiceRequest);

  function close() {
    setOpen(false);
    setAsking(false);
    setTyping(false);
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

  async function choose(qualified: string) {
    close();
    buttonRef.current?.focus();
    if (!qualified) return;
    if (settings && qualified === settings.model && !settings.modelIsDefault) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateCurrentUser({ inference_model: qualified });
      setTyped("");
      await refreshAgentSettings();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to update model.");
    } finally {
      setSaving(false);
    }
  }

  const current = settings?.model ?? null;
  const listed =
    modelsState.status === "ready"
      ? modelsState.models.some((m) => qualifyModelId(m.id) === current)
      : true;
  const unlistedBy =
    settings && current && !settings.modelIsDefault && !listed ? providerName(settings) : null;

  const label = saving
    ? "Saving…"
    : !settings
      ? "Model"
      : current === null
        ? "No model"
        : settings.modelIsDefault
          ? `Default (${displayModelName(current)})`
          : displayModelName(current);

  const typedHint = "Type its name under “Use another model…” below.";

  return (
    <div className="model-picker" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="model-picker-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Model: ${label}${unlistedBy ? `, not listed by ${unlistedBy}` : ""}. Change model`}
        disabled={saving}
        onClick={() => (open ? close() : openMenu())}
      >
        <span className="model-picker-label">{label}</span>
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

          <div className="model-picker-options" role="listbox" aria-label="Models">
            {modelsState.status === "ready" &&
              [...modelsState.models]
                .sort((a, b) => a.id.localeCompare(b.id))
                .map((model) => {
                  const isActive = qualifyModelId(model.id) === current;
                  return (
                    <button
                      key={model.id}
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      className="model-picker-option"
                      data-active={isActive ? "true" : "false"}
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

export default ModelPicker;
