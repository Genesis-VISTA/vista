/**
 * The first-boot tour's step logic: which step, and which coachmark within it,
 * is showing, worked out from what the app already knows. Pure, so every rule
 * about the tour's order is tested here without a DOM (design.md §1).
 */

import type { OnboardingTourState } from "./settings";

/** The tour's steps, in the order the spec gives them. */
export const TOUR_STEPS = [
  "welcome",
  "project",
  "model",
  "knowledge-bases",
  "skills",
  "files",
  "hpc",
  "first-question",
] as const;

export type TourStepId = (typeof TOUR_STEPS)[number];

/**
 * Each step's coachmarks, in order. A step is done when every one of them is:
 * by app state, by an event, or by the researcher's Next.
 */
export const TOUR_SUB_STEPS = {
  welcome: ["intro", "sidebar"],
  project: ["overview", "card", "new", "open"],
  model: ["overview", "composer", "panel", "picker", "provider", "providers", "credential", "pick", "set"],
  "knowledge-bases": ["visit", "overview", "list", "open-kb", "about", "papers", "add-files"],
  skills: ["visit", "overview", "loaded", "available", "open-skill", "detail", "load", "import"],
  files: ["visit", "overview", "outputs", "uploads", "open-chart", "chart-preview", "open-report", "review"],
  hpc: ["resources", "open-card", "card", "check", "settings"],
  "first-question": ["send", "activity", "answer", "finish"],
} as const satisfies Record<TourStepId, readonly string[]>;

export type TourSubStep<S extends TourStepId = TourStepId> = (typeof TOUR_SUB_STEPS)[S][number];

/** What pages report through `useTourEvent()` when the researcher does a step's action. */
export type TourEvent =
  | "kb-opened"
  | "skill-opened"
  | "skill-closed"
  | "example-image-opened"
  | "example-md-opened"
  | "hpc-card-opened"
  | "turn-started"
  | "turn-succeeded";

/**
 * Why the first question did not get an answer, as the chat page tells the
 * tour: no working credential (the backend refused the turn for want of one),
 * no model chosen, or anything else that went wrong.
 */
export type TourFailure = "credential" | "model" | "other";

/** A sub-step passed with Next, as `"<step>:<sub-step>"`, e.g. `"skills:load"`. */
export type Acknowledgement = `${TourStepId}:${string}`;

export type TourInput = {
  /** The current page's path, from `usePathname()`. */
  pathname: string;
  /** What `/settings` holds; null when the tour never started. */
  stored: OnboardingTourState | null;
  /** From `useAgentSettings()`, once it has loaded. */
  inference: {
    hasCredential: boolean;
    /** The model in effect, `provider:name`; null when there is none. */
    model: string | null;
    /** True when `model` is only the provider's default, not the researcher's choice. */
    modelIsDefault: boolean;
    /** Not used to decide completion; carried so the coachmark can name the suggestion. */
    providerDefault: string | null;
  };
  /** The active project's name, or null. */
  activeProject: string | null;
  /** Actions seen this session. */
  events: ReadonlySet<TourEvent>;
  /** Steps passed with Skip this step. */
  skipped: ReadonlySet<TourStepId>;
  /** Sub-steps passed with Next. */
  acknowledged: ReadonlySet<Acknowledgement>;
};

export type TourPosition = { step: TourStepId; subStep: string };

/** The page each step's `visit` coachmark sends the researcher to. */
const STEP_PAGES: Partial<Record<TourStepId, string>> = {
  "knowledge-bases": "/knowledge-bases",
  skills: "/skills",
  files: "/files",
};

/** Whether one coachmark is done: by app state, by an event, or by Next. */
function isSubStepDone(step: TourStepId, sub: string, input: TourInput): boolean {
  const key = `${step}:${sub}`;
  switch (key) {
    case "project:overview":
    case "project:card":
    case "project:new":
      // A project already open skips explaining how to pick one.
      return input.activeProject !== null || input.acknowledged.has(key as Acknowledgement);
    case "project:open":
      return input.activeProject !== null;
    case "model:provider":
    case "model:providers":
      // A researcher with a credential has no use for the provider settings.
      return input.inference.hasCredential || input.acknowledged.has(key as Acknowledgement);
    case "model:credential":
      return input.inference.hasCredential;
    case "model:pick":
      return input.inference.model !== null && !input.inference.modelIsDefault;
    case "knowledge-bases:visit":
    case "skills:visit":
    case "files:visit":
      return input.pathname === STEP_PAGES[step];
    case "knowledge-bases:open-kb":
      return input.events.has("kb-opened");
    case "skills:open-skill":
      return input.events.has("skill-opened");
    case "skills:detail":
      return input.events.has("skill-closed");
    case "files:open-chart":
      return input.events.has("example-image-opened");
    case "files:open-report":
      return input.events.has("example-md-opened");
    case "hpc:open-card":
      return input.events.has("hpc-card-opened");
    case "first-question:send":
      return input.events.has("turn-started");
    case "first-question:activity":
      return input.events.has("turn-succeeded");
    default:
      // Everything else is passed with Next.
      return input.acknowledged.has(key as Acknowledgement);
  }
}

/**
 * The coachmark to show, or null when the tour is closed (finished or exited).
 */
export function nextTourStep(input: TourInput): TourPosition | null {
  let stepIndex = 0;
  if (input.stored !== null) {
    if (input.stored.status === "exited" || input.stored.status === "finished") {
      return null;
    }
    // A step id this version doesn't know starts the tour over.
    stepIndex = Math.max(TOUR_STEPS.indexOf(input.stored.step as TourStepId), 0);
  }

  const restSteps = TOUR_STEPS.slice(stepIndex);
  for (let i = 0; i < restSteps.length; i++) {
    let j = 0;
    const curStep = restSteps[i];
    if (input.skipped.has(curStep)) continue;
    const curSubSteps = TOUR_SUB_STEPS[curStep];
    while (j < curSubSteps.length && isSubStepDone(curStep, curSubSteps[j], input)) j++;
    if (j !== curSubSteps.length) return { step: curStep, subStep: curSubSteps[j] };
  }

  return null;
}

/** The sub-steps the researcher completes by doing something (`visit` is going to a page), so they have no Next. */
const ACTION_SUB_STEPS: ReadonlySet<string> = new Set([
  "project:open",
  "model:credential",
  "model:pick",
  "knowledge-bases:visit",
  "knowledge-bases:open-kb",
  "skills:visit",
  "skills:open-skill",
  "skills:detail",
  "files:visit",
  "files:open-chart",
  "files:open-report",
  "hpc:open-card",
  "first-question:send",
  "first-question:activity",
]);

/**
 * Whether a coachmark is passed with Next. The others wait for the researcher's
 * action (or app state), so their coachmark offers only Skip this step and Exit.
 */
export function completesOnNext(step: TourStepId, subStep: string): boolean {
  return !ACTION_SUB_STEPS.has(`${step}:${subStep}`);
}

/**
 * The failure a refused send stands for, from the backend's own refusal body:
 * `inference_not_configured` is a missing credential, and any other refusal
 * is something the researcher can only retry. Pure, so the mapping is tested
 * without the chat page.
 */
export function tourFailureForRefusal(body: unknown): TourFailure {
  const code = typeof body === "object" && body !== null ? (body as { code?: unknown }).code : null;
  return code === "inference_not_configured" ? "credential" : "other";
}

/** Coachmarks that point at an element another coachmark's anchor already names. */
const ANCHOR_ALIASES: Record<string, string> = {
  "model:composer": "first-question:send",
  "model:picker": "model:pick",
  "model:set": "model:pick",
  "first-question:activity": "model:panel",
  "files:chart-preview": "files:preview",
  "files:review": "files:preview",
};

/**
 * The `data-tour` value a coachmark points at. `"<step>:<sub-step>"`, except
 * where one element serves two coachmarks (`ANCHOR_ALIASES`), and on the first
 * question after a failure, where it points at what fixes it: the settings
 * button for a credential, the model picker for a model.
 */
export function tourAnchor(position: TourPosition, failure: TourFailure | null): string {
  const key = `${position.step}:${position.subStep}`;
  if (key === "first-question:send") {
    if (failure === "credential") return "first-question:settings";
    if (failure === "model") return "model:pick";
  }
  return ANCHOR_ALIASES[key] ?? key;
}
