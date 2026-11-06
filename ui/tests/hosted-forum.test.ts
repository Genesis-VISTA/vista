import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  continueDebate,
  hostedForumServer,
  liveRunLine,
  publishErrorNotice,
  runnerName,
  signInPageUrl,
  type DebatePost,
  type LiveRun,
  type PublishError,
} from "@/lib/debates";
import { forumCredential } from "@/lib/forum-credential";
import { forumRequest } from "@/lib/projects";

/*
 * A forum hosted on the collaboration server, from the UI's side: how the
 * project dialog's choice goes to the API, which credential it names, how a
 * failed publish is explained, and what the lab shows of verified names.
 */

const SERVER = "https://obsidian.example.org/vista/api";
const ID = "11111111-2222-4333-8444-555555555555";

const error = (overrides: Partial<PublishError>): PublishError => ({
  reason: "refused",
  credential: "collaboration",
  message: "",
  at: "2026-11-05T12:00:00Z",
  ...overrides,
});

describe("forumRequest", () => {
  it("sends a blank URL for a hosted forum, and wants one", () => {
    expect(forumRequest("hosted", "https://github.com/o/forum.git")).toEqual({
      forumRepoUrl: "",
      hostedForumWanted: true,
    });
  });

  it("sends the repository's URL, trimmed", () => {
    expect(forumRequest("url", "  https://github.com/o/forum.git ")).toEqual({
      forumRepoUrl: "https://github.com/o/forum.git",
      hostedForumWanted: true,
    });
  });

  it("sends no URL and no wish for a hosted forum for none", () => {
    expect(forumRequest("none", "https://github.com/o/forum.git")).toEqual({
      forumRepoUrl: "",
      hostedForumWanted: false,
    });
  });
});

describe("forumCredential for a hosted forum", () => {
  const url = `${SERVER}/git/${ID}.git`;

  it("is the collaboration sign-in, whatever tokens exist", () => {
    expect(forumCredential(url, { has_github_token: true, has_gitlab_token: true }, SERVER)).toBe(
      "collaboration",
    );
  });

  it("is an ordinary remote when it is another server's, or none is in effect", () => {
    expect(forumCredential(url, {}, "https://other.example.org")).toBe("system-git");
    expect(forumCredential(url, {}, null)).toBe("system-git");
  });
});

describe("hostedForumServer", () => {
  it("reads the server out of a hosted forum's URL", () => {
    expect(hostedForumServer(`${SERVER}/git/${ID}.git`)).toBe(SERVER);
    expect(hostedForumServer(`${SERVER}/git/${ID}.git`, `${SERVER}/`)).toBe(SERVER);
  });

  it("is null for a forge, and for a server other than the one in effect", () => {
    expect(hostedForumServer("https://github.com/o/forum.git")).toBeNull();
    expect(hostedForumServer(`${SERVER}/git/${ID}.git`, "https://other.example.org")).toBeNull();
    expect(hostedForumServer("", SERVER)).toBeNull();
  });

  it("builds the sign-in page", () => {
    expect(signInPageUrl(`${SERVER}/`)).toBe(`${SERVER}/sign-in`);
  });
});

describe("publishErrorNotice for a hosted forum", () => {
  it("shows the server's rule message", () => {
    const notice = publishErrorNotice(
      error({ reason: "rule", message: "Only the user who opened this thread may close it." }),
      SERVER,
    );
    expect(notice.headline).toMatch(/rules/);
    expect(notice.detail).toBe("Only the user who opened this thread may close it.");
    expect(notice.link).toBeUndefined();
  });

  it("says the sign-in was refused and links to the server's sign-in page", () => {
    const notice = publishErrorNotice(
      error({ message: `The collaboration sign-in was refused. Get a token from ${SERVER}/sign-in.` }),
      `${SERVER}/`,
    );
    expect(notice.headline).toBe("The collaboration sign-in was refused by the server.");
    expect(notice.detail).toMatch(/Settings → Collaboration/);
    expect(notice.link).toEqual({ href: `${SERVER}/sign-in`, label: "Open the sign-in page" });
    expect(`${notice.headline} ${notice.detail}`).not.toMatch(/GitHub|system/);
  });

  it("has no link when the server's address is not known", () => {
    const notice = publishErrorNotice(error({ message: "The collaboration sign-in was refused." }));
    expect(notice.link).toBeUndefined();
  });

  it("does not call a missing forum a refused sign-in", () => {
    const notice = publishErrorNotice(
      error({
        reason: "no_access",
        message: "The collaboration server has no forum your account may use here.",
      }),
      SERVER,
    );
    expect(notice.headline).not.toMatch(/sign-in/);
    expect(notice.detail).toMatch(/no forum your account may use/);
    expect(notice.link).toBeUndefined();
  });
});

function live(over: Partial<LiveRun> = {}): LiveRun {
  return {
    post_id: "run",
    ours: false,
    origin: null,
    rounds: 3,
    posts: 2,
    started_at: "2026-11-04T12:00:00+00:00",
    last_post_at: "2026-11-04T12:10:00+00:00",
    ...over,
  };
}

function post(over: Partial<DebatePost>): DebatePost {
  return {
    id: "p",
    run_id: null,
    post_id: "p",
    kind: "RUN",
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

describe("a peer's run on a hosted forum", () => {
  const now = Date.parse("2026-11-04T12:14:00+00:00");

  it("names the user whose RUN post the server recorded", () => {
    const who = runnerName(live(), [post({ post_id: "run", pusher_name: "Ada Lovelace" })]);
    expect(who).toBe("Ada Lovelace");
    expect(liveRunLine(live(), now, who)).toBe(
      "Ada Lovelace is running 3 rounds · 2 posts so far · last 4 min ago",
    );
  });

  it("keeps 'Another user' where no name is known, and never renames our own run", () => {
    expect(runnerName(live(), [post({ post_id: "run" })])).toBeNull();
    expect(
      runnerName(live({ ours: true }), [post({ post_id: "run", pusher_name: "Me" })]),
    ).toBeNull();
    expect(liveRunLine(live(), now, null)).toMatch(/^Another user is running/);
  });
});

describe("continueDebate when another user is continuing", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("throws the server's sentence for a 409", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Another user is continuing this debate." }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(continueDebate("p", "t", 2)).rejects.toThrow(
      "Another user is continuing this debate.",
    );
  });
});
