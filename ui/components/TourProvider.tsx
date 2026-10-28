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
import { usePathname, useRouter } from "next/navigation";
import { useAgentSettings } from "@/lib/agent-settings";
import {
  completesOnNext,
  nextTourStep,
  tourAnchor,
  type Acknowledgement,
  type TourEvent,
  type TourFailure,
  type TourPosition,
  type TourStepId,
} from "@/lib/onboarding";
import { coachmarkFor, type Coachmark } from "@/lib/onboarding-copy";
import { useActiveProjectName } from "@/lib/projects";
import { closeSettings, openSettings } from "@/lib/settings-modal";
import { updateSettings, useSettings, type OnboardingTourState } from "@/lib/settings";

/**
 * Next coachmarks whose target stays usable: the file previews, whose Download
 * the files step promises works while the tour waits.
 */
const USABLE_WHILE_EXPLAINED: ReadonlySet<string> = new Set(["files:chart-preview", "files:review"]);

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
  /**
   * Why the first question failed, until the step ends; null before any
   * failure. The coachmark then points at what fixes it.
   */
  failure: TourFailure | null;
  /** Tell the tour the first question failed. A later failure replaces this one. */
  reportFailure: (kind: TourFailure) => void;
  /**
   * True once per start of the first question, for the chat page to put the
   * starter question in the composer. False when no tour is running.
   */
  claimStarter: () => boolean;
};

const NOT_RUNNING: TourApi = {
  position: null,
  running: false,
  next: () => {},
  skip: () => {},
  exit: () => {},
  report: () => {},
  failure: null,
  reportFailure: () => {},
  claimStarter: () => false,
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

/** Coachmarks with nothing to point at; they sit in the middle of the window. */
const CENTRED: ReadonlySet<string> = new Set(["welcome:intro", "first-question:finish"]);

/** What this page load has seen of the tour; a replay starts it over. */
type Session = {
  events: ReadonlySet<TourEvent>;
  skipped: ReadonlySet<TourStepId>;
  acknowledged: ReadonlySet<Acknowledgement>;
  /** Replay was pressed while mounted: the tour opens even with the off switch on. */
  replayed: boolean;
  /** Exit was pressed; closes at once, before the write has come back. */
  dismissed: boolean;
  /** Why the first question failed; the next failure replaces it. */
  failure: TourFailure | null;
  /**
   * Counts every report, new or not. A coachmark that stood aside for the
   * researcher's click is drawn again when it changes.
   */
  pulse: number;
};

const FRESH_SESSION: Session = {
  events: new Set(),
  skipped: new Set(),
  acknowledged: new Set(),
  replayed: false,
  dismissed: false,
  failure: null,
  pulse: 0,
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
  const pathname = usePathname();
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

  const hasCredential = agent?.hasCredential ?? false;
  const providerDefault = agent?.providers.find((p) => p.id === agent.provider)?.defaultModel ?? null;

  const position = useMemo<TourPosition | null>(() => {
    if (!loaded || agent === null) return null;
    if (!enabled && !session.replayed) return null;
    if (session.dismissed) return null;
    return nextTourStep({
      pathname,
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
  }, [loaded, agent, enabled, session, stored, providerDefault, activeProject, pathname]);
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
      setSession((s) => ({
        ...s,
        events: s.events.has(event) ? s.events : new Set(s.events).add(event),
        pulse: s.pulse + 1,
      }));
    },
    [running],
  );

  // A failure belongs to the first question's Send coachmark and ends with it.
  const atSend = step === "first-question" && subStep === "send";
  const failure = atSend ? session.failure : null;
  const reportFailure = useCallback(
    (kind: TourFailure) => {
      if (!running) return;
      setSession((s) => {
        // A failure after the turn started puts the researcher back at Send,
        // where the coachmark points at what fixes it. Once the turn has
        // answered, a later failure is the researcher's own business.
        let events = s.events;
        if (events.has("turn-started") && !events.has("turn-succeeded")) {
          const without = new Set(events);
          without.delete("turn-started");
          events = without;
        }
        return { ...s, events, failure: kind, pulse: s.pulse + 1 };
      });
    },
    [running],
  );

  // The starter question goes into the composer once per visit to the last step.
  const starterClaimed = useRef(false);
  useEffect(() => {
    if (step !== "first-question") starterClaimed.current = false;
  }, [step]);
  const claimStarter = useCallback(() => {
    if (!running || starterClaimed.current) return false;
    starterClaimed.current = true;
    return true;
  }, [running]);

  const api = useMemo<TourApi>(
    () => ({ position, running, next, skip, exit, report, failure, reportFailure, claimStarter }),
    [position, running, next, skip, exit, report, failure, reportFailure, claimStarter],
  );

  // Where a sub-step needs the app somewhere, the provider takes it there once
  // as the sub-step starts; the researcher's clicks do the rest.
  const router = useRouter();
  const nav = useRef({ router, pathname });
  useEffect(() => {
    nav.current = { router, pathname };
  });
  const stepKey = step !== null && subStep !== null ? `${step}:${subStep}` : null;
  useEffect(() => {
    switch (stepKey) {
      case "project:overview":
        if (nav.current.pathname !== "/projects") nav.current.router.push("/projects");
        break;
      case "model:overview":
      case "first-question:send":
        // Opening a project lands in chat, but a researcher can be elsewhere.
        if (nav.current.pathname !== "/") nav.current.router.push("/");
        break;
      case "model:provider":
        openSettings("agent");
        break;
      case "model:pick":
        // The researcher is in chat by now; the picker is behind the modal.
        closeSettings();
        break;
    }
  }, [stepKey]);

  // The coachmark. One driver instance; VISTA's buttons call the latest actions.
  const actions = useRef({ next, skip, exit });
  useEffect(() => {
    actions.current = { next, skip, exit };
  });
  const driverRef = useRef<Driver | null>(null);
  const destroyDriver = useCallback(() => {
    driverRef.current?.destroy();
    driverRef.current = null;
  }, []);

  const anchorKey = position !== null ? tourAnchor(position, failure) : null;
  const pulse = session.pulse;
  useEffect(() => {
    if (step === null || subStep === null || anchorKey === null) return;
    const mark = coachmarkFor(step, subStep, { providerDefault, hasCredential, failure });
    if (mark === null) return;
    const key = anchorKey;
    const withNext = completesOnNext(step, subStep);
    // Stands aside for the researcher: the page is theirs while they use the
    // highlighted control, and the coachmark is drawn again by the next report,
    // step or page change (this effect running again).
    let standAside = () => {};

    // An overview points at the page's top bar and leaves the page undimmed.
    const overview = subStep === "overview";
    // A coachmark passed with Next only explains its control: using it would
    // open something (Import's dialog, Settings) under the tour's overlay.
    const lookOnly = withNext && !USABLE_WHILE_EXPLAINED.has(`${step}:${subStep}`);
    const show = (element: Element | undefined) => {
      // The overlay's opacity is fixed when driver.js draws it, so a change
      // of kind starts a new driver.
      if (driverRef.current !== null && (driverRef.current.getConfig().overlayOpacity === 0) !== overview) {
        destroyDriver();
      }
      if (driverRef.current === null) {
        driverRef.current = driver({
          // Its glide to the next anchor fixes the coachmark's place from the
          // size it had before our buttons were in it, and ignores a refresh.
          animate: false,
          allowClose: false,
          allowKeyboardControl: false,
          // A click outside the coachmark's target nudges the coachmark.
          overlayClickBehavior: () => nudgeCoachmark(),
          overlayColor: "var(--scrim-strong)",
          overlayOpacity: overview ? 0 : 1,
          // Undimmed, an overview's card needs its own border to stand out.
          popoverClass: overview ? "vista-tour vista-tour-overview" : "vista-tour",
          showButtons: [],
          // Anything that asks driver.js to close is the tour being exited.
          onDestroyStarted: () => actions.current.exit(),
        });
      }
      driverRef.current.highlight({
        element,
        disableActiveInteraction: lookOnly,
        popover: {
          title: mark.title,
          description: mark.body,
          onPopoverRender: (popover) => {
            renderCoachmark(popover, mark, withNext, {
              next: () => actions.current.next(),
              skip: () => actions.current.skip(),
              exit: () => actions.current.exit(),
            });
            // driver.js measured the popover before our buttons were in it, and
            // it is placed from that height: place it again with the real one.
            requestAnimationFrame(() => driverRef.current?.refresh());
          },
        },
      });
      if (element === undefined) return;

      // driver.js places the coachmark once, and again only on scroll and
      // resize. Pages keep laying out after their anchor appears (a stats line
      // arriving above a button), so follow the anchor and place it again.
      const box = (el: Element) => {
        const r = el.getBoundingClientRect();
        return `${r.left},${r.top},${r.width},${r.height}`;
      };
      let placedAt = box(element);
      let frame = requestAnimationFrame(function follow() {
        const now = box(element);
        if (now !== placedAt) {
          placedAt = now;
          driverRef.current?.refresh();
        }
        frame = requestAnimationFrame(follow);
      });
      standAside = () => cancelAnimationFrame(frame);

      // The control under a look-only coachmark ignores the click, so the
      // coachmark answers it, as it does a click outside it.
      if (lookOnly) {
        const onDown = (event: PointerEvent) => {
          const r = element.getBoundingClientRect();
          const inside =
            event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
          const onCoachmark = event.target instanceof Element && event.target.closest(".driver-popover") !== null;
          if (inside && !onCoachmark) nudgeCoachmark();
        };
        document.addEventListener("pointerdown", onDown, true);
        standAside = () => {
          cancelAnimationFrame(frame);
          document.removeEventListener("pointerdown", onDown, true);
        };
      }

      // A coachmark that waits for an action stands aside once the researcher
      // uses its control, or the control goes away (a menu closing, a run
      // starting): driver.js would otherwise keep the rest of the page dead.
      if (!withNext) {
        // The open skill is explained in place and closed from inside it, so
        // its coachmark must stay up while the researcher uses it.
        const staysUp = key === "skills:detail";
        const onDown = (event: Event) => {
          if (staysUp) return;
          if (event.target instanceof Node && element.contains(event.target)) destroyDriver();
        };
        const gone = new MutationObserver(() => {
          if (!element.isConnected) destroyDriver();
        });
        document.addEventListener("pointerdown", onDown, true);
        gone.observe(document.body, { childList: true, subtree: true });
        standAside = () => {
          cancelAnimationFrame(frame);
          document.removeEventListener("pointerdown", onDown, true);
          gone.disconnect();
        };
      }
    };

    // A centred coachmark has nothing to point at; the others wait for their anchor.
    if (CENTRED.has(key)) {
      show(undefined);
      return;
    }
    const selector = overview ? ".page-topbar" : `[data-tour="${key}"]`;
    const found = document.querySelector(selector);
    if (found !== null) {
      show(found);
      return () => standAside();
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
    return () => {
      stop();
      standAside();
    };
    // `pulse` and `pathname` only redraw: a report or a new page re-targets.
  }, [step, subStep, anchorKey, failure, providerDefault, hasCredential, pathname, pulse, destroyDriver]);

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

  useEffect(() => {
    if (!running) destroyDriver();
  }, [running, destroyDriver]);
  useEffect(() => destroyDriver, [destroyDriver]);

  return <TourContext.Provider value={api}>{children}</TourContext.Provider>;
}

type Popover = Parameters<NonNullable<NonNullable<Parameters<Driver["highlight"]>[0]["popover"]>["onPopoverRender"]>>[0];

/** Pulses the coachmark, so a click in the wrong place shows where the tour is. */
export function nudgeCoachmark(): void {
  const popover = document.querySelector<HTMLElement>(".driver-popover.vista-tour");
  if (popover === null) return;
  popover.classList.remove("vista-tour-nudge");
  void popover.offsetWidth; // restart the animation when clicked again mid-pulse
  popover.classList.add("vista-tour-nudge");
  window.setTimeout(() => popover.classList.remove("vista-tour-nudge"), 900);
}

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
    primary = button(mark.nextLabel ?? "Next","tour-btn tour-btn-primary", actions.next);
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
