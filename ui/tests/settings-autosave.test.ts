import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEBOUNCE_MS, SAVED_MS, useSettingsAutosave } from "@/lib/settings-autosave";

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
    expect(result.current.status).toBe("saving");

    await act(async () => release());
    await settle();
    expect(calls.map((c) => c.value)).toEqual(["/one", "/three"]);
    await act(async () => release());
    await settle();
    expect(result.current.status).toBe("saved");
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

describe("useSettingsAutosave status", () => {
  it("shows saving, then saved, then fades", async () => {
    let release: () => void = () => {};
    save = () => new Promise<void>((resolve) => (release = resolve));
    const { result } = setup();
    act(() => result.current.edit("dir", "/a", "now"));
    expect(result.current.status).toBe("saving");
    await act(async () => release());
    await settle();
    expect(result.current.status).toBe("saved");
    act(() => vi.advanceTimersByTime(SAVED_MS + 10));
    expect(result.current.status).toBe("idle");
  });

  it("reports a failure with its reason, and clears it on a successful retry", async () => {
    save = async () => {
      throw new Error("Remote directory must be absolute.");
    };
    const { result } = setup();
    await act(async () => result.current.edit("dir", "relative", "now"));
    await settle();
    expect(result.current.status).toBe("failed");
    expect(result.current.errors).toEqual({ dir: "Remote directory must be absolute." });

    save = async (field, value) => {
      calls.push({ field, value });
    };
    await act(async () => result.current.edit("dir", "/absolute", "now"));
    await settle();
    expect(result.current.errors).toEqual({});
    expect(result.current.status).toBe("saved");
  });

  it("stays failed while another field saves fine", async () => {
    save = async (field) => {
      if (field === "dir") throw new Error("nope");
    };
    const { result } = setup();
    await act(async () => result.current.edit("dir", "x", "now"));
    await act(async () => result.current.edit("token", "t", "now"));
    await settle();
    expect(result.current.status).toBe("failed");
    expect(Object.keys(result.current.errors)).toEqual(["dir"]);
  });
});
