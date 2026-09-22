"use client";

import { useEffect, useRef, useState } from "react";
import { useActiveProject } from "@/lib/projects";
import { useAvailableModels, qualifyModelId, displayModelName } from "@/lib/models";
import { fetchCurrentUserWithConfig, updateCurrentUser } from "@/lib/user";

/**
 * The model crumb in the chat header, next to the project switcher.
 *
 * Follows `ProjectSwitcher`'s pattern exactly (local `open` state, a
 * `pointerdown` listener for click-outside, `Escape` to close, listbox
 * roles) since it sits right beside it. Selecting an option writes straight
 * to the same `inference_model` field `UserSettingsModal` already edits --
 * there is no separate "current model" concept to keep in sync, and no new
 * mutation endpoint.
 *
 * `useAvailableModels` only ever lists a project's configured OpenAI-compatible
 * endpoint (`list_models` on the backend gates on
 * `InferenceTarget.uses_configured_endpoint`), so a listed id always belongs
 * to the `openai` provider. `qualifyModelId` persists that association;
 * `displayModelName` hides it again for the button label, since `openai:` is
 * an internal routing detail, not something a researcher picked.
 */
export function ModelPicker() {
  const activeProject = useActiveProject();
  const modelsState = useAvailableModels(activeProject?.name ?? null);
  const [currentModel, setCurrentModel] = useState<string | null>(null);
  const [loadingCurrent, setLoadingCurrent] = useState(true);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  async function loadCurrent() {
    setLoadingCurrent(true);
    try {
      const user = await fetchCurrentUserWithConfig();
      setCurrentModel(user.inference_model);
    } catch {
      // The button falls back to a placeholder label; whatever is actionable
      // is already explained by `modelsState` in the dropdown itself.
      setCurrentModel(null);
    } finally {
      setLoadingCurrent(false);
    }
  }

  useEffect(() => {
    void loadCurrent();
  }, []);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
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

  async function choose(id: string) {
    setOpen(false);
    buttonRef.current?.focus();
    const qualified = qualifyModelId(id);
    if (qualified === currentModel) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateCurrentUser({ inference_model: qualified });
      setCurrentModel(qualified);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to update model.");
    } finally {
      setSaving(false);
    }
  }

  const label = saving
    ? "Saving…"
    : loadingCurrent
      ? "Model"
      : currentModel
        ? displayModelName(currentModel)
        : "Default model";

  return (
    <div className="model-picker" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="model-picker-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Model: ${label}. Change model`}
        disabled={saving}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="model-picker-label">{label}</span>
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
        <div className="model-picker-menu" role="listbox" aria-label="Models">
          {modelsState.status === "loading" && (
            <div className="model-picker-empty">Loading models&hellip;</div>
          )}

          {modelsState.status === "missing-credential" && (
            <div className="model-picker-hint">{modelsState.detail}</div>
          )}

          {modelsState.status === "error" && (
            <div className="model-picker-hint">{modelsState.detail}</div>
          )}

          {modelsState.status === "unavailable" && (
            <div className="model-picker-hint">
              This inference endpoint doesn&apos;t report which models it
              offers. Set one by name in the Model field in Settings (click
              your name in the bottom-left corner).
            </div>
          )}

          {modelsState.status === "ready" && modelsState.models.length === 0 && (
            <div className="model-picker-empty">No models reported.</div>
          )}

          {modelsState.status === "ready" &&
            [...modelsState.models]
              .sort((a, b) => a.id.localeCompare(b.id))
              .map((model) => {
                const isActive = qualifyModelId(model.id) === currentModel;
                return (
                  <button
                    key={model.id}
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    className="model-picker-option"
                    data-active={isActive ? "true" : "false"}
                    onClick={() => void choose(model.id)}
                  >
                    <span className="model-picker-dot" aria-hidden="true" />
                    <span className="model-picker-option-id">{model.id}</span>
                  </button>
                );
              })}

          {saveError && <div className="model-picker-hint">{saveError}</div>}
        </div>
      )}
    </div>
  );
}

export default ModelPicker;
