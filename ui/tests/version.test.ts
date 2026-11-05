import { afterEach, describe, expect, it, vi } from "vitest";
import {
  activityLines,
  checksSentence,
  dataResetSentence,
  PENDING_MS,
  progressText,
  refreshVersion,
  resetVersionForTests,
  relativeTime,
  updateAvailable,
  vistaApp,
  type VersionInfo,
} from "@/lib/version";

const NOW = Date.parse("2026-11-04T12:00:00Z");

function info(overrides: Partial<VersionInfo> = {}): VersionInfo {
  return {
    version: "0.2.3",
    commit: "abc",
    checks: "on",
    checked_at: null,
    latest: null,
    notes_url: null,
    data_reset: null,
    ...overrides,
  };
}

describe("updateAvailable", () => {
  it("is true only while checks are on and a newer release was found", () => {
    expect(updateAvailable(null)).toBe(false);
    expect(updateAvailable(info())).toBe(false);
    expect(updateAvailable(info({ latest: "0.2.5" }))).toBe(true);
    expect(updateAvailable(info({ latest: "0.2.5", checks: "off" }))).toBe(false);
    expect(updateAvailable(info({ latest: "0.2.5", checks: "dev_build" }))).toBe(false);
  });
});

describe("checksSentence", () => {
  it("says why nothing is offered", () => {
    expect(checksSentence(info({ checks: "dev_build" }), NOW)).toBe("Update checks are off for development builds.");
    expect(checksSentence(info({ checks: "off" }), NOW)).toBe("Update checks are off.");
    expect(checksSentence(info(), NOW)).toBe("Not checked yet.");
    expect(checksSentence(info({ checked_at: "2026-11-04T11:58:00Z" }), NOW)).toBe(
      "VISTA is up to date. Checked 2 minutes ago.",
    );
  });
});

describe("relativeTime", () => {
  it.each([
    ["2026-11-04T12:00:00Z", "just now"],
    ["2026-11-04T11:59:00Z", "1 minute ago"],
    ["2026-11-04T09:00:00Z", "3 hours ago"],
    ["2026-11-02T12:00:00Z", "2 days ago"],
    ["not a time", "at an unknown time"],
  ])("%s is %s", (iso, text) => {
    expect(relativeTime(iso, NOW)).toBe(text);
  });
});

describe("dataResetSentence", () => {
  it("is exact when the release says, generic when it does not, and absent when no reset", () => {
    expect(dataResetSentence(true)).toMatch(/^This update changes how VISTA stores its data/);
    expect(dataResetSentence(null)).toMatch(/^If this update changes/);
    expect(dataResetSentence(false)).toBeNull();
  });
});

describe("activityLines", () => {
  it("names each conversation, the debates and each watched job", () => {
    expect(
      activityLines({
        chat_runs: [
          { chat_session_id: "a", project_id: "p", needs_you: false, title: "Li6 enrichment", project_name: "molten-salt" },
          { chat_session_id: "b", project_id: "p", needs_you: true, title: null, project_name: null },
        ],
        debates: 2,
        watched_jobs: [
          { job_id: "42", cluster: "frontier", job_name: "neutronics", state: "RUNNING" },
          { job_id: "43", cluster: "odo", job_name: null, state: "PENDING" },
        ],
      }),
    ).toEqual([
      "Li6 enrichment in molten-salt is responding; its response will stop.",
      "A conversation is waiting for your answer; its response will stop.",
      "2 Hypothesis Lab debates are running; they will stop.",
      "neutronics (42) on Frontier keeps running; VISTA watches it again once it reopens.",
      "Job 43 on Odo keeps running; VISTA watches it again once it reopens.",
    ]);
  });

  it("is empty when nothing runs", () => {
    expect(activityLines({ chat_runs: [], debates: 0, watched_jobs: [] })).toEqual([]);
  });
});

describe("progressText", () => {
  it("reads in MB, then GB", () => {
    expect(progressText({ state: "downloading", version: "0.2.5", bytes: 312 * 1024 ** 2, total: 1.7 * 1024 ** 3, error: null })).toBe(
      "312 MB of 1.7 GB",
    );
  });
});

describe("vistaApp", () => {
  afterEach(() => {
    delete window.vistaApp;
  });

  it("is the window's bridge only when it says it is VISTA's", () => {
    expect(vistaApp()).toBeNull();
    (window as unknown as { vistaApp: unknown }).vistaApp = { isVistaApp: "yes" };
    expect(vistaApp()).toBeNull();
    window.vistaApp = { isVistaApp: true, restartToUpdate: async () => ({ ok: true }) };
    expect(vistaApp()).not.toBeNull();
  });
});

describe("refreshVersion while the backend's first check is out", () => {
  afterEach(() => {
    resetVersionForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("asks again soon until the check lands, then stops", async () => {
    vi.useFakeTimers();
    const answers = [info({ pending: true }), info({ pending: true }), info({ pending: false, latest: "0.2.5" })];
    const fetch = vi.fn(async () => new Response(JSON.stringify(answers.shift()), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await refreshVersion();
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(PENDING_MS);
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(PENDING_MS);
    expect(fetch).toHaveBeenCalledTimes(3);
    // The check has landed: no more quick re-asks.
    await vi.advanceTimersByTimeAsync(PENDING_MS * 10);
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
