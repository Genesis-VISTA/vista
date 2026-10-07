"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Autosave for the settings modal: when each field saves, one save at a
 * time per field, and what the modal's single indicator shows.
 *
 * The modal owns the values; this only decides when a value is sent. Each
 * field is told how it changed:
 * - `"debounce"`: typed text, saved once the researcher pauses (`DEBOUNCE_MS`);
 * - `"hold"`: a secret typed by hand, kept until `commit` (blur) or `flush`,
 *   so a half-typed token is never sent;
 * - `"now"`: a paste, a switch or a choice, saved at once.
 *
 * Saves of one field are serialised and coalesced: a value edited while its
 * last save is in flight waits for it, and only the newest is sent, so a slow
 * response can never land after a newer one. Different fields save in
 * parallel. `flush` sends everything pending, for closing the modal.
 */

export const DEBOUNCE_MS = 800;
/** How long "Saved" shows before the indicator fades. */
export const SAVED_MS = 3000;

export type SaveHow = "debounce" | "hold" | "now";

export type SaveStatus = "idle" | "saving" | "saved" | "failed";

type FieldState = {
  /** The last value saved, or the loaded one; never re-sent. */
  saved: unknown;
  /** A value not yet sent. `undefined` when there is none. */
  pending: unknown;
  timer: ReturnType<typeof setTimeout> | null;
  /** The save in flight for this field, if any. */
  running: Promise<void> | null;
};

export type SettingsAutosave = {
  /** The loaded values, so an unchanged field is never saved. */
  seed: (values: Record<string, unknown>) => void;
  edit: (field: string, value: unknown, how: SaveHow) => void;
  /** Send this field's pending value now, e.g. when it loses focus. */
  commit: (field: string) => void;
  /** Send every pending value now and wait for every save in flight. */
  flush: () => Promise<void>;
  status: SaveStatus;
  /** Why each failed field's last save failed. */
  errors: Record<string, string>;
};

export function useSettingsAutosave({
  save,
  debounceMs = DEBOUNCE_MS,
}: {
  /** Sends one field. Rejects with an Error whose message says why. */
  save: (field: string, value: unknown) => Promise<void>;
  debounceMs?: number;
}): SettingsAutosave {
  const fields = useRef(new Map<string, FieldState>());
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);

  const [inFlight, setInFlight] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [, setFadeTick] = useState(0);

  const state = useCallback((field: string): FieldState => {
    let s = fields.current.get(field);
    if (!s) {
      s = { saved: undefined, pending: undefined, timer: null, running: null };
      fields.current.set(field, s);
    }
    return s;
  }, []);

  const clearError = useCallback((field: string) => {
    setErrors((prev) => {
      if (!(field in prev)) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }, []);

  const run = useCallback(
    (field: string): Promise<void> => {
      const s = state(field);
      if (s.timer) {
        clearTimeout(s.timer);
        s.timer = null;
      }
      // One at a time: the save in flight sends whatever is newest next.
      if (s.running) return s.running;
      if (s.pending === undefined) return Promise.resolve();
      const value = s.pending;
      s.pending = undefined;
      if (same(value, s.saved)) {
        // Put back to what is stored: nothing left to fail.
        clearError(field);
        return Promise.resolve();
      }

      setInFlight((n) => n + 1);
      s.running = (async () => {
        try {
          await saveRef.current(field, value);
          s.saved = value;
          clearError(field);
          setSavedAt(Date.now());
        } catch (e) {
          const message = e instanceof Error && e.message ? e.message : "Couldn't save this.";
          setErrors((prev) => ({ ...prev, [field]: message }));
        } finally {
          s.running = null;
          setInFlight((n) => n - 1);
        }
        // Edited while this was in flight, and not still waiting on its own
        // pause: send the newer value now.
        if (s.pending !== undefined && !s.timer) await run(field);
      })();
      return s.running;
    },
    [clearError, state],
  );

  const seed = useCallback(
    (values: Record<string, unknown>) => {
      for (const [field, value] of Object.entries(values)) state(field).saved = value;
    },
    [state],
  );

  const edit = useCallback(
    (field: string, value: unknown, how: SaveHow) => {
      const s = state(field);
      s.pending = value;
      if (s.timer) {
        clearTimeout(s.timer);
        s.timer = null;
      }
      if (how === "now") void run(field);
      else if (how === "debounce") {
        s.timer = setTimeout(() => {
          s.timer = null;
          void run(field);
        }, debounceMs);
      }
    },
    [debounceMs, run, state],
  );

  const commit = useCallback((field: string) => void run(field), [run]);

  const flush = useCallback(async () => {
    const all = [...fields.current.keys()].map((field) => run(field));
    await Promise.all(all);
  }, [run]);

  // Fade "Saved" a few seconds after the last success.
  useEffect(() => {
    if (savedAt === null) return;
    const left = savedAt + SAVED_MS - Date.now();
    if (left <= 0) return;
    const t = setTimeout(() => setFadeTick((n) => n + 1), left);
    return () => clearTimeout(t);
  }, [savedAt]);

  const hasErrors = Object.keys(errors).length > 0;
  const status: SaveStatus =
    inFlight > 0
      ? "saving"
      : hasErrors
        ? "failed"
        : savedAt !== null && Date.now() - savedAt < SAVED_MS
          ? "saved"
          : "idle";

  return useMemo(
    () => ({ seed, edit, commit, flush, status, errors }),
    [seed, edit, commit, flush, status, errors],
  );
}

/** Values are strings, nulls, string lists: compare them by content. */
function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}
