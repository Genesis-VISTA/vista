import { describe, expect, it } from "vitest";
import { PUBLIC_TEAM_ID } from "@/lib/collaboration";
import { TOUR_STEPS, TOUR_SUB_STEPS } from "@/lib/onboarding";
import {
  EXAMPLE_FILES,
  EXAMPLE_HUB_SKILLS,
  EXAMPLE_LOCAL_SKILLS,
  EXAMPLE_TEAM_ID,
  EXAMPLE_TEAMS,
  isExample,
  showsTourExamples,
} from "@/lib/onboarding-examples";

describe("the collaboration step's examples", () => {
  it("has Public and Example team as its teams, never a real team's id", () => {
    expect(EXAMPLE_TEAMS.map((t) => [t.name, t.public])).toEqual([
      ["Public", true],
      ["Example team", false],
    ]);
    for (const t of EXAMPLE_TEAMS) expect(t.id).not.toBe(PUBLIC_TEAM_ID);
  });

  it("puts plot-results in Public and batch-job-submit in Example team, each carrying its team's name", () => {
    const byId = new Map(EXAMPLE_TEAMS.map((t) => [t.id, t.name]));
    expect(EXAMPLE_HUB_SKILLS.map((s) => [s.name, s.team_name])).toEqual([
      ["plot-results", "Public"],
      ["batch-job-submit", "Example team"],
    ]);
    for (const s of EXAMPLE_HUB_SKILLS) {
      expect(byId.get(s.team_id)).toBe(s.team_name);
      expect(s.local).toBeNull();
      expect(s.name_taken).toBe(false);
    }
  });

  it("has a private, unpublished csv-cleanup and a changed run-summary in Example team", () => {
    const [csv, summary] = EXAMPLE_LOCAL_SKILLS;
    expect(csv).toMatchObject({ slug: "csv-cleanup", teamId: null, teamName: null, remoteId: null });
    expect(summary).toMatchObject({
      slug: "run-summary",
      teamId: EXAMPLE_TEAM_ID,
      teamName: "Example team",
      remoteVersion: 2,
      modified: true,
      sharing: "Changed here since version 2",
    });
  });

  it("marks every one of them as an example, and nothing else", () => {
    for (const e of [...EXAMPLE_TEAMS, ...EXAMPLE_HUB_SKILLS, ...EXAMPLE_LOCAL_SKILLS]) expect(isExample(e)).toBe(true);
    expect(isExample({ slug: "csv-cleanup" })).toBe(false);
    expect(isExample(null)).toBe(false);
    expect(isExample(EXAMPLE_FILES[0])).toBe(false);
  });

  it("shows only during the collaboration step", () => {
    expect(showsTourExamples(null)).toBe(false);
    for (const step of TOUR_STEPS) {
      for (const subStep of TOUR_SUB_STEPS[step]) {
        expect(showsTourExamples({ step, subStep }), `${step}:${subStep}`).toBe(step === "collaboration");
      }
    }
  });
});
