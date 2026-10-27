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

const KEY = "vista.navRail.collapsed.v1";

function setTouring(running: boolean) {
  useTourMock.mockReturnValue({ running });
}

beforeEach(() => {
  window.localStorage.setItem(KEY, "true");
  setTouring(false);
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
