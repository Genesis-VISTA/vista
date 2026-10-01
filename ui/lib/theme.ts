import { useCallback, useSyncExternalStore } from "react";
import { THEME_STORAGE_KEY } from "./theme-init";

/**
 * Light / dark appearance.
 *
 * The palette lives entirely in globals.css: with no `data-theme` on <html>
 * the OS decides through `prefers-color-scheme`, and `data-theme="light"` or
 * `"dark"` overrides it. So "System" is the absence of the attribute, and this
 * module only ever stores, sets or removes it.
 *
 * The choice is per machine, in localStorage, never on the user row: it has to
 * be readable before the first paint, which no API call can be.
 */

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export { THEME_STORAGE_KEY };

const DARK_QUERY = "(prefers-color-scheme: dark)";

function parseChoice(value: string | null | undefined): ThemeChoice {
  return value === "light" || value === "dark" ? value : "system";
}

/** Where the choice lives when the browser refuses storage: this tab, until reload. */
let sessionChoice: ThemeChoice = "system";
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((cb) => cb());
}

function readChoice(): ThemeChoice {
  try {
    return parseChoice(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return sessionChoice;
  }
}

function applyChoice(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

function darkQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia(DARK_QUERY) : null;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  // Another tab changed the choice; THEME_INIT_SCRIPT has already applied it.
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_STORAGE_KEY) cb();
  };
  window.addEventListener("storage", onStorage);
  const query = darkQuery();
  query?.addEventListener("change", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", onStorage);
    query?.removeEventListener("change", cb);
  };
}

// The server cannot know either value. Both server snapshots describe the
// default, so the first client render matches the HTML and the real values
// arrive right after hydration.
const serverChoice = (): ThemeChoice => "system";
const systemIsDark = () => darkQuery()?.matches ?? false;
const serverSystemIsDark = () => false;

/** Records a choice in storage (or this tab, if storage is refused) and applies it. */
export function setThemeChoice(choice: ThemeChoice) {
  sessionChoice = choice;
  try {
    if (choice === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Storage refused: sessionChoice carries it until the page reloads.
  }
  applyChoice(choice);
  notify();
}

export function useTheme(): {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setChoice: (choice: ThemeChoice) => void;
} {
  const choice = useSyncExternalStore(subscribe, readChoice, serverChoice);
  const dark = useSyncExternalStore(subscribe, systemIsDark, serverSystemIsDark);
  const setChoice = useCallback((next: ThemeChoice) => setThemeChoice(next), []);
  const resolved: ResolvedTheme = choice === "system" ? (dark ? "dark" : "light") : choice;
  return { choice, resolved, setChoice };
}
