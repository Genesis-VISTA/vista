import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEBOUNCE_MS, SAVED_MS, SLOW_MS, useSettingsAutosave } from "@/lib/settings-autosave";

type Call = { field: string; value: unknown };

let calls: Call[];
let save: (field: string, value: unknown) => Promise<void>;

beforeEach(() => {
  vi.useFakeTimers();
  calls = [];
  save = async (field, value) => {
    calls.push({ field, value });
  };
});

afterEach(() => {
  vi.useRealTimers();
});

function setup() {
  const hook = renderHook(() => useSettingsAutosave({ save: (f, v) => save(f, v) }));
  act(() => hook.result.current.seed({ dir: null, token: null, shown: [] }));
  return hook;
}

/** Let resolved saves finish and React apply their state. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useSettingsAutosave triggers", () => {
  it("saves typed text once, after a pause", async () => {
    const { result } = setup();
    act(() => result.current.edit("dir", "/a", "debounce"));
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS - 100));
    act(() => result.current.edit("dir", "/ab", "debounce"));
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS - 100));
    expect(calls).toEqual([]);
    await act(async () => vi.advanceTimersByTime(100));
    await settle();
    expect(calls).toEqual([{ field: "dir", value: "/ab" }]);
  });

  it("holds a hand-typed secret until it is committed", async () => {
    const { result } = setup();
    act(() => result.current.edit("token", "s", "hold"));
    act(() => result.current.edit("token", "se", "hold"));
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS * 10));
    expect(calls).toEqual([]);
    await act(async () => result.current.commit("token"));
    await settle();
    expect(calls).toEqual([{ field: "token", value: "se" }]);
  });

  it("saves a paste, a switch or a choice at once", async () => {
    const { result } = setup();
    await act(async () => result.current.edit("shown", ["odo"], "now"));
    await settle();
    expect(calls).toEqual([{ field: "shown", value: ["odo"] }]);
  });

  it("does not save a value equal to the stored one", async () => {
    const { result } = setup();
    await act(async () => result.current.edit("dir", null, "now"));
    await act(async () => result.current.commit("dir"));
    await settle();
    expect(calls).toEqual([]);
  });

  it("flushes pending text and held secrets on close", async () => {
    const { result } = setup();
    act(() => result.current.edit("dir", "/a", "debounce"));
    act(() => result.current.edit("token", "tok", "hold"));
    await act(async () => result.current.flush());
    expect(calls).toEqual(
      expect.arrayContaining([
        { field: "dir", value: "/a" },
        { field: "token", value: "tok" },
      ]),
    );
    expect(calls).toHaveLength(2);
    // Nothing left to send once the pause would have ended.
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS));
    expect(calls).toHaveLength(2);
  });
});

describe("useSettingsAutosave ordering", () => {
  it("serialises one field's saves and sends only the newest after a slow one", async () => {
    let release: () => void = () => {};
    save = (field, value) => {
      calls.push({ field, value });
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    };
    const { result } = setup();
    act(() => result.current.edit("dir", "/one", "now"));
    act(() => result.current.edit("dir", "/two", "now"));
    act(() => result.current.edit("dir", "/three", "now"));
    expect(calls).toEqual([{ field: "dir", value: "/one" }]);

    await act(async () => release());
    await settle();
    expect(calls.map((c) => c.value)).toEqual(["/one", "/three"]);
    await act(async () => release());
    await settle();
    expect(result.current.marks).toEqual({ dir: "saved" });
  });

  it("saves different fields in parallel", async () => {
    save = (field, value) => {
      calls.push({ field, value });
      return new Promise<void>(() => {});
    };
    const { result } = setup();
    act(() => result.current.edit("dir", "/a", "now"));
    act(() => result.current.edit("token", "t", "now"));
    expect(calls.map((c) => c.field)).toEqual(["dir", "token"]);
  });
});

describe("useSettingsAutosave marks", () => {
  it("shows nothing while a save is in flight, then a check that fades", async () => {
    let release: () => void = () => {};
    save = () => new Promise<void>((resolve) => (release = resolve));
    const { result } = setup();
    act(() => result.current.edit("dir", "/a", "now"));
    expect(result.current.marks).toEqual({});
    await act(async () => release());
    await settle();
    expect(result.current.marks).toEqual({ dir: "saved" });
    act(() => vi.advanceTimersByTime(SAVED_MS + 10));
    expect(result.current.marks).toEqual({});
  });

  it("marks only the field that saved", async () => {
    const { result } = setup();
    await act(async () => result.current.edit("dir", "/a", "now"));
    await settle();
    expect(result.current.marks).toEqual({ dir: "saved" });
  });

  it("shows a slow save as still saving, until it completes", async () => {
    let release: () => void = () => {};
    save = () => new Promise<void>((resolve) => (release = resolve));
    const { result } = setup();
    act(() => result.current.edit("dir", "/a", "now"));
    act(() => vi.advanceTimersByTime(SLOW_MS - 10));
    expect(result.current.marks).toEqual({});
    act(() => vi.advanceTimersByTime(20));
    expect(result.current.marks).toEqual({ dir: "slow" });
    await act(async () => release());
    await settle();
    expect(result.current.marks).toEqual({ dir: "saved" });
  });

  it("drops the check as soon as the field is edited again", async () => {
    const { result } = setup();
    await act(async () => result.current.edit("dir", "/a", "now"));
    await settle();
    expect(result.current.marks.dir).toBe("saved");
    act(() => result.current.edit("dir", "/ab", "debounce"));
    expect(result.current.marks).toEqual({});
  });

  it("marks a failure with its reason, and a successful retry replaces it with a check", async () => {
    const settled: Array<[string, unknown]> = [];
    save = async () => {
      throw new Error("Remote directory must be absolute.");
    };
    const { result } = renderHook(() =>
      useSettingsAutosave({ save: (f, v) => save(f, v), onSettled: (f, o) => settled.push([f, o]) }),
    );
    await act(async () => result.current.edit("dir", "relative", "now"));
    await settle();
    expect(result.current.marks).toEqual({ dir: "failed" });
    expect(result.current.errors).toEqual({ dir: "Remote directory must be absolute." });
    // Editing keeps the cross: it goes only when a save succeeds.
    act(() => result.current.edit("dir", "/abs", "hold"));
    expect(result.current.marks).toEqual({ dir: "failed" });

    save = async (field, value) => {
      calls.push({ field, value });
    };
    await act(async () => result.current.commit("dir"));
    await settle();
    expect(result.current.errors).toEqual({});
    expect(result.current.marks).toEqual({ dir: "saved" });
    expect(settled).toEqual([
      ["dir", { ok: false, message: "Remote directory must be absolute." }],
      ["dir", { ok: true }],
    ]);
  });

  it("keeps one field's failure while another saves fine", async () => {
    save = async (field) => {
      if (field === "dir") throw new Error("nope");
    };
    const { result } = setup();
    await act(async () => result.current.edit("dir", "x", "now"));
    await act(async () => result.current.edit("token", "t", "now"));
    await settle();
    expect(result.current.marks).toEqual({ dir: "failed", token: "saved" });
    expect(Object.keys(result.current.errors)).toEqual(["dir"]);
  });
});
