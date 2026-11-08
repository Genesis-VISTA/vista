import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { closeSettings, openSettings, useSettingsModal } from "@/lib/settings-modal";

afterEach(() => act(() => closeSettings()));

describe("settings modal store", () => {
  it("starts closed", () => {
    const { result } = renderHook(() => useSettingsModal());
    expect(result.current).toEqual({ open: false, section: undefined });
  });

  it("opens with no section, as the rail button does", () => {
    const { result } = renderHook(() => useSettingsModal());
    act(() => openSettings());
    expect(result.current).toEqual({ open: true, section: undefined });
  });

  it("opens at a named section and closes again", () => {
    const { result } = renderHook(() => useSettingsModal());
    act(() => openSettings("agent"));
    expect(result.current).toEqual({ open: true, section: "agent" });
    act(() => closeSettings());
    expect(result.current).toEqual({ open: false, section: undefined });
  });

  it("forgets the last section when opened plainly", () => {
    const { result } = renderHook(() => useSettingsModal());
    act(() => openSettings("frontier"));
    act(() => closeSettings());
    act(() => openSettings());
    expect(result.current.section).toBeUndefined();
  });

  it("tells every subscriber", () => {
    const a = renderHook(() => useSettingsModal());
    const b = renderHook(() => useSettingsModal());
    act(() => openSettings("odo"));
    expect(a.result.current.section).toBe("odo");
    expect(b.result.current.section).toBe("odo");
  });
});
