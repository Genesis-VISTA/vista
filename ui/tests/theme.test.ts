import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from "@/lib/theme-init";
import { setThemeChoice, useTheme } from "@/lib/theme";

/** jsdom has no matchMedia; this one lets a test flip the OS appearance. */
function mockSystemDark(initial: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    matches: initial,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return {
    set(dark: boolean) {
      query.matches = dark;
      listeners.forEach((cb) => cb());
    },
  };
}

const root = () => document.documentElement;

beforeEach(() => {
  window.localStorage.clear();
  root().removeAttribute("data-theme");
});

afterEach(() => {
  vi.unstubAllGlobals();
  // Leaves the module's in-tab fallback at its default for the next test.
  setThemeChoice("system");
});

describe("useTheme", () => {
  it("follows the OS when nothing is stored, including live changes", () => {
    const os = mockSystemDark(true);
    const { result } = renderHook(() => useTheme());

    expect(result.current.choice).toBe("system");
    expect(result.current.resolved).toBe("dark");
    expect(root().hasAttribute("data-theme")).toBe(false);

    act(() => os.set(false));
    expect(result.current.resolved).toBe("light");
  });

  it("forces dark on a light OS, and stores it", () => {
    const os = mockSystemDark(false);
    const { result } = renderHook(() => useTheme());

    act(() => result.current.setChoice("dark"));

    expect(result.current.choice).toBe("dark");
    expect(result.current.resolved).toBe("dark");
    expect(root().getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    act(() => os.set(false));
    expect(result.current.resolved).toBe("dark");
  });

  it("returns to the OS on System, clearing the attribute and the stored value", () => {
    mockSystemDark(false);
    const { result } = renderHook(() => useTheme());

    act(() => result.current.setChoice("dark"));
    act(() => result.current.setChoice("system"));

    expect(result.current.choice).toBe("system");
    expect(result.current.resolved).toBe("light");
    expect(root().hasAttribute("data-theme")).toBe(false);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("picks up a choice made in another tab", () => {
    mockSystemDark(false);
    const { result } = renderHook(() => useTheme());

    act(() => {
      window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
      window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "dark" }));
    });

    expect(result.current.choice).toBe("dark");
  });

  it("keeps an in-tab choice when the browser refuses storage", () => {
    mockSystemDark(false);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    const { result } = renderHook(() => useTheme());

    expect(result.current.choice).toBe("system");
    act(() => result.current.setChoice("dark"));

    expect(result.current.choice).toBe("dark");
    expect(root().getAttribute("data-theme")).toBe("dark");
  });

  it("shares one choice between components in the same tab", () => {
    mockSystemDark(false);
    const a = renderHook(() => useTheme());
    const b = renderHook(() => useTheme());

    act(() => a.result.current.setChoice("light"));

    expect(b.result.current.choice).toBe("light");
  });
});

describe("THEME_INIT_SCRIPT", () => {
  const run = () => new Function(THEME_INIT_SCRIPT)();

  it("applies a stored choice before React runs", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    run();
    expect(root().getAttribute("data-theme")).toBe("dark");
  });

  it("leaves the attribute off for System or an unrecognised value", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    run();
    expect(root().hasAttribute("data-theme")).toBe(false);
  });

  it("does not throw when storage is refused", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(run).not.toThrow();
    expect(root().hasAttribute("data-theme")).toBe(false);
  });

  it("applies a choice made in another tab without any React mounted", () => {
    run();
    window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "light" }));
    expect(root().getAttribute("data-theme")).toBe("light");

    window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: null }));
    expect(root().hasAttribute("data-theme")).toBe(false);
  });
});
