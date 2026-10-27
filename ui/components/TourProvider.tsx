"use client";

import "driver.js/dist/driver.css";
import { driver, type Driver } from "driver.js";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAgentSettings } from "@/lib/agent-settings";
import {
  completesOnNext,
  nextTourStep,
  type Acknowledgement,
  type TourEvent,
  type TourPosition,
  type TourStepId,
} from "@/lib/onboarding";
import { coachmarkFor, type Coachmark } from "@/lib/onboarding-copy";
import { useActiveProjectName } from "@/lib/projects";
import { updateSettings, useSettings, type OnboardingTourState } from "@/lib/settings";

/** How long a step waits for its `data-tour` target before it centres instead. */
export const TARGET_TIMEOUT_MS = 3000;

/** What the first-boot tour exposes to the rest of the app. */
export type TourApi = {
  /** The coachmark showing, or null when no tour is running. */
  position: TourPosition | null;
  running: boolean;
  /** Pass the current coachmark (only offered where it completes by Next). */
  next: () => void;
  /** Skip the current step. */
  skip: () => void;
  /** Close the tour for good; Settings can replay it. */
  exit: () => void;
  /** Tell the tour the researcher did something it waits for. */
  report: (event: TourEvent) => void;
};

const NOT_RUNNING: TourApi = {
  position: null,
  running: false,
  next: () => {},
  skip: () => {},
  exit: () => {},
  report: () => {},
};

const TourContext = createContext<TourApi>(NOT_RUNNING);

/** The tour's position and controls; everything is inert when no tour is running. */
export function useTour(): TourApi {
  return useContext(TourContext);
}

/**
 * `report(event)` for pages to call when the researcher does something a tour
 * step waits for. Does nothing when no tour is running.
 */
export function useTourEvent(): (event: TourEvent) => void {
  return useContext(TourContext).report;
}

/** What this page load has seen of the tour; a replay starts it over. */
type Session = {
  events: ReadonlySet<TourEvent>;
  skipped: ReadonlySet<TourStepId>;
  acknowledged: ReadonlySet<Acknowledgement>;
  /** Replay was pressed while mounted: the tour opens even with the off switch on. */
  replayed: boolean;
  /** Exit was pressed; closes at once, before the write has come back. */
  dismissed: boolean;
};

const FRESH_SESSION: Session = {
  events: new Set(),
  skipped: new Set(),
  acknowledged: new Set(),
  replayed: false,
  dismissed: false,
};

function sameTour(a: OnboardingTourState | null, b: OnboardingTourState | null): boolean {
  return a?.status === b?.status && a?.step === b?.step;
}

/** What Replay tutorial writes. */
function isReplayStart(s: OnboardingTourState | null): boolean {
  return s !== null && s.status === "in_progress" && s.step === "welcome";
}

function persist(state: OnboardingTourState): Promise<unknown> {
  // A failed write must not break the tour; the next change tries again.
  return updateSettings({ onboarding_tour: state }).catch(() => undefined);
}

/**
 * The first-boot tour. Reads the installation's settings, the inference state
 * and the active project, works out the coachmark with `nextTourStep`, saves
 * the step it reaches, and draws the coachmark with driver.js. It renders no
 * markup of its own, and it stays closed until the settings have loaded.
 */
export function TourProvider({ children }: { children: ReactNode }) {
  const { settings, loading: settingsLoading } = useSettings();
  const { settings: agent } = useAgentSettings();
  const activeProject = useActiveProjectName();
  const [session, setSession] = useState<Session>(FRESH_SESSION);

  const loaded = !settingsLoading && settings !== null && agent !== null;
  const stored = settings?.onboarding_tour ?? null;
  const enabled = settings?.onboarding_tour_enabled !== false;

  // A replay is `/settings` changing under us to the start of the tour, so it
  // works the same whether the Settings button or anything else wrote it.
  // Compared during render, React's pattern for state that follows a prop.
  const [seen, setSeen] = useState<{ stored: OnboardingTourState | null } | null>(null);
  if (loaded && seen === null) {
    setSeen({ stored });
  } else if (loaded && seen !== null && !sameTour(seen.stored, stored)) {
    setSeen({ stored });
    if (isReplayStart(stored) && !isReplayStart(seen.stored)) {
      setSession({ ...FRESH_SESSION, replayed: true });
    }
  }

  const providerDefault = agent?.providers.find((p) => p.id === agent.provider)?.defaultModel ?? null;

  const position = useMemo<TourPosition | null>(() => {
    if (!loaded || agent === null) return null;
    if (!enabled && !session.replayed) return null;
    if (session.dismissed) return null;
    return nextTourStep({
      stored,
      inference: {
        hasCredential: agent.hasCredential,
        model: agent.model,
        modelIsDefault: agent.modelIsDefault,
        providerDefault,
      },
      activeProject,
      events: session.events,
      skipped: session.skipped,
      acknowledged: session.acknowledged,
    });
  }, [loaded, agent, enabled, session, stored, providerDefault, activeProject]);
  const running = position !== null;
  const step = position?.step ?? null;
  const subStep = position?.subStep ?? null;

  // Save the step reached, and on the way out whether the tour finished. Only
  // on a change: the write's own answer is what makes the stored step match.
  const lastStep = useRef<TourStepId | null>(null);
  const writing = useRef<string | null>(null);
  const storedStatus = stored?.status ?? null;
  const storedStep = stored?.step ?? null;
  useEffect(() => {
    if (!loaded) return;
    let state: OnboardingTourState | null = null;
    if (step !== null) {
      lastStep.current = step;
      if (storedStatus !== "in_progress" || storedStep !== step) {
        state = { status: "in_progress", step };
      }
    } else if (
      lastStep.current !== null &&
      !session.dismissed &&
      (storedStatus === null || storedStatus === "in_progress")
    ) {
      state = { status: "finished", step: lastStep.current };
    }
    if (state === null) return;
    const key = `${state.status}:${state.step}`;
    if (writing.current === key) return;
    writing.current = key;
    void persist(state).finally(() => {
      if (writing.current === key) writing.current = null;
    });
  }, [loaded, step, storedStatus, storedStep, session.dismissed]);

  const next = useCallback(() => {
    if (position === null || !completesOnNext(position.step, position.subStep)) return;
    const key: Acknowledgement = `${position.step}:${position.subStep}`;
    setSession((s) => ({ ...s, acknowledged: new Set(s.acknowledged).add(key) }));
  }, [position]);

  const skip = useCallback(() => {
    if (position === null) return;
    const skipped = position.step;
    setSession((s) => ({ ...s, skipped: new Set(s.skipped).add(skipped) }));
  }, [position]);

  const exit = useCallback(() => {
    if (position === null) return;
    setSession((s) => ({ ...s, dismissed: true }));
    void persist({ status: "exited", step: position.step });
  }, [position]);

  const report = useCallback(
    (event: TourEvent) => {
      if (!running) return;
      setSession((s) => (s.events.has(event) ? s : { ...s, events: new Set(s.events).add(event) }));
    },
    [running],
  );

  const api = useMemo<TourApi>(
    () => ({ position, running, next, skip, exit, report }),
    [position, running, next, skip, exit, report],
  );

  // The coachmark. One driver instance; VISTA's buttons call the latest actions.
  const actions = useRef({ next, skip, exit });
  useEffect(() => {
    actions.current = { next, skip, exit };
  });
  const driverRef = useRef<Driver | null>(null);

  useEffect(() => {
    if (step === null || subStep === null) return;
    const mark = coachmarkFor(step, subStep, { providerDefault });
    if (mark === null) return;
    const key = `${step}:${subStep}`;
    const withNext = completesOnNext(step, subStep);

    const show = (element: Element | undefined) => {
      if (driverRef.current === null) {
        driverRef.current = driver({
          allowClose: false,
          allowKeyboardControl: false,
          overlayClickBehavior: "none",
          overlayColor: "var(--scrim-strong)",
          overlayOpacity: 1,
          popoverClass: "vista-tour",
          showButtons: [],
          // Anything that asks driver.js to close is the tour being exited.
          onDestroyStarted: () => actions.current.exit(),
        });
      }
      driverRef.current.highlight({
        element,
        popover: {
          title: mark.title,
          description: mark.body,
          onPopoverRender: (popover) =>
            renderCoachmark(popover, mark, withNext, {
              next: () => actions.current.next(),
              skip: () => actions.current.skip(),
              exit: () => actions.current.exit(),
            }),
        },
      });
    };

    // The welcome has nothing to point at; the other steps wait for their anchor.
    if (key === "welcome:intro") {
      show(undefined);
      return;
    }
    const selector = `[data-tour="${key}"]`;
    const found = document.querySelector(selector);
    if (found !== null) {
      show(found);
      return;
    }
    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el === null) return;
      stop();
      show(el);
    });
    const timer = setTimeout(() => {
      stop();
      show(undefined);
    }, TARGET_TIMEOUT_MS);
    function stop() {
      observer.disconnect();
      clearTimeout(timer);
    }
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-tour"],
    });
    return stop;
  }, [step, subStep, providerDefault]);

  // Escape closes the tour. driver.js's own handler also answers the arrow
  // keys, which here would step through nothing, so it is turned off above.
  useEffect(() => {
    if (!running) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") actions.current.exit();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [running]);

  const destroyDriver = useCallback(() => {
    driverRef.current?.destroy();
    driverRef.current = null;
  }, []);
  useEffect(() => {
    if (!running) destroyDriver();
  }, [running, destroyDriver]);
  useEffect(() => destroyDriver, [destroyDriver]);

  return <TourContext.Provider value={api}>{children}</TourContext.Provider>;
}

type Popover = Parameters<NonNullable<NonNullable<Parameters<Driver["highlight"]>[0]["popover"]>["onPopoverRender"]>>[0];

function button(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

/** Fills driver.js's popover with the coachmark's text and VISTA's buttons. */
function renderCoachmark(
  popover: Popover,
  mark: Coachmark,
  withNext: boolean,
  actions: { next: () => void; skip: () => void; exit: () => void },
): void {
  popover.wrapper.setAttribute("role", "dialog");
  popover.wrapper.setAttribute("aria-label", mark.title);
  popover.title.textContent = mark.title;
  popover.description.textContent = mark.body;
  if (mark.link) {
    const a = document.createElement("a");
    a.href = mark.link.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.className = "tour-link";
    a.textContent = mark.link.label;
    const p = document.createElement("p");
    p.append(a);
    popover.description.append(p);
  }

  const exit = button("Exit tutorial", "tour-btn tour-btn-quiet", actions.exit);
  const skip = button("Skip this step", "tour-btn", actions.skip);
  const buttons = [exit, skip];
  let primary = skip;
  if (withNext) {
    primary = button("Next", "tour-btn tour-btn-primary", actions.next);
    buttons.push(primary);
  }
  const row = document.createElement("div");
  row.className = "tour-buttons";
  row.append(...buttons);
  popover.footer.replaceChildren(row);
  // driver.js hides the footer inline when it has no buttons of its own
  // (showButtons: []), just before this hook runs; ours live in it.
  popover.footer.style.display = "flex";
  requestAnimationFrame(() => primary.focus({ preventScroll: true }));
}
