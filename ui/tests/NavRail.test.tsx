import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NavRail } from "@/components/NavRail";

const { useTourMock } = vi.hoisted(() => ({ useTourMock: vi.fn() }));
vi.mock("@/components/TourProvider", () => ({ useTour: useTourMock }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/projects", () => ({ useActiveProject: () => null }));
vi.mock("@/lib/chat-run-status", () => ({ useChatRunStatus: () => ({ summary: { status: null } }) }));
vi.mock("@/lib/settings", () => ({
  useSettings: () => ({ settings: null, loading: true, error: null, refresh: vi.fn() }),
}));
vi.mock("@/components/HpcStatusSection", () => ({ HpcStatusSection: () => null }));
vi.mock("@/components/UserSettingsModal", () => ({ UserSettingsModal: () => null }));

const { useVersionMock } = vi.hoisted(() => ({ useVersionMock: vi.fn() }));
vi.mock("@/lib/version", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/version")>()),
  useVersion: useVersionMock,
}));

function versionInfo(overrides: Record<string, unknown> = {}) {
  return {
    info: {
      version: "0.2.3",
      commit: "abc1234",
      checks: "on",
      checked_at: "2026-11-04T09:00:00Z",
      latest: null,
      notes_url: null,
      data_reset: null,
      ...overrides,
    },
    error: null,
    checking: false,
    checkedAt: 0,
    download: null,
  };
}

const KEY = "vista.navRail.collapsed.v1";

function setTouring(running: boolean) {
  useTourMock.mockReturnValue({ running });
}

beforeEach(() => {
  window.localStorage.setItem(KEY, "true");
  setTouring(false);
  useVersionMock.mockReturnValue(versionInfo());
});

afterEach(() => {
  window.localStorage.clear();
});

describe("NavRail during the tour", () => {
  it("is expanded while the tour runs and collapsed again after, without touching the stored choice", () => {
    const { rerender } = render(<NavRail />);
    const rail = () => screen.getByRole("complementary", { name: "Primary navigation" });
    expect(rail()).toHaveClass("collapsed");

    setTouring(true);
    rerender(<NavRail />);
    expect(rail()).toHaveClass("expanded");
    expect(window.localStorage.getItem(KEY)).toBe("true");

    // The toggle does nothing while the rail is borrowed.
    act(() => screen.getByRole("button", { name: /navigation/i }).click());
    expect(window.localStorage.getItem(KEY)).toBe("true");

    setTouring(false);
    rerender(<NavRail />);
    expect(rail()).toHaveClass("collapsed");
    expect(window.localStorage.getItem(KEY)).toBe("true");
  });
});

describe("NavRail's update marker", () => {
  it("marks the settings button while a newer release is available", () => {
    useVersionMock.mockReturnValue(versionInfo({ latest: "0.2.5" }));
    render(<NavRail />);
    expect(screen.getByTestId("settings-update-marker")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open settings, update available" })).toBeInTheDocument();
  });

  it("shows nothing when VISTA is up to date", () => {
    render(<NavRail />);
    expect(screen.queryByTestId("settings-update-marker")).toBeNull();
    expect(screen.getByRole("button", { name: "Open settings" })).toBeInTheDocument();
  });

  it("shows nothing once checks are turned off, whatever was found before", () => {
    useVersionMock.mockReturnValue(versionInfo({ checks: "off", latest: "0.2.5" }));
    render(<NavRail />);
    expect(screen.queryByTestId("settings-update-marker")).toBeNull();
  });
});
