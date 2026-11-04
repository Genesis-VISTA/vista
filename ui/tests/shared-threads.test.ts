import { describe, expect, it } from "vitest";
import {
  acceptsPosts,
  canClose,
  canContinue,
  liveRunLine,
  readablePosts,
  threadStatusLabel,
  type DebatePost,
  type DebateRun,
  type ForumThread,
  type LiveRun,
} from "@/lib/debates";

/*
 * A shared Hypothesis Lab (openspec/changes/forum-shared-threads): every thread
 * on the forum is listed, anyone may post or continue it with their own agents
 * while no run is live, and only the install that opened it may close it.
 */

function thread(over: Partial<ForumThread> = {}): ForumThread {
  return {
    id: "row",
    thread_id: "01a0d3c1-db12-747a-b485-fbd38b3b9f8b",
    title: "Why does coffee cool faster when you blow on it?",
    framing: null,
    created_by: "a".repeat(32),
    opened_here: true,
    opened_at: "2026-11-04T12:00:00+00:00",
    status: "done",
    thread_missing: false,
    current_verdict: null,
    live_run: null,
    run: null,
    ...over,
  };
}

function run(status: DebateRun["status"]): DebateRun {
  return {
    id: "r1",
    project_id: "p",
    topic: "t",
    framing: null,
    thread_id: "t",
    rounds: 5,
    rounds_done: 2,
    status,
    verdict: null,
    created_at: "",
    updated_at: "",
    activity: null,
    activity_since: null,
    thread_missing: null,
  };
}

function live(over: Partial<LiveRun> = {}): LiveRun {
  return {
    post_id: "run",
    ours: false,
    origin: "b".repeat(32),
    rounds: 3,
    posts: 2,
    started_at: "2026-11-04T12:00:00+00:00",
    last_post_at: "2026-11-04T12:10:00+00:00",
    ...over,
  };
}

function post(kind: string, over: Partial<DebatePost> = {}): DebatePost {
  return {
    id: kind,
    run_id: null,
    post_id: kind,
    kind,
    body: "",
    sender: "human",
    forum_role: "human",
    origin: null,
    reply_to: null,
    ts: "",
    vouch_lane: "peer-claimed",
    denied: null,
    published: null,
    on_remote: true,
    votes: 0,
    peer_votes: 0,
    authored_by: null,
    round_index: null,
    model: null,
    tools_used: [],
    ...over,
  };
}

describe("a teammate's thread", () => {
  it("can be posted to and continued with your own agents", () => {
    const theirs = thread({ opened_here: false });
    expect(acceptsPosts(theirs, [])).toBe(true);
    expect(canContinue(theirs, [])).toBe(true);
  });

  it("cannot be closed by you", () => {
    expect(canClose(thread({ opened_here: false }), [])).toBe(false);
    expect(canClose(thread({ opened_here: true }), [])).toBe(true);
  });

  it("cannot be continued while someone's run is live, but can still be posted to", () => {
    const busy = thread({ opened_here: false, status: "open", live_run: live() });
    expect(canContinue(busy, [])).toBe(false);
    expect(acceptsPosts(busy, [])).toBe(true);
    expect(threadStatusLabel(busy, [])).toBe("Another researcher is arguing");
  });

  it("cannot be continued while your own run argues", () => {
    expect(canContinue(thread({ run: run("debating") }), [])).toBe(false);
  });
});

describe("threadStatusLabel", () => {
  it("says who closed it", () => {
    const closed = thread({ status: "closed" });
    expect(threadStatusLabel(closed, [post("CLOSED")])).toBe("Ended by a peer");
    expect(
      threadStatusLabel(closed, [post("CLOSED", { vouch_lane: "host-observed" })]),
    ).toBe("Ended early");
  });

  it("calls a thread with a verdict concluded, whoever ruled", () => {
    expect(threadStatusLabel(thread({ status: "done", opened_here: false }), [])).toBe(
      "Concluded",
    );
  });

  it("says a missing thread is missing", () => {
    expect(threadStatusLabel(thread({ thread_missing: true }), [])).toBe(
      "No longer on the forum",
    );
  });
});

describe("liveRunLine", () => {
  it("says how far another researcher's run has got", () => {
    const now = Date.parse("2026-11-04T12:14:00+00:00");
    expect(liveRunLine(live(), now)).toBe(
      "Another researcher is running 3 rounds · 2 posts so far · last 4 min ago",
    );
  });
});

describe("readablePosts", () => {
  it("leaves out RUN markers", () => {
    expect(readablePosts([post("RUN"), post("ASK")]).map((p) => p.kind)).toEqual(["ASK"]);
  });
});
