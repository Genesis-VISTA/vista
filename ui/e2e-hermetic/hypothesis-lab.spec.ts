import { expect, test, type Page } from "@playwright/test";
import { ROUTES } from "./fixtures";
import { by, installStub, type Stub } from "./stub";

/**
 * The Hypothesis Lab keeps the forum's status current while it is open
 * (openspec change global-repo-tokens, task 4.3).
 *
 * A debate's first push is refused seconds after the page has loaded, so a
 * status read only on load never showed why posts stay "not yet published". The
 * page re-reads it on an interval; the fake clock skips that interval instead of
 * waiting it out.
 */
const STATUS = {
  enabled: true,
  shared: true,
  remote: "https://github.com/org/forum.git",
  git_ok: true,
  git_reason: null,
  unpublished: 0,
  publish_error: null,
};

const REFUSED = {
  ...STATUS,
  unpublished: 2,
  publish_error: {
    reason: "refused",
    credential: "settings-github",
    message: "remote: Write access to repository not granted.",
    at: "2026-10-15T16:00:00+00:00",
  },
};

const NOTICE = "Your GitHub token from Settings was refused by the forum.";
const POLL_MS = 16_000;

async function openLab(page: Page) {
  await page.goto("/projects");
  await page
    .locator(".project-card")
    .filter({ hasText: "molten-salt" })
    .getByRole("button", { name: "Open" })
    .click();
  await page
    .getByRole("complementary", { name: "Primary navigation" })
    .getByRole("link", { name: "Hypothesis Lab" })
    .click();
  await expect(page.getByRole("heading", { name: "Hypothesis Lab" })).toBeVisible();
}

test.describe("hypothesis lab publish notice", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("a publish refused after the page loaded is explained without a reload", async ({
    page,
  }) => {
    await page.clock.install();
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/debates": [],
      "GET /api/forum/status": STATUS,
    });
    await openLab(page);
    await expect(page.getByText(NOTICE)).toHaveCount(0);

    await stub.setRoute("GET /api/forum/status", REFUSED);
    await page.clock.runFor(POLL_MS);
    await expect(page.getByText(NOTICE)).toBeVisible();
  });

  test("the notice goes once a push succeeds", async ({ page }) => {
    await page.clock.install();
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/debates": [],
      "GET /api/forum/status": REFUSED,
    });
    await openLab(page);
    await expect(page.getByText(NOTICE)).toBeVisible();

    await stub.setRoute("GET /api/forum/status", STATUS);
    await page.clock.runFor(POLL_MS);
    await expect(page.getByText(NOTICE)).toHaveCount(0);
  });
});


/**
 * Each debate role has its own model (openspec change per-chat-model-override,
 * task 6.2): the roster line names them, each post names the one that wrote it,
 * and Continue starts from them.
 */
const RUN_ID = "00000000-0000-4000-8000-0000000000d1";
const NOW = "2026-01-01T00:00:00+00:00";

const RUN = {
  id: RUN_ID,
  project_id: "00000000-0000-4000-8000-000000000001",
  topic: "Why does the knee move?",
  framing: null,
  thread_id: "thread-1",
  rounds: 1,
  rounds_done: 1,
  status: "converged",
  verdict: null,
  created_at: NOW,
  updated_at: NOW,
  activity: null,
  activity_since: null,
  thread_missing: false,
};

const participant = (role: string, model: string | null) => ({
  id: `p-${role}`,
  run_id: RUN_ID,
  identity: `vista-${role}-000000d1`,
  debate_role: role,
  forum_role: role,
  active: true,
  granted_tools: [] as string[],
  model,
});

const post = (role: string, kind: string, model: string | null) => ({
  id: `row-${role}`,
  run_id: RUN_ID,
  post_id: `post-${role}`,
  kind,
  body: `The ${role} speaks.`,
  sender: `vista-${role}-000000d1`,
  forum_role: role,
  origin: null,
  reply_to: null,
  ts: role === "proposer" ? "2026-01-01T00:00:01+00:00" : "2026-01-01T00:00:02+00:00",
  vouch_lane: "host-observed",
  denied: null,
  published: true,
  on_remote: true,
  votes: 0,
  peer_votes: 0,
  authored_by: null,
  round_index: 1,
  model,
  tools_used: [] as unknown[],
});

const DEBATE = {
  run: RUN,
  participants: [
    participant("proposer", null),
    participant("reviewer", "openai:gpt-5-mini"),
    participant("referee", "openai:gpt-5"),
  ],
  posts: [
    post("proposer", "PROPOSAL", "openai:claude-sonnet"),
    post("reviewer", "RISK", "openai:gpt-5-mini"),
  ],
  simulations: [],
};

test.describe("hypothesis lab role models", () => {
  let stub: Stub | null = null;

  test.afterEach(async () => {
    if (stub) {
      expect(await stub.unstubbed(), "page asked for a route with no fixture").toEqual([]);
    }
    stub = null;
  });

  test("the roster line, each post and Continue show the models in use", async ({ page }) => {
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/debates": by("run_id", { default: [RUN], [RUN_ID]: DEBATE }),
      "GET /api/forum/status": STATUS,
    });
    await openLab(page);
    await page.locator(".debate-list__item").filter({ hasText: RUN.topic }).click();

    // A role on the default says so, and names what that is today.
    await expect(page.locator(".debate-roster__models")).toHaveText(
      "Proposer Default (claude-sonnet) · Reviewer gpt-5-mini · Referee gpt-5",
    );
    // Each post names the model that wrote it.
    await expect(page.locator(".debate-post__model")).toHaveText(["claude-sonnet", "gpt-5-mini"]);

    // Continue starts from the models the debate used.
    const continuing = page.locator(".debate-detail__continue");
    await expect(
      continuing.getByRole("button", { name: /^Proposer model: Default \(claude-sonnet\)\. / }),
    ).toBeVisible();
    await expect(
      continuing.getByRole("button", { name: /^Reviewer model: gpt-5-mini\. / }),
    ).toBeVisible();
    await expect(
      continuing.getByRole("button", { name: /^Referee model: gpt-5\. / }),
    ).toBeVisible();
  });

  test("the open form offers a model for each role, starting at the default", async ({ page }) => {
    stub = await installStub(page, {
      ...ROUTES,
      "GET /api/debates": [],
      "GET /api/forum/status": STATUS,
    });
    await openLab(page);

    const form = page.locator(".debate-open");
    for (const role of ["Proposer", "Reviewer", "Referee"]) {
      await expect(
        form.getByRole("button", { name: new RegExp(`^${role} model: Default \\(claude-sonnet\\)\\. `) }),
      ).toBeVisible();
    }
  });
});
