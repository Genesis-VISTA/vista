import { useState } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TARGET_TIMEOUT_MS, TourProvider, useTour, useTourEvent } from "@/components/TourProvider";
import type { AgentSettings } from "@/lib/agent-settings";
import type { LocalSettings, OnboardingTourState } from "@/lib/settings";

const { highlightMock, destroyMock, refreshMock, driverMock } = vi.hoisted(() => {
  const highlightMock = vi.fn();
  const destroyMock = vi.fn();
  const refreshMock = vi.fn();
  return { highlightMock, destroyMock, refreshMock, driverMock: vi.fn() };
});
vi.mock("driver.js", () => ({ driver: driverMock }));
vi.mock("driver.js/dist/driver.css", () => ({}));

const { useSettingsMock, updateSettingsMock } = vi.hoisted(() => ({
  useSettingsMock: vi.fn(),
  updateSettingsMock: vi.fn(),
}));
vi.mock("@/lib/settings", () => ({
  useSettings: useSettingsMock,
  updateSettings: updateSettingsMock,
}));

const { useAgentSettingsMock } = vi.hoisted(() => ({ useAgentSettingsMock: vi.fn() }));
vi.mock("@/lib/agent-settings", () => ({ useAgentSettings: useAgentSettingsMock }));

const { useActiveProjectNameMock } = vi.hoisted(() => ({ useActiveProjectNameMock: vi.fn() }));
vi.mock("@/lib/projects", () => ({ useActiveProjectName: useActiveProjectNameMock }));

const { push, nav } = vi.hoisted(() => ({ push: vi.fn(), nav: { pathname: "/", router: { push: vi.fn() } } }));
nav.router.push = push;
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => nav.pathname,
}));

const { openSettingsMock, closeSettingsMock } = vi.hoisted(() => ({
  openSettingsMock: vi.fn(),
  closeSettingsMock: vi.fn(),
}));
vi.mock("@/lib/settings-modal", () => ({
  openSettings: openSettingsMock,
  closeSettings: closeSettingsMock,
}));

function localSettings(over: Partial<LocalSettings> = {}): LocalSettings {
  return {
    install_id: "x",
    display_name: null,
    collaboration_server_url: null,
    onboarding_tour: null,
    onboarding_tour_enabled: true,
    ...over,
  } as LocalSettings;
}

function agentSettings(over: Partial<AgentSettings> = {}): AgentSettings {
  return {
    providers: [
      { id: "i2", name: "AmSC i2", takesUrl: false, defaultModel: "claude-sonnet" },
    ],
    provider: "i2",
    source: "default",
    baseUrl: "",
    model: null,
    modelIsDefault: true,
    hasCredential: false,
    keysSet: {},
    ...over,
  };
}

function setSettings(over: Partial<LocalSettings> = {}) {
  useSettingsMock.mockReturnValue({
    settings: localSettings(over),
    loading: false,
    error: null,
    refresh: vi.fn(),
  });
}

/** What highlight was last asked to show. */
type Step = {
  element: Element | undefined;
  popover: { title: string; onPopoverRender: (p: unknown) => void };
};
function lastStep(): Step {
  return highlightMock.mock.calls.at(-1)![0] as Step;
}

/** Plays driver.js: builds an empty popover, hands it to the step's render hook. */
function openPopover(): void {
  const parts = Object.fromEntries(
    ["wrapper", "title", "description", "footer"].map((name) => [
      name,
      document.body.appendChild(document.createElement("div")),
    ]),
  );
  // driver.js hides the footer inline when showButtons is empty.
  parts.footer.style.display = "none";
  lastStep().popover.onPopoverRender(parts);
}

/** Passes the coachmark showing with its Next, as the researcher does. */
function pressNext(): void {
  openPopover();
  // Earlier popovers stay in the document, so press the newest one's.
  act(() => screen.getAllByRole("button", { name: "Next" }).at(-1)!.click());
}

/**
 * With a credential, the model step's Next coachmarks run overview, composer,
 * panel, picker, and the researcher is at the picker's pick. Mounts the anchors.
 */
function walkToPick(): void {
  for (let i = 0; i < 4; i++) pressNext();
}

/** The elements the model step's chat coachmarks point at. */
function mountChatAnchors(): void {
  document.body.appendChild(document.createElement("header")).className = "page-topbar";
  anchor("first-question:send");
  anchor("model:panel");
  anchor("model:pick");
}

function tourWrittenAs(state: OnboardingTourState) {
  return { onboarding_tour: state };
}

beforeEach(() => {
  highlightMock.mockReset();
  destroyMock.mockReset();
  driverMock.mockReset();
  refreshMock.mockReset();
  driverMock.mockImplementation((config: Record<string, unknown>) => ({
    highlight: highlightMock,
    destroy: destroyMock,
    refresh: refreshMock,
    getConfig: () => config,
  }));
  push.mockReset();
  openSettingsMock.mockReset();
  closeSettingsMock.mockReset();
  nav.pathname = "/";
  updateSettingsMock.mockReset();
  updateSettingsMock.mockResolvedValue(localSettings());
  useAgentSettingsMock.mockReturnValue({ settings: agentSettings(), error: null });
  useActiveProjectNameMock.mockReturnValue(null);
  setSettings();
});

afterEach(() => {
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe("TourProvider", () => {
  it("shows the welcome centred when nothing is stored", () => {
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).toHaveBeenCalledTimes(1);
    expect(lastStep().element).toBeUndefined();
    expect(lastStep().popover.title).toBe("Welcome to VISTA");
    openPopover();
    const start = screen.getByRole("button", { name: "Start the tour" });
    expect(start.closest<HTMLElement>("div")!.parentElement!.style.display).toBe("flex");
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip this step" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Exit tutorial" })).toBeInTheDocument();
    // The first step is saved as soon as it shows.
    expect(updateSettingsMock).toHaveBeenCalledWith(
      tourWrittenAs({ status: "in_progress", step: "welcome" }),
    );
  });

  it("renders nothing, and writes nothing, when the configuration turns the tour off", () => {
    setSettings({ onboarding_tour_enabled: false });
    render(<TourProvider>app</TourProvider>);
    expect(driverMock).not.toHaveBeenCalled();
    expect(highlightMock).not.toHaveBeenCalled();
    expect(updateSettingsMock).not.toHaveBeenCalled();
  });

  it("stays closed for an in-progress tour from an earlier session when it is off", () => {
    setSettings({
      onboarding_tour_enabled: false,
      onboarding_tour: { status: "in_progress", step: "skills" },
    });
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();
    expect(updateSettingsMock).not.toHaveBeenCalled();
  });

  it("waits for the settings to load", () => {
    useSettingsMock.mockReturnValue({ settings: null, loading: true, error: null, refresh: vi.fn() });
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();
  });

  it("stays closed for a finished or exited tour", () => {
    setSettings({ onboarding_tour: { status: "exited", step: "model" } });
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();
    expect(updateSettingsMock).not.toHaveBeenCalled();
  });

  it("writes exited with the current step when Exit tutorial is pressed", async () => {
    render(<TourProvider>app</TourProvider>);
    openPopover();
    await userEvent.click(screen.getByRole("button", { name: "Exit tutorial" }));
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "exited", step: "welcome" }),
    );
    expect(destroyMock).toHaveBeenCalled();
  });

  it("exits on Escape", async () => {
    render(<TourProvider>app</TourProvider>);
    await userEvent.keyboard("{Escape}");
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "exited", step: "welcome" }),
    );
  });

  it("leaves Escape to an open dialog or menu, and stays open", async () => {
    render(<TourProvider>app</TourProvider>);
    for (const open of ['<div class="modal-backdrop"></div>', '<div role="listbox"></div>']) {
      const el = document.body.appendChild(document.createElement("div"));
      el.innerHTML = open;
      await userEvent.keyboard("{Escape}");
      el.remove();
    }
    expect(updateSettingsMock).not.toHaveBeenCalledWith(tourWrittenAs({ status: "exited", step: "welcome" }));
  });

  it("moves to the project step and saves it when the welcome and the sidebar are passed", async () => {
    anchor("welcome:sidebar");
    render(<TourProvider>app</TourProvider>);
    openPopover();
    await userEvent.click(screen.getByRole("button", { name: "Start the tour" }));
    expect(lastStepKey()).toBe("The sidebar");
    pressNext();
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "in_progress", step: "project" }),
    );
  });

  it("offers no Next on a step that waits for the researcher's action", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    vi.useFakeTimers();
    render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    openPopover();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip this step" })).toBeInTheDocument();
  });

  it("skips a step to the next one", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    vi.useFakeTimers();
    render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    openPopover();
    // userEvent waits on timers; click directly under fake timers.
    act(() => screen.getByRole("button", { name: "Skip this step" }).click());
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "in_progress", step: "skills" }),
    );
  });

  it("saves finished when the last step is passed", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "first-question" } });
    vi.useFakeTimers();
    render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    openPopover();
    act(() => screen.getByRole("button", { name: "Skip this step" }).click());
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "finished", step: "first-question" }),
    );
  });

  it("opens at the welcome when Replay writes it, even with the switch off", () => {
    setSettings({
      onboarding_tour_enabled: false,
      onboarding_tour: { status: "exited", step: "model" },
    });
    const { rerender } = render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();

    setSettings({
      onboarding_tour_enabled: false,
      onboarding_tour: { status: "in_progress", step: "welcome" },
    });
    rerender(<TourProvider>app</TourProvider>);
    expect(highlightMock).toHaveBeenCalledTimes(1);
    expect(lastStep().popover.title).toBe("Welcome to VISTA");
  });

  it("points at the step's data-tour target when it is on the page", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    const target = document.body.appendChild(document.createElement("button"));
    target.setAttribute("data-tour", "knowledge-bases:visit");
    render(<TourProvider>app</TourProvider>);
    expect(lastStep().element).toBe(target);
  });

  it("waits for a target that mounts late, then centres if it never does", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "model" } });
    vi.useFakeTimers();
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS - 1));
    expect(highlightMock).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(1));
    expect(highlightMock).toHaveBeenCalledTimes(1);
    expect(lastStep().element).toBeUndefined();
  });

  it("points at a target that mounts while it waits", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();
    const target = document.createElement("button");
    target.setAttribute("data-tour", "knowledge-bases:visit");
    document.body.appendChild(target);
    await vi.waitFor(() => expect(highlightMock).toHaveBeenCalledTimes(1));
    expect(lastStep().element).toBe(target);
  });

  it("names the provider's suggested model on the model step, or none", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "model" } });
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({ hasCredential: true }),
      error: null,
    });
    mountChatAnchors();
    const { unmount } = render(<TourProvider>app</TourProvider>);
    walkToPick();
    const withDefault = lastStep() as Step & { popover: { description: string } };
    expect(withDefault.popover.description).toBe("Suggested: claude-sonnet. Any model on the list works.");
    unmount();

    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({
        hasCredential: true,
        providers: [{ id: "i2", name: "Custom", takesUrl: true, defaultModel: null }],
      }),
      error: null,
    });
    render(<TourProvider>app</TourProvider>);
    walkToPick();
    const without = lastStep() as Step & { popover: { description: string } };
    expect(without.popover.description).not.toContain("Suggested");
    expect(without.popover.description).not.toContain("claude-sonnet");
  });

  it("destroys the driver when the provider unmounts", () => {
    const { unmount } = render(<TourProvider>app</TourProvider>);
    unmount();
    expect(destroyMock).toHaveBeenCalled();
  });
});

function Probe() {
  const tour = useTour();
  const report = useTourEvent();
  return (
    <button onClick={() => report("kb-opened")}>
      {tour.running ? "running" : "idle"}
    </button>
  );
}

describe("useTour and useTourEvent", () => {
  it("do nothing without a running tour", async () => {
    render(<Probe />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("button")).toHaveTextContent("idle");
  });

  it("do nothing when the tour is off", async () => {
    setSettings({ onboarding_tour_enabled: false });
    render(
      <TourProvider>
        <Probe />
      </TourProvider>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("button")).toHaveTextContent("idle");
  });

  it("report a tour event while the tour is running", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    nav.pathname = "/knowledge-bases";
    document.body.appendChild(document.createElement("header")).className = "page-topbar";
    for (const key of ["knowledge-bases:list", "knowledge-bases:open-kb", "knowledge-bases:about"]) {
      document.body.appendChild(document.createElement("i")).setAttribute("data-tour", key);
    }
    render(
      <TourProvider>
        <Probe />
      </TourProvider>,
    );
    expect(screen.getByText("running")).toBeInTheDocument();
    pressNext();
    pressNext();
    expect(lastStepKey()).toBe("Open one");
    await userEvent.click(screen.getByText("running"));
    // kb-opened completes open-kb, leaving the knowledge base's about, which has a Next.
    await vi.waitFor(() => expect(lastStepKey()).toBe("About this knowledge base"));
  });
});

function lastStepKey(): string | undefined {
  return (highlightMock.mock.calls.at(-1)?.[0] as Step | undefined)?.popover.title;
}

function anchor(key: string): HTMLElement {
  const el = document.body.appendChild(document.createElement("button"));
  el.setAttribute("data-tour", key);
  el.textContent = key;
  return el;
}

function Controls() {
  const { report, reportFailure, claimStarter } = useTour();
  const [claimed, setClaimed] = useState("");
  return (
    <div>
      <button onClick={() => report("skill-opened")}>report skill</button>
      <button onClick={() => report("skill-closed")}>report skill closed</button>
      <button onClick={() => report("turn-started")}>report turn started</button>
      <button onClick={() => report("kb-opened")}>report kb</button>
      <button onClick={() => report("example-image-opened")}>report image</button>
      <button onClick={() => report("turn-succeeded")}>report success</button>
      <button onClick={() => reportFailure("credential")}>fail credential</button>
      <button onClick={() => reportFailure("model")}>fail model</button>
      <button onClick={() => reportFailure("other")}>fail other</button>
      <button onClick={() => setClaimed((c) => `${c}${claimStarter()},`)}>claim</button>
      <output>{claimed}</output>
    </div>
  );
}

const tree = (
  <TourProvider>
    <Controls />
  </TourProvider>
);

describe("a coachmark that waits for an action", () => {
  it("stands aside when its control is used, and is drawn again by the next report", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    const target = anchor("knowledge-bases:visit");
    render(tree);
    expect(highlightMock).toHaveBeenCalledTimes(1);

    // Using something else leaves it up (and a report draws it again).
    await userEvent.click(screen.getByRole("button", { name: "report kb" }));
    expect(destroyMock).not.toHaveBeenCalled();
    expect(highlightMock).toHaveBeenCalledTimes(2);

    await userEvent.click(target);
    expect(destroyMock).toHaveBeenCalledTimes(1);
    expect(highlightMock).toHaveBeenCalledTimes(2);

    await userEvent.click(screen.getByRole("button", { name: "report kb" }));
    expect(driverMock).toHaveBeenCalledTimes(2);
    expect(highlightMock).toHaveBeenCalledTimes(3);
    expect(lastStep().element).toBe(target);
  });

  it("stands aside when the highlighted control leaves the page", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    const target = anchor("knowledge-bases:visit");
    render(tree);
    expect(destroyMock).not.toHaveBeenCalled();
    target.remove();
    await vi.waitFor(() => expect(destroyMock).toHaveBeenCalledTimes(1));
  });

  it("stays up when the control is used on a coachmark that Next completes", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "skills" } });
    nav.pathname = "/skills";
    document.body.appendChild(document.createElement("header")).className = "page-topbar";
    anchor("skills:loaded");
    anchor("skills:available");
    anchor("skills:open-skill");
    anchor("skills:detail");
    const load = anchor("skills:load");
    render(tree);
    pressNext();
    pressNext();
    pressNext();
    expect(lastStepKey()).toBe("Read a skill");
    await userEvent.click(screen.getByRole("button", { name: "report skill" }));
    expect(lastStepKey()).toBe("A skill's instructions");
    await userEvent.click(screen.getByRole("button", { name: "report skill closed" }));
    expect(lastStep().element).toBe(load);
    // Moving on from an earlier coachmark started a new driver; using the control does not.
    const destroyed = destroyMock.mock.calls.length;
    await userEvent.click(load);
    expect(destroyMock).toHaveBeenCalledTimes(destroyed);
  });

  it("makes a Next coachmark's control look-only, and a click on it pulses the coachmark", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "skills" } });
    nav.pathname = "/skills";
    document.body.appendChild(document.createElement("header")).className = "page-topbar";
    const loaded = anchor("skills:loaded");
    loaded.getBoundingClientRect = () => ({ left: 0, top: 0, right: 100, bottom: 50, width: 100, height: 50 }) as DOMRect;
    render(tree);
    pressNext();
    expect(lastStep().element).toBe(loaded);
    expect((lastStep() as Step & { disableActiveInteraction?: boolean }).disableActiveInteraction).toBe(true);
    const popover = document.body.appendChild(document.createElement("div"));
    popover.className = "driver-popover vista-tour";
    loaded.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 10, clientY: 10 }));
    expect(popover.classList.contains("vista-tour-nudge")).toBe(true);
  });

  it("leaves the file previews usable, for their Download", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "files" } });
    nav.pathname = "/files";
    document.body.appendChild(document.createElement("header")).className = "page-topbar";
    for (const a of ["files:outputs", "files:uploads", "files:open-chart", "files:preview"]) anchor(a);
    render(tree);
    pressNext();
    pressNext();
    pressNext();
    await userEvent.click(screen.getByRole("button", { name: "report image" }));
    expect(lastStepKey()).toBe("Image preview");
    expect((lastStep() as Step & { disableActiveInteraction?: boolean }).disableActiveInteraction).toBe(false);
  });

  it("keeps a skill's detail coachmark up while the skill is used, since Close is inside it", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "skills" } });
    nav.pathname = "/skills";
    document.body.appendChild(document.createElement("header")).className = "page-topbar";
    anchor("skills:loaded");
    anchor("skills:available");
    anchor("skills:open-skill");
    const detail = anchor("skills:detail");
    const close = document.createElement("button");
    close.textContent = "Close";
    detail.appendChild(close);
    anchor("skills:load");
    render(tree);
    pressNext();
    pressNext();
    pressNext();
    await userEvent.click(screen.getByRole("button", { name: "report skill" }));
    expect(lastStep().element).toBe(detail);
    const destroyed = destroyMock.mock.calls.length;
    await userEvent.click(close);
    expect(destroyMock).toHaveBeenCalledTimes(destroyed);
    // It goes with the detail, which is what the researcher closing it does.
    detail.remove();
    await vi.waitFor(() => expect(destroyMock.mock.calls.length).toBeGreaterThan(destroyed));
  });
});

describe("a coachmark follows the page", () => {
  it("places the coachmark again once its buttons are in it", async () => {
    render(<TourProvider>app</TourProvider>);
    expect(refreshMock).not.toHaveBeenCalled();
    openPopover();
    await vi.waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("places the coachmark again when its anchor moves", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    const target = anchor("knowledge-bases:visit");
    let top = 100;
    target.getBoundingClientRect = () => ({ left: 0, top, width: 50, height: 20 }) as DOMRect;
    render(tree);
    await new Promise((r) => setTimeout(r, 50));
    expect(refreshMock).not.toHaveBeenCalled();
    top = 130;
    await vi.waitFor(() => expect(refreshMock).toHaveBeenCalledTimes(1));
  });

  it("re-targets when the route changes, from the rail's entry to the page's item", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "knowledge-bases" } });
    const rail = anchor("knowledge-bases:visit");
    const { rerender } = render(tree);
    expect(lastStep().element).toBe(rail);

    // The rail drops its anchor on the page, which shows its overview first.
    rail.removeAttribute("data-tour");
    const topbar = document.body.appendChild(document.createElement("header"));
    topbar.className = "page-topbar";
    nav.pathname = "/knowledge-bases";
    // A fresh element, since React skips an element it has already rendered.
    rerender(<TourProvider><Controls /></TourProvider>);
    expect(lastStep().element).toBe(topbar);
    expect(lastStepKey()).toBe("The Knowledge Bases screen");
  });

  it("pulses the coachmark when the researcher clicks outside its target", () => {
    vi.useFakeTimers();
    try {
      render(tree);
      const popover = document.body.appendChild(document.createElement("div"));
      popover.className = "driver-popover vista-tour";
      const onOverlayClick = driverMock.mock.calls[0][0].overlayClickBehavior as () => void;
      onOverlayClick();
      expect(popover.classList.contains("vista-tour-nudge")).toBe(true);
      vi.advanceTimersByTime(900);
      expect(popover.classList.contains("vista-tour-nudge")).toBe(false);
      popover.remove();
    } finally {
      vi.useRealTimers();
    }
  });

  it("draws an overview on the top bar with no overlay, and the next coachmark with one", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "skills" } });
    nav.pathname = "/skills";
    const topbar = document.body.appendChild(document.createElement("header"));
    topbar.className = "page-topbar";
    const loaded = anchor("skills:loaded");
    render(tree);
    expect(lastStep().element).toBe(topbar);
    expect(driverMock).toHaveBeenCalledTimes(1);
    expect(driverMock.mock.calls[0][0]).toMatchObject({ overlayOpacity: 0 });

    pressNext();
    expect(destroyMock).toHaveBeenCalledTimes(1);
    expect(driverMock).toHaveBeenCalledTimes(2);
    expect(driverMock.mock.calls[1][0]).toMatchObject({ overlayOpacity: 1 });
    expect(lastStep().element).toBe(loaded);
  });
});

describe("where the tour takes the researcher", () => {
  it("opens the projects page once when the project step starts", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    const { rerender } = render(tree);
    expect(push).toHaveBeenCalledExactlyOnceWith("/projects");
    rerender(<TourProvider><Controls /></TourProvider>);
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("stays put when it is already there", () => {
    nav.pathname = "/projects";
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    render(tree);
    expect(push).not.toHaveBeenCalled();
  });

  it("opens Settings at the model provider, and closes it for the model picker", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "model" } });
    mountChatAnchors();
    anchor("model:provider");
    anchor("model:credential");
    const { rerender } = render(tree);
    // The chat is explained first, with the picker in view and Settings shut.
    expect(openSettingsMock).not.toHaveBeenCalled();
    walkToPick();
    expect(lastStepKey()).toBe("Model provider");
    expect(openSettingsMock).toHaveBeenCalledExactlyOnceWith("agent");
    expect(closeSettingsMock).not.toHaveBeenCalled();

    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({ hasCredential: true }),
      error: null,
    });
    rerender(<TourProvider><Controls /></TourProvider>);
    // The key entered during the tour is confirmed in Settings; Next closes it.
    expect(lastStepKey()).toBe("Key saved");
    expect(closeSettingsMock).not.toHaveBeenCalled();
    pressNext();
    expect(closeSettingsMock).toHaveBeenCalledTimes(1);
    expect(openSettingsMock).toHaveBeenCalledTimes(1);
  });

  it("skips Key saved when the credential was there before the tour", () => {
    useAgentSettingsMock.mockReturnValue({ settings: agentSettings({ hasCredential: true }), error: null });
    setSettings({ onboarding_tour: { status: "in_progress", step: "model" } });
    mountChatAnchors();
    render(tree);
    pressNext();
    pressNext();
    pressNext();
    pressNext();
    expect(lastStepKey()).toBe("Choose a model");
  });

  it("opens the chat for the model step from another page, once", () => {
    nav.pathname = "/projects";
    setSettings({ onboarding_tour: { status: "in_progress", step: "model" } });
    render(tree);
    expect(push).toHaveBeenCalledExactlyOnceWith("/");
  });

  it("opens the chat for the first question", () => {
    nav.pathname = "/files";
    setSettings({ onboarding_tour: { status: "in_progress", step: "first-question" } });
    render(tree);
    expect(push).toHaveBeenCalledExactlyOnceWith("/");
  });

  it("does nothing on a step the rail leads to", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "skills" } });
    render(tree);
    expect(push).not.toHaveBeenCalled();
    expect(openSettingsMock).not.toHaveBeenCalled();
  });
});

describe("the first question", () => {
  beforeEach(() => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "first-question" } });
  });

  it("hands out the starter question once", async () => {
    render(tree);
    await userEvent.click(screen.getByRole("button", { name: "claim" }));
    await userEvent.click(screen.getByRole("button", { name: "claim" }));
    expect(screen.getByRole("status")).toHaveTextContent("true,false,");
  });

  it("points at what fixes each failure", async () => {
    const send = anchor("first-question:send");
    const settingsButton = anchor("first-question:settings");
    const picker = anchor("model:pick");
    const panel = anchor("model:panel");
    render(tree);
    expect(lastStep().element).toBe(send);
    expect(lastStepKey()).toBe("Ask your first question");

    // The turn starting moves the tour to the agent at work...
    await userEvent.click(screen.getByRole("button", { name: "report turn started" }));
    expect(lastStep().element).toBe(panel);
    expect(lastStepKey()).toBe("The agent at work");

    // ...and a failure puts it back at Send, pointing at the fix.
    await userEvent.click(screen.getByRole("button", { name: "fail credential" }));
    expect(lastStep().element).toBe(settingsButton);
    expect(lastStepKey()).toBe("VISTA needs a model connected");

    await userEvent.click(screen.getByRole("button", { name: "fail model" }));
    expect(lastStep().element).toBe(picker);
    expect(lastStepKey()).toBe("Choose a model first");

    await userEvent.click(screen.getByRole("button", { name: "fail other" }));
    expect(lastStep().element).toBe(send);
    expect(lastStepKey()).toBe("That didn't work");

    // The same failure again is drawn again, not swallowed.
    const calls = highlightMock.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "fail other" }));
    expect(highlightMock.mock.calls.length).toBeGreaterThan(calls);
  });

  it("goes from the answer to a centred finish, labelled Done, on the next success after a failure", async () => {
    anchor("first-question:send");
    anchor("model:panel");
    const answer = anchor("first-question:answer");
    render(tree);
    await userEvent.click(screen.getByRole("button", { name: "fail credential" }));
    await userEvent.click(screen.getByRole("button", { name: "report turn started" }));
    await userEvent.click(screen.getByRole("button", { name: "report success" }));
    expect(lastStepKey()).toBe("The answer");
    expect(lastStep().element).toBe(answer);

    pressNext();
    expect(lastStepKey()).toBe("You're set up");
    expect(lastStep().element).toBeUndefined();
    openPopover();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Documentation" })).toHaveAttribute(
      "href",
      "https://genesis-vista.github.io/",
    );
  });

  it("leaves the answer in place when a later turn fails", async () => {
    anchor("first-question:send");
    anchor("model:panel");
    anchor("first-question:answer");
    render(tree);
    await userEvent.click(screen.getByRole("button", { name: "report turn started" }));
    await userEvent.click(screen.getByRole("button", { name: "report success" }));
    await userEvent.click(screen.getByRole("button", { name: "fail other" }));
    expect(lastStepKey()).toBe("The answer");
  });
});

describe("the model suggestion", () => {
  it.each([
    ["AmSC MAG", "claude-sonnet"],
    ["OLCF Inference", "gpt-oss-120b"],
  ])("names %s's default, %s", (name, defaultModel) => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "model" } });
    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({
        hasCredential: true,
        providers: [{ id: "i2", name, takesUrl: false, defaultModel }],
      }),
      error: null,
    });
    mountChatAnchors();
    render(tree);
    walkToPick();
    expect((lastStep().popover as unknown as { description: string }).description).toContain(
      defaultModel,
    );
  });
});
