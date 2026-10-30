import { describe, expect, it } from "vitest";
import {
  completesOnNext,
  nextTourStep,
  TOUR_STEPS,
  TOUR_SUB_STEPS,
  tourAnchor,
  tourFailureForRefusal,
  type Acknowledgement,
  type TourEvent,
  type TourInput,
  type TourStepId,
} from "@/lib/onboarding";
import { coachmarkFor } from "@/lib/onboarding-copy";

/** A fresh install on AmSC MAG: no credential, no project, nothing done yet. */
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

const acks = (...keys: Acknowledgement[]) => new Set<Acknowledgement>(keys);
const events = (...names: TourEvent[]) => new Set<TourEvent>(names);

describe("nextTourStep", () => {
  it("starts a never-started tour at the welcome step, then points at the sidebar", () => {
    expect(nextTourStep(input())).toEqual({ step: "welcome", subStep: "intro" });
    const out = nextTourStep(input({ acknowledged: acks("welcome:intro") }));
    expect(out).toEqual({ step: "welcome", subStep: "sidebar" });
  });

  it("explains the Projects screen before the open step", () => {
    const p = at("project");
    expect(nextTourStep(input(p))).toEqual({ step: "project", subStep: "overview" });
    expect(nextTourStep(input({ ...p, acknowledged: acks("project:overview") }))).toEqual({
      step: "project",
      subStep: "card",
    });
    expect(nextTourStep(input({ ...p, acknowledged: acks("project:overview", "project:card") }))).toEqual({
      step: "project",
      subStep: "new",
    });
    const all = acks("project:overview", "project:card", "project:new");
    expect(nextTourStep(input({ ...p, acknowledged: all }))).toEqual({ step: "project", subStep: "open" });
  });

  it("skips the whole project step when a project is already open", () => {
    const out = nextTourStep(input({ ...at("project"), activeProject: "ai-safety-autonomous-labs" }));
    expect(out).toEqual({ step: "model", subStep: "overview" });
  });

  it("walks the chat before the provider settings, which a credential skips", () => {
    const base = { ...at("model"), activeProject: "ai-safety-autonomous-labs" };
    const seen = acks("model:overview", "model:composer", "model:panel", "model:picker");
    expect(nextTourStep(input({ ...base, acknowledged: seen }))).toEqual({ step: "model", subStep: "provider" });
    const provider = acks(...seen, "model:provider");
    expect(nextTourStep(input({ ...base, acknowledged: provider }))).toEqual({ step: "model", subStep: "providers" });
    const providers = acks(...provider, "model:providers");
    expect(nextTourStep(input({ ...base, acknowledged: providers }))).toEqual({ step: "model", subStep: "credential" });

    const withKey = {
      ...base,
      acknowledged: seen,
      inference: { hasCredential: true, model: "openai:claude-sonnet", modelIsDefault: true, providerDefault: "claude-sonnet" },
    };
    expect(nextTourStep(input(withKey))).toEqual({ step: "model", subStep: "pick" });
  });

  it("keeps MAG on the picker while only the default model is in effect", () => {
    const out = nextTourStep(
      input({
        ...at("model"),
        activeProject: "ai-safety-autonomous-labs",
        acknowledged: acks("model:overview", "model:composer", "model:panel", "model:picker"),
        inference: { hasCredential: true, model: "openai:claude-sonnet", modelIsDefault: true, providerDefault: "claude-sonnet" },
      }),
    );
    expect(out).toEqual({ step: "model", subStep: "pick" });
  });

  it("confirms a chosen model with Set, then finishes the model step", () => {
    const base = {
      ...at("model"),
      activeProject: "ai-safety-autonomous-labs",
      acknowledged: acks("model:overview", "model:composer", "model:panel", "model:picker"),
      inference: { hasCredential: true, model: "openai:llama-4", modelIsDefault: false, providerDefault: "gpt-oss-120b" },
    };
    expect(nextTourStep(input(base))).toEqual({ step: "model", subStep: "set" });
    const set = input({ ...base, acknowledged: acks(...base.acknowledged, "model:set") });
    expect(nextTourStep(set)).toEqual({ step: "knowledge-bases", subStep: "visit" });
  });

  it("explains the knowledge bases screen, its list, then the open one", () => {
    const kb = { ...at("knowledge-bases"), pathname: "/knowledge-bases" };
    expect(nextTourStep(input(kb))).toEqual({ step: "knowledge-bases", subStep: "overview" });
    const overview = acks("knowledge-bases:overview");
    expect(nextTourStep(input({ ...kb, acknowledged: overview }))).toEqual({ step: "knowledge-bases", subStep: "list" });
    const list = acks("knowledge-bases:overview", "knowledge-bases:list");
    expect(nextTourStep(input({ ...kb, acknowledged: list }))).toEqual({ step: "knowledge-bases", subStep: "open-kb" });
    const opened = input({ ...kb, acknowledged: list, events: events("kb-opened") });
    expect(nextTourStep(opened)).toEqual({ step: "knowledge-bases", subStep: "about" });
  });

  it.each([
    ["knowledge-bases", "/knowledge-bases"],
    ["skills", "/skills"],
    ["files", "/files"],
  ] as const)("takes a visit to %s as done on its page only, then shows the overview", (step, page) => {
    expect(nextTourStep(input({ ...at(step), pathname: "/" }))).toEqual({ step, subStep: "visit" });
    expect(nextTourStep(input({ ...at(step), pathname: `${page}/x` }))).toEqual({ step, subStep: "visit" });
    expect(nextTourStep(input({ ...at(step), pathname: page }))).toEqual({ step, subStep: "overview" });
  });

  it("resumes at the page's overview when the researcher is already on it", () => {
    expect(nextTourStep(input({ ...at("skills"), pathname: "/skills" }))).toEqual({
      step: "skills",
      subStep: "overview",
    });
  });

  it("opens a skill, then waits for it to be closed", () => {
    const base = {
      ...at("skills"),
      pathname: "/skills",
      acknowledged: acks("skills:overview", "skills:loaded", "skills:available"),
    };
    expect(nextTourStep(input(base))).toEqual({ step: "skills", subStep: "open-skill" });
    // Reporting on open does not skip ahead: the detail is explained first.
    expect(nextTourStep(input({ ...base, events: events("skill-opened") }))).toEqual({
      step: "skills",
      subStep: "detail",
    });
    expect(nextTourStep(input({ ...base, events: events("skill-opened", "skill-closed") }))).toEqual({
      step: "skills",
      subStep: "load",
    });
  });

  it("moves past a skipped step", () => {
    const out = nextTourStep(input({ ...at("knowledge-bases"), skipped: new Set<TourStepId>(["knowledge-bases"]) }));
    expect(out).toEqual({ step: "skills", subStep: "visit" });
  });

  it("completes each example's coachmark by opening that example", () => {
    const onFiles = {
      ...at("files"),
      pathname: "/files",
      acknowledged: acks("files:overview", "files:outputs", "files:uploads"),
    };
    expect(nextTourStep(input(onFiles))).toEqual({ step: "files", subStep: "open-chart" });
    const chart = input({ ...onFiles, events: events("example-image-opened") });
    expect(nextTourStep(chart)).toEqual({ step: "files", subStep: "chart-preview" });
    const previewed = acks(...onFiles.acknowledged, "files:chart-preview");
    expect(nextTourStep(input({ ...onFiles, acknowledged: previewed, events: events("example-image-opened") }))).toEqual({
      step: "files",
      subStep: "open-report",
    });
    const both = events("example-image-opened", "example-md-opened");
    expect(nextTourStep(input({ ...onFiles, acknowledged: previewed, events: both }))).toEqual({
      step: "files",
      subStep: "review",
    });
    const reviewed = acks(...previewed, "files:review");
    expect(nextTourStep(input({ ...onFiles, acknowledged: reviewed, events: both }))).toEqual({
      step: "hpc",
      subStep: "resources",
    });
  });

  it("walks the cluster list, the open card, one check, then Settings", () => {
    const hpc = at("hpc");
    expect(nextTourStep(input(hpc))).toEqual({ step: "hpc", subStep: "resources" });
    const resources = { ...hpc, acknowledged: acks("hpc:resources") };
    expect(nextTourStep(input(resources))).toEqual({ step: "hpc", subStep: "open-card" });
    const opened = { ...resources, events: events("hpc-card-opened") };
    expect(nextTourStep(input(opened))).toEqual({ step: "hpc", subStep: "card" });
    expect(nextTourStep(input({ ...opened, acknowledged: acks("hpc:resources", "hpc:card") }))).toEqual({
      step: "hpc",
      subStep: "check",
    });
    expect(
      nextTourStep(input({ ...opened, acknowledged: acks("hpc:resources", "hpc:card", "hpc:check") })),
    ).toEqual({ step: "hpc", subStep: "settings" });
  });

  it("follows the first question from send, through the agent at work, to the answer", () => {
    const q = at("first-question");
    expect(nextTourStep(input(q))).toEqual({ step: "first-question", subStep: "send" });
    expect(nextTourStep(input({ ...q, events: events("turn-started") }))).toEqual({
      step: "first-question",
      subStep: "activity",
    });
    const answered = input({ ...q, events: events("turn-started", "turn-succeeded") });
    expect(nextTourStep(answered)).toEqual({ step: "first-question", subStep: "answer" });
    const read = input({
      ...q,
      events: events("turn-started", "turn-succeeded"),
      acknowledged: acks("first-question:answer"),
    });
    expect(nextTourStep(read)).toEqual({ step: "first-question", subStep: "finish" });
  });

  it("returns to send when a failure drops turn-started", () => {
    // What the provider leaves in the session's events after a failure.
    expect(nextTourStep(input({ ...at("first-question"), events: events() }))).toEqual({
      step: "first-question",
      subStep: "send",
    });
  });

  it("returns null once the last coachmark is acknowledged", () => {
    const out = nextTourStep(
      input({
        ...at("first-question"),
        events: events("turn-started", "turn-succeeded"),
        acknowledged: acks("first-question:answer", "first-question:finish"),
      }),
    );
    expect(out).toBeNull();
  });

  it("returns null for an exited or finished tour", () => {
    expect(nextTourStep(input({ stored: { status: "exited", step: "skills" } }))).toBeNull();
    expect(nextTourStep(input({ stored: { status: "finished", step: "first-question" } }))).toBeNull();
  });

  it.each(TOUR_STEPS)("resumes an in-progress tour at the %s step", (step) => {
    const out = nextTourStep(input(at(step)));
    expect(out?.step).toBe(step);
    expect(out?.subStep).toBe(TOUR_SUB_STEPS[step][0]);
  });

  it("starts over when the stored step is one this version doesn't know", () => {
    const out = nextTourStep(input({ stored: { status: "in_progress", step: "retired-step" } }));
    expect(out).toEqual({ step: "welcome", subStep: "intro" });
  });

  it("moves past the welcome on Next even before any state is stored", () => {
    const out = nextTourStep(input({ acknowledged: acks("welcome:intro", "welcome:sidebar") }));
    expect(out).toEqual({ step: "project", subStep: "overview" });
  });
});

describe("the script", () => {
  it("has copy for every coachmark, with and without a credential and a suggestion", () => {
    for (const step of TOUR_STEPS) {
      for (const sub of TOUR_SUB_STEPS[step]) {
        for (const hasCredential of [true, false]) {
          for (const providerDefault of ["claude-sonnet", null]) {
            const mark = coachmarkFor(step, sub, { providerDefault, hasCredential });
            expect(mark, `${step}:${sub}`).not.toBeNull();
            expect(mark!.title, `${step}:${sub}`).not.toBe("");
            expect(mark!.body, `${step}:${sub}`).not.toBe("");
          }
        }
      }
    }
  });

  it("labels the welcome and the last coachmark's button, and no other", () => {
    const labelled: string[] = [];
    for (const step of TOUR_STEPS) {
      for (const sub of TOUR_SUB_STEPS[step]) {
        const mark = coachmarkFor(step, sub, { providerDefault: null, hasCredential: false });
        if (mark?.nextLabel) labelled.push(`${step}:${sub}=${mark.nextLabel}`);
      }
    }
    expect(labelled).toEqual(["welcome:intro=Start the tour", "first-question:finish=Done"]);
  });

  it("words the picker by whether a credential is there", () => {
    const without = coachmarkFor("model", "picker", { providerDefault: null, hasCredential: false });
    const withKey = coachmarkFor("model", "picker", { providerDefault: null, hasCredential: true });
    expect(without?.body).toContain("First, VISTA needs a model provider.");
    expect(withKey?.body).not.toContain("First, VISTA needs a model provider.");
  });

  it("names the provider's suggestion when picking, and none for Custom", () => {
    const named = coachmarkFor("model", "pick", { providerDefault: "claude-sonnet", hasCredential: true });
    expect(named?.body).toBe("Suggested: claude-sonnet. Any model on the list works.");
    const custom = coachmarkFor("model", "pick", { providerDefault: null, hasCredential: true });
    expect(custom?.body).not.toContain("Suggested");
  });

  it("links the documentation from the last coachmark", () => {
    const mark = coachmarkFor("first-question", "finish", { providerDefault: null, hasCredential: true });
    expect(mark?.link?.href).toBe("https://genesis-vista.github.io/");
    expect(mark?.body).not.toContain("genesis-vista.github.io");
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
      ["skills", "detail"],
      ["files", "visit"],
      ["files", "open-chart"],
      ["files", "open-report"],
      ["hpc", "open-card"],
      ["first-question", "send"],
      ["first-question", "activity"],
    ];
    for (const [step, sub] of actions) expect(completesOnNext(step, sub)).toBe(false);
  });

  it("is true for the rest", () => {
    expect(completesOnNext("welcome", "intro")).toBe(true);
    expect(completesOnNext("welcome", "sidebar")).toBe(true);
    for (const step of ["project", "model", "knowledge-bases", "skills", "files"] as const) {
      expect(completesOnNext(step, "overview")).toBe(true);
    }
    expect(completesOnNext("model", "provider")).toBe(true);
    expect(completesOnNext("model", "set")).toBe(true);
    expect(completesOnNext("knowledge-bases", "add-files")).toBe(true);
    expect(completesOnNext("skills", "load")).toBe(true);
    expect(completesOnNext("skills", "import")).toBe(true);
    expect(completesOnNext("hpc", "resources")).toBe(true);
    expect(completesOnNext("hpc", "card")).toBe(true);
    expect(completesOnNext("hpc", "settings")).toBe(true);
    expect(completesOnNext("first-question", "answer")).toBe(true);
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

  it("shares one element between two coachmarks where the script says so", () => {
    expect(tourAnchor({ step: "model", subStep: "composer" }, null)).toBe("first-question:send");
    expect(tourAnchor({ step: "model", subStep: "picker" }, null)).toBe("model:pick");
    expect(tourAnchor({ step: "model", subStep: "set" }, null)).toBe("model:pick");
    expect(tourAnchor({ step: "first-question", subStep: "activity" }, null)).toBe("model:panel");
    expect(tourAnchor({ step: "files", subStep: "chart-preview" }, null)).toBe("files:preview");
    expect(tourAnchor({ step: "files", subStep: "review" }, null)).toBe("files:preview");
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
