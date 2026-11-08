import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  changedModels,
  chosenModels,
  continueDebate,
  modelsOf,
  noRoleModels,
  openDebate,
  type DebateParticipant,
} from "@/lib/debates";

/*
 * What the Lab sends for a role's model: an open sends the roles that have one,
 * a Continue sends only the roles that changed, and a role returned to the
 * default goes as null.
 */

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(
    async () =>
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function sentBody(): unknown {
  return JSON.parse(fetchMock.mock.calls[0][1].body as string);
}

function participant(role: string, model: string | null): DebateParticipant {
  return {
    id: role,
    run_id: "r1",
    identity: `vista-${role}-1`,
    debate_role: role,
    forum_role: role,
    active: true,
    granted_tools: [],
    model,
  };
}

describe("openDebate", () => {
  it("sends the models the researcher chose, and none for roles on the default", async () => {
    await openDebate("molten-salt", {
      topic: "t",
      rounds: 2,
      models: chosenModels({ ...noRoleModels(), referee: "openai:gpt-5" }),
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/debates?project_name=molten-salt");
    expect(sentBody()).toEqual({ topic: "t", rounds: 2, models: { referee: "openai:gpt-5" } });
  });

  it("sends no models when every role is on the default", async () => {
    await openDebate("molten-salt", { topic: "t", models: chosenModels(noRoleModels()) });
    expect(sentBody()).toEqual({ topic: "t", models: {} });
  });
});

describe("continueDebate", () => {
  const original = modelsOf([
    participant("proposer", null),
    participant("reviewer", "openai:gpt-5-mini"),
    participant("referee", "openai:gpt-5"),
  ]);

  it("sends only the roles that changed", async () => {
    const current = { ...original, reviewer: "openai:gpt-5" };
    await continueDebate("molten-salt", "r1", 3, changedModels(current, original));
    expect(sentBody()).toEqual({ rounds: 3, models: { reviewer: "openai:gpt-5" } });
  });

  it("sends a role returned to the default as null", async () => {
    const current = { ...original, referee: null };
    await continueDebate("molten-salt", "r1", 3, changedModels(current, original));
    expect(sentBody()).toEqual({ rounds: 3, models: { referee: null } });
  });

  it("sends no models when the form is left alone", async () => {
    await continueDebate("molten-salt", "r1", 3, changedModels(original, original));
    expect(sentBody()).toEqual({ rounds: 3, models: {} });
  });
});

describe("modelsOf", () => {
  it("reads each role's recorded model, and null for a role on the default", () => {
    expect(
      modelsOf([participant("proposer", null), participant("referee", "openai:gpt-5")]),
    ).toEqual({ proposer: null, reviewer: null, referee: "openai:gpt-5" });
  });
});
