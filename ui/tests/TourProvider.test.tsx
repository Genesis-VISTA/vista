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

function tourWrittenAs(state: OnboardingTourState) {
  return { onboarding_tour: state };
}

beforeEach(() => {
  highlightMock.mockReset();
  destroyMock.mockReset();
  driverMock.mockReset();
  refreshMock.mockReset();
  driverMock.mockImplementation(() => ({
    highlight: highlightMock,
    destroy: destroyMock,
    refresh: refreshMock,
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
    expect(screen.getByRole("button", { name: "Next" }).closest<HTMLElement>("div")!.parentElement!.style.display).toBe("flex");
    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
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

  it("moves to the project step and saves it when Next is pressed on the welcome", async () => {
    render(<TourProvider>app</TourProvider>);
    openPopover();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "in_progress", step: "project" }),
    );
  });

  it("offers no Next on a step that waits for the researcher's action", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    vi.useFakeTimers();
    render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    openPopover();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip this step" })).toBeInTheDocument();
  });

  it("skips a step to the next one", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    vi.useFakeTimers();
    render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    openPopover();
    // userEvent waits on timers; click directly under fake timers.
    act(() => screen.getByRole("button", { name: "Skip this step" }).click());
    expect(updateSettingsMock).toHaveBeenLastCalledWith(
      tourWrittenAs({ status: "in_progress", step: "model" }),
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
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    const target = document.body.appendChild(document.createElement("button"));
    target.setAttribute("data-tour", "project:open");
    render(<TourProvider>app</TourProvider>);
    expect(lastStep().element).toBe(target);
  });

  it("waits for a target that mounts late, then centres if it never does", () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
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
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    render(<TourProvider>app</TourProvider>);
    expect(highlightMock).not.toHaveBeenCalled();
    const target = document.createElement("button");
    target.setAttribute("data-tour", "project:open");
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
    vi.useFakeTimers();
    const { unmount } = render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    const withDefault = lastStep() as Step & { popover: { description: string } };
    expect(withDefault.popover.description).toContain("claude-sonnet");
    unmount();

    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({
        hasCredential: true,
        providers: [{ id: "i2", name: "Custom", takesUrl: true, defaultModel: null }],
      }),
      error: null,
    });
    render(<TourProvider>app</TourProvider>);
    act(() => void vi.advanceTimersByTime(TARGET_TIMEOUT_MS));
    const without = lastStep() as Step & { popover: { description: string } };
    expect(without.popover.description).toContain("any model your endpoint serves");
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
    for (const key of ["knowledge-bases:open-kb", "knowledge-bases:add-files"]) {
      document.body.appendChild(document.createElement("i")).setAttribute("data-tour", key);
    }
    render(
      <TourProvider>
        <Probe />
      </TourProvider>,
    );
    expect(screen.getByRole("button")).toHaveTextContent("running");
    await userEvent.click(screen.getByRole("button"));
    // kb-opened completes open-kb, leaving add-files, which has a Next.
    await vi.waitFor(() => expect(lastStepKey()).toBe("Add your own papers"));
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
      <button onClick={() => report("kb-opened")}>report kb</button>
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
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    const target = anchor("project:open");
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
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    const target = anchor("project:open");
    render(tree);
    expect(destroyMock).not.toHaveBeenCalled();
    target.remove();
    await vi.waitFor(() => expect(destroyMock).toHaveBeenCalledTimes(1));
  });

  it("stays up when the control is used on a coachmark that Next completes", async () => {
    setSettings({ onboarding_tour: { status: "in_progress", step: "skills" } });
    anchor("skills:open-skill");
    const load = anchor("skills:load");
    render(tree);
    await userEvent.click(screen.getByRole("button", { name: "report skill" }));
    expect(lastStep().element).toBe(load);
    await userEvent.click(load);
    expect(destroyMock).not.toHaveBeenCalled();
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
    setSettings({ onboarding_tour: { status: "in_progress", step: "project" } });
    const target = anchor("project:open");
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
    const rail = anchor("knowledge-bases:open-kb");
    const { rerender } = render(tree);
    expect(lastStep().element).toBe(rail);

    // The rail drops its anchor on the page, where the item has it.
    rail.removeAttribute("data-tour");
    const item = anchor("knowledge-bases:open-kb");
    nav.pathname = "/knowledge-bases";
    // A fresh element, since React skips an element it has already rendered.
    rerender(<TourProvider><Controls /></TourProvider>);
    expect(lastStep().element).toBe(item);
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
    const { rerender } = render(tree);
    expect(openSettingsMock).toHaveBeenCalledExactlyOnceWith("agent");
    expect(closeSettingsMock).not.toHaveBeenCalled();

    useAgentSettingsMock.mockReturnValue({
      settings: agentSettings({ hasCredential: true }),
      error: null,
    });
    rerender(<TourProvider><Controls /></TourProvider>);
    expect(closeSettingsMock).toHaveBeenCalledTimes(1);
    expect(openSettingsMock).toHaveBeenCalledTimes(1);
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
    render(tree);
    expect(lastStep().element).toBe(send);
    expect(lastStepKey()).toBe("Ask your first question");

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

  it("finishes on the next success after a failure", async () => {
    anchor("first-question:send");
    const finish = anchor("first-question:finish");
    render(tree);
    await userEvent.click(screen.getByRole("button", { name: "fail credential" }));
    await userEvent.click(screen.getByRole("button", { name: "report success" }));
    expect(lastStepKey()).toBe("You're ready");
    expect(lastStep().element).toBe(finish);
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
    anchor("model:pick");
    render(tree);
    expect((lastStep().popover as unknown as { description: string }).description).toContain(
      defaultModel,
    );
  });
});
