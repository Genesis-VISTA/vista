import { describe, expect, it } from "vitest";
import {
  nextTourStep,
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
    expect(both).toEqual({ step: "hpc", subStep: "open-card" });
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
});
