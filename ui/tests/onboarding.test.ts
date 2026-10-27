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

  it("completes the project step at once when a project is already active", () => {
    const out = nextTourStep(input({ ...at("project"), activeProject: "ai-safety-autonomous-labs" }));
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
    expect(out).toEqual({ step: "knowledge-bases", subStep: "open-kb" });
  });

  it("moves past a skipped step", () => {
    const out = nextTourStep(input({ ...at("knowledge-bases"), skipped: new Set<TourStepId>(["knowledge-bases"]) }));
    expect(out).toEqual({ step: "skills", subStep: "open-skill" });
  });

  it("needs both example files opened to finish the files step", () => {
    const one = nextTourStep(input({ ...at("files"), events: new Set<TourEvent>(["example-image-opened"]) }));
    expect(one).toEqual({ step: "files", subStep: "open-examples" });
    const both = nextTourStep(
      input({ ...at("files"), events: new Set<TourEvent>(["example-image-opened", "example-md-opened"]) }),
    );
    expect(both).toEqual({ step: "files", subStep: "review" });
    const reviewed = nextTourStep(
      input({
        ...at("files"),
        events: new Set<TourEvent>(["example-image-opened", "example-md-opened"]),
        acknowledged: new Set<Acknowledgement>(["files:review"]),
      }),
    );
    expect(reviewed).toEqual({ step: "hpc", subStep: "open-card" });
  });

  it("finishes the first question only on a successful turn", () => {
    expect(nextTourStep(input({ ...at("first-question") }))).toEqual({ step: "first-question", subStep: "send" });
    const answered = nextTourStep(input({ ...at("first-question"), events: new Set<TourEvent>(["turn-succeeded"]) }));
    expect(answered).toEqual({ step: "first-question", subStep: "finish" });
  });

  it("returns null once the last coachmark is acknowledged", () => {
    const out = nextTourStep(
      input({
        ...at("first-question"),
        events: new Set<TourEvent>(["turn-succeeded"]),
        acknowledged: new Set<Acknowledgement>(["first-question:finish"]),
      }),
    );
    expect(out).toBeNull();
  });

  it("returns null for an exited or finished tour", () => {
    expect(nextTourStep(input({ stored: { status: "exited", step: "skills" } }))).toBeNull();
    expect(nextTourStep(input({ stored: { status: "finished", step: "first-question" } }))).toBeNull();
  });

  it("resumes an in-progress tour at the step it reached", () => {
    expect(nextTourStep(input({ ...at("skills") }))).toEqual({ step: "skills", subStep: "open-skill" });
  });

  it("starts over when the stored step is one this version doesn't know", () => {
    const out = nextTourStep(input({ stored: { status: "in_progress", step: "retired-step" } }));
    expect(out).toEqual({ step: "welcome", subStep: "intro" });
  });

  it("moves past welcome on Next even before any state is stored", () => {
    const out = nextTourStep(input({ acknowledged: new Set<Acknowledgement>(["welcome:intro"]) }));
    expect(out).toEqual({ step: "project", subStep: "open" });
  });
});

describe("completesOnNext", () => {
  it("is false for the eight action sub-steps", () => {
    const actions: [TourStepId, string][] = [
      ["project", "open"],
      ["model", "credential"],
      ["model", "pick"],
      ["knowledge-bases", "open-kb"],
      ["skills", "open-skill"],
      ["files", "open-examples"],
      ["hpc", "open-card"],
      ["first-question", "send"],
    ];
    for (const [step, sub] of actions) expect(completesOnNext(step, sub)).toBe(false);
  });

  it("is true for the rest", () => {
    expect(completesOnNext("welcome", "intro")).toBe(true);
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
