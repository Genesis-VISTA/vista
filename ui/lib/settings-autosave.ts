"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Autosave for the settings modal: when each field saves, one save at a
 * time per field, and the mark each field shows for its own save.
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
 *
 * Each field's mark: none while a save is in flight (a one-field save to a
 * local backend finishes within a frame, so anything shown would only
 * flicker), `"slow"` once it has taken `SLOW_MS`, `"saved"` for `SAVED_MS`
 * after it succeeds or until the field is edited again, and `"failed"` until a
 * later save of the field succeeds.
 */

export const DEBOUNCE_MS = 800;
/** A save in flight this long shows that it is still saving. */
export const SLOW_MS = 1000;
/** How long a field's check mark shows before it fades. */
export const SAVED_MS = 2000;

export type SaveHow = "debounce" | "hold" | "now";

export type FieldMark = "slow" | "saved" | "failed";

export type SaveOutcome = { ok: true } | { ok: false; message: string };

type FieldState = {
  /** The last value saved, or the loaded one; never re-sent. */
  saved: unknown;
  /** A value not yet sent. `undefined` when there is none. */
  pending: unknown;
  timer: ReturnType<typeof setTimeout> | null;
  /** Shows "slow" if the save in flight is still running. */
  slowTimer: ReturnType<typeof setTimeout> | null;
  /** Clears "saved" once it has shown long enough. */
  savedTimer: ReturnType<typeof setTimeout> | null;
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
  /** Each field's mark; a field with none is absent. */
  marks: Record<string, FieldMark>;
  /** Why each failed field's last save failed. */
  errors: Record<string, string>;
};

export function useSettingsAutosave({
  save,
  onSettled,
  debounceMs = DEBOUNCE_MS,
}: {
  /** Sends one field. Rejects with an Error whose message says why. */
  save: (field: string, value: unknown) => Promise<void>;
  /** After each save, e.g. to announce it. */
  onSettled?: (field: string, outcome: SaveOutcome) => void;
  debounceMs?: number;
}): SettingsAutosave {
  const fields = useRef(new Map<string, FieldState>());
  const saveRef = useRef(save);
  const settledRef = useRef(onSettled);
  useEffect(() => {
    saveRef.current = save;
    settledRef.current = onSettled;
  }, [save, onSettled]);

  const [marks, setMarks] = useState<Record<string, FieldMark>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  const state = useCallback((field: string): FieldState => {
    let s = fields.current.get(field);
    if (!s) {
      s = { saved: undefined, pending: undefined, timer: null, slowTimer: null, savedTimer: null, running: null };
      fields.current.set(field, s);
    }
    return s;
  }, []);

  /** Set a field's mark, or clear it; `only` clears it just when it is that mark. */
  const mark = useCallback((field: string, next: FieldMark | null, only?: FieldMark) => {
    setMarks((prev) => {
      if (only && prev[field] !== only) return prev;
      if (next === null) {
        if (!(field in prev)) return prev;
        const rest = { ...prev };
        delete rest[field];
        return rest;
      }
      return prev[field] === next ? prev : { ...prev, [field]: next };
    });
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
        mark(field, null, "failed");
        return Promise.resolve();
      }

      if (s.savedTimer) clearTimeout(s.savedTimer);
      s.slowTimer = setTimeout(() => mark(field, "slow"), SLOW_MS);
      s.running = (async () => {
        let outcome: SaveOutcome;
        try {
          await saveRef.current(field, value);
          s.saved = value;
          clearError(field);
          outcome = { ok: true };
        } catch (e) {
          const message = e instanceof Error && e.message ? e.message : "Couldn't save this.";
          setErrors((prev) => ({ ...prev, [field]: message }));
          outcome = { ok: false, message };
        } finally {
          if (s.slowTimer) clearTimeout(s.slowTimer);
          s.slowTimer = null;
          s.running = null;
        }
        if (outcome.ok) {
          mark(field, "saved");
          s.savedTimer = setTimeout(() => mark(field, null, "saved"), SAVED_MS);
        } else {
          mark(field, "failed");
        }
        settledRef.current?.(field, outcome);
        // Edited while this was in flight, and not still waiting on its own
        // pause: send the newer value now.
        if (s.pending !== undefined && !s.timer) await run(field);
      })();
      return s.running;
    },
    [clearError, mark, state],
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
      // The check was for the value just replaced.
      mark(field, null, "saved");
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
    [debounceMs, mark, run, state],
  );

  const commit = useCallback((field: string) => void run(field), [run]);

  const flush = useCallback(async () => {
    const all = [...fields.current.keys()].map((field) => run(field));
    await Promise.all(all);
  }, [run]);

  return useMemo(
    () => ({ seed, edit, commit, flush, marks, errors }),
    [seed, edit, commit, flush, marks, errors],
  );
}

/** Values are strings, nulls, string lists: compare them by content. */
function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}
