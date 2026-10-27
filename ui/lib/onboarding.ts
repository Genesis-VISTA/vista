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
  welcome: ["intro"],
  project: ["open"],
  model: ["credential", "pick"],
  "knowledge-bases": ["open-kb", "add-files"],
  skills: ["open-skill", "load", "import"],
  files: ["open-examples", "review"],
  hpc: ["open-card", "settings"],
  "first-question": ["send", "finish"],
} as const satisfies Record<TourStepId, readonly string[]>;

export type TourSubStep<S extends TourStepId = TourStepId> = (typeof TOUR_SUB_STEPS)[S][number];

/** What pages report through `useTourEvent()` when the researcher does a step's action. */
export type TourEvent =
  | "kb-opened"
  | "skill-opened"
  | "example-image-opened"
  | "example-md-opened"
  | "hpc-card-opened"
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

/** Whether one coachmark is done: by app state, by an event, or by Next. */
function isSubStepDone(step: TourStepId, sub: string, input: TourInput): boolean {
  const key = `${step}:${sub}`;
  switch (key) {
    case "project:open":
      return input.activeProject !== null;
    case "model:credential":
      return input.inference.hasCredential;
    case "model:pick":
      return input.inference.model !== null && !input.inference.modelIsDefault;
    case "knowledge-bases:open-kb":
      return input.events.has("kb-opened");
    case "skills:open-skill":
      return input.events.has("skill-opened");
    case "files:open-examples":
      return input.events.has("example-image-opened") && input.events.has("example-md-opened");
    case "hpc:open-card":
      return input.events.has("hpc-card-opened");
    case "first-question:send":
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

/** The sub-steps the researcher completes by doing something, so they have no Next. */
const ACTION_SUB_STEPS: ReadonlySet<string> = new Set([
  "project:open",
  "model:credential",
  "model:pick",
  "knowledge-bases:open-kb",
  "skills:open-skill",
  "files:open-examples",
  "hpc:open-card",
  "first-question:send",
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

/**
 * The `data-tour` value a coachmark points at. Always `"<step>:<sub-step>"`,
 * except on the first question after a failure, where it points at what fixes
 * it: the settings button for a credential, the model picker for a model.
 */
export function tourAnchor(position: TourPosition, failure: TourFailure | null): string {
  const key = `${position.step}:${position.subStep}`;
  if (key === "first-question:send") {
    if (failure === "credential") return "first-question:settings";
    if (failure === "model") return "model:pick";
  }
  return key;
}
