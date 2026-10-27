import { describe, expect, it } from "vitest";
import {
  completesOnNext,
  nextTourStep,
  tourAnchor,
  tourFailureForRefusal,
  type Acknowledgement,
  type TourEvent,
  type TourInput,
  type TourStepId,
} from "@/lib/onboarding";

/** A fresh install on AmSC i2: no credential, no project, nothing done yet. */
function input(over: Partial<TourInput> = {}): TourInput {
  return {
    pathname: "/",
    stored: null,
    inference: { hasCredential: false, model: "openai:claude-sonnet", modelIsDefault: true, providerDefault: "claude-sonnet" },
    activeProject: null,
    events: new Set<TourEvent>(),
    skipped: new Set<TourStepId>(),
    acknowledged: new Set<Acknowledgement>(),
    ...over,
  };
}

/** Resume at `step`, as a researcher who reached it in an earlier session. */
const at = (step: TourStepId) => ({ stored: { status: "in_progress" as const, step } });

describe("nextTourStep", () => {
  it("starts a never-started tour at the welcome step", () => {
    expect(nextTourStep(input())).toEqual({ step: "welcome", subStep: "intro" });
  });

  it("still shows the projects overview when a project is already active, then completes the step", () => {
    const active = { ...at("project"), activeProject: "ai-safety-autonomous-labs" };
    expect(nextTourStep(input(active))).toEqual({ step: "project", subStep: "overview" });
    const out = nextTourStep(input({ ...active, acknowledged: new Set<Acknowledgement>(["project:overview"]) }));
    expect(out).toEqual({ step: "model", subStep: "credential" });
  });

  it("keeps i2 on the picker while only the default model is in effect", () => {
    const out = nextTourStep(
      input({
        ...at("model"),
        activeProject: "ai-safety-autonomous-labs",
        inference: { hasCredential: true, model: "openai:claude-sonnet", modelIsDefault: true, providerDefault: "claude-sonnet" },
      }),
    );
    expect(out).toEqual({ step: "model", subStep: "pick" });
  });

  it("completes the model step at once on OLCF with a credential and a chosen model", () => {
    const out = nextTourStep(
      input({
        ...at("model"),
        activeProject: "ai-safety-autonomous-labs",
        inference: { hasCredential: true, model: "openai:llama-4", modelIsDefault: false, providerDefault: "gpt-oss-120b" },
      }),
    );
    expect(out).toEqual({ step: "knowledge-bases", subStep: "visit" });
  });

  it.each([
    ["knowledge-bases", "/knowledge-bases"],
    ["skills", "/skills"],
    ["files", "/files"],
  ] as const)("takes a visit to %s as done on its page only, then shows the overview before its controls", (step, page) => {
    expect(nextTourStep(input({ ...at(step), pathname: "/" }))).toEqual({ step, subStep: "visit" });
    expect(nextTourStep(input({ ...at(step), pathname: `${page}/x` }))).toEqual({ step, subStep: "visit" });
    expect(nextTourStep(input({ ...at(step), pathname: page }))).toEqual({ step, subStep: "overview" });
    const out = nextTourStep(
      input({ ...at(step), pathname: page, acknowledged: new Set<Acknowledgement>([`${step}:overview`]) }),
    );
    expect(out?.step).toBe(step);
    expect(out?.subStep).toMatch(/^open-/);
  });

  it("resumes at the page's overview when the researcher is already on it", () => {
    expect(nextTourStep(input({ ...at("skills"), pathname: "/skills" }))).toEqual({
      step: "skills",
      subStep: "overview",
    });
  });

  it("moves past a skipped step", () => {
    const out = nextTourStep(input({ ...at("knowledge-bases"), skipped: new Set<TourStepId>(["knowledge-bases"]) }));
    expect(out).toEqual({ step: "skills", subStep: "visit" });
  });

  it("needs both example files opened to finish the files step", () => {
    const onFiles = { ...at("files"), pathname: "/files", acknowledged: new Set<Acknowledgement>(["files:overview"]) };
    const one = nextTourStep(input({ ...onFiles, events: new Set<TourEvent>(["example-image-opened"]) }));
    expect(one).toEqual({ step: "files", subStep: "open-examples" });
    const both = nextTourStep(
      input({ ...onFiles, events: new Set<TourEvent>(["example-image-opened", "example-md-opened"]) }),
    );
    expect(both).toEqual({ step: "files", subStep: "review" });
    const reviewed = nextTourStep(
      input({
        ...onFiles,
        events: new Set<TourEvent>(["example-image-opened", "example-md-opened"]),
        acknowledged: new Set<Acknowledgement>(["files:overview", "files:review"]),
      }),
    );
    expect(reviewed).toEqual({ step: "hpc", subStep: "open-card" });
  });

  it("finishes the first question only on a successful turn", () => {
    expect(nextTourStep(input({ ...at("first-question") }))).toEqual({ step: "first-question", subStep: "overview" });
    const overviewed = { ...at("first-question"), acknowledged: new Set<Acknowledgement>(["first-question:overview"]) };
    expect(nextTourStep(input(overviewed))).toEqual({ step: "first-question", subStep: "send" });
    const answered = nextTourStep(input({ ...overviewed, events: new Set<TourEvent>(["turn-succeeded"]) }));
    expect(answered).toEqual({ step: "first-question", subStep: "finish" });
  });

  it("returns null once the last coachmark is acknowledged", () => {
    const out = nextTourStep(
      input({
        ...at("first-question"),
        events: new Set<TourEvent>(["turn-succeeded"]),
        acknowledged: new Set<Acknowledgement>(["first-question:overview", "first-question:finish"]),
      }),
    );
    expect(out).toBeNull();
  });

  it("returns null for an exited or finished tour", () => {
    expect(nextTourStep(input({ stored: { status: "exited", step: "skills" } }))).toBeNull();
    expect(nextTourStep(input({ stored: { status: "finished", step: "first-question" } }))).toBeNull();
  });

  it("resumes an in-progress tour at the step it reached", () => {
    expect(nextTourStep(input({ ...at("skills") }))).toEqual({ step: "skills", subStep: "visit" });
  });

  it("starts over when the stored step is one this version doesn't know", () => {
    const out = nextTourStep(input({ stored: { status: "in_progress", step: "retired-step" } }));
    expect(out).toEqual({ step: "welcome", subStep: "intro" });
  });

  it("moves past welcome on Next even before any state is stored", () => {
    const out = nextTourStep(input({ acknowledged: new Set<Acknowledgement>(["welcome:intro"]) }));
    expect(out).toEqual({ step: "project", subStep: "overview" });
  });
});

describe("completesOnNext", () => {
  it("is false for the action sub-steps, visits included", () => {
    const actions: [TourStepId, string][] = [
      ["project", "open"],
      ["model", "credential"],
      ["model", "pick"],
      ["knowledge-bases", "visit"],
      ["knowledge-bases", "open-kb"],
      ["skills", "visit"],
      ["skills", "open-skill"],
      ["files", "visit"],
      ["files", "open-examples"],
      ["hpc", "open-card"],
      ["first-question", "send"],
    ];
    for (const [step, sub] of actions) expect(completesOnNext(step, sub)).toBe(false);
  });

  it("is true for the rest", () => {
    expect(completesOnNext("welcome", "intro")).toBe(true);
    for (const step of ["project", "knowledge-bases", "skills", "files", "first-question"] as const) {
      expect(completesOnNext(step, "overview")).toBe(true);
    }
    expect(completesOnNext("knowledge-bases", "add-files")).toBe(true);
    expect(completesOnNext("skills", "load")).toBe(true);
    expect(completesOnNext("skills", "import")).toBe(true);
    expect(completesOnNext("hpc", "settings")).toBe(true);
    expect(completesOnNext("first-question", "finish")).toBe(true);
  });
});

describe("tourFailureForRefusal", () => {
  it("takes inference_not_configured for a missing credential", () => {
    expect(tourFailureForRefusal({ detail: "No inference API key.", code: "inference_not_configured" })).toBe(
      "credential",
    );
  });

  it.each([null, "text", {}, { code: "run_busy" }, { detail: "boom" }])(
    "takes anything else (%j) for a failure to retry",
    (body) => {
      expect(tourFailureForRefusal(body)).toBe("other");
    },
  );
});

describe("tourAnchor", () => {
  const send = { step: "first-question", subStep: "send" } as const;

  it("is the coachmark's own key", () => {
    expect(tourAnchor(send, null)).toBe("first-question:send");
    expect(tourAnchor({ step: "skills", subStep: "load" }, null)).toBe("skills:load");
  });

  it("points a first-question failure at what fixes it", () => {
    expect(tourAnchor(send, "credential")).toBe("first-question:settings");
    expect(tourAnchor(send, "model")).toBe("model:pick");
    expect(tourAnchor(send, "other")).toBe("first-question:send");
  });

  it("ignores a failure on any other coachmark", () => {
    expect(tourAnchor({ step: "first-question", subStep: "finish" }, "credential")).toBe("first-question:finish");
  });
});
