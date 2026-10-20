/**
 * Client types + fetchers for the agent-forum debate view.
 *
 * Mirrors the backend's `DebateStatePublic` (snake_case wire shape). All calls go
 * through the BFF proxy under `/api/debates`.
 *
 * The one thing to preserve when touching these types: what this install
 * *knows* about a post and what the post *says* about itself are different.
 * `vouch_lane`, `published` and `on_remote` are ours. `sender`, `forum_role` and
 * `origin` are written into the post by whoever posted it, so on anything but a
 * host-observed post they are that peer's claim. Anything rendering a post has
 * to keep that boundary visible — flattening it into one blob of text would let
 * a post claim an identity it does not have.
 */

export type DebateStatus =
  | "setting_up"
  | "debating"
  | "converged"
  | "closed"
  | "failed";

/** Kinds a client may post. Wider kinds exist but are produced by other verbs. */
export type PostableKind = "ASK" | "FINDING" | "RISK" | "PROPOSAL";

export type DebateRun = {
  id: string;
  project_id: string;
  user_id: string;
  topic: string;
  framing: string | null;
  thread_id: string;
  rounds: number;
  rounds_done: number;
  status: DebateStatus;
  verdict: Verdict | null;
  created_at: string;
  updated_at: string;
  /**
   * What the debate is doing right now, in words, or null when nothing is.
   *
   * A turn produces nothing until it finishes, so without this a thread that has
   * stopped growing looks the same whether a role is thinking, waiting on a
   * cluster job, or dead.
   */
  activity: string | null;
  activity_since: string | null;
  /**
   * True once the forum no longer has this debate's thread — deleted on the
   * forge, or written by h5i before the forum moved to plain git. The stored
   * posts are still shown; posting, continuing and ending it are refused.
   */
  thread_missing: boolean | null;
};

export type DebateParticipant = {
  id: string;
  run_id: string;
  identity: string;
  debate_role: string;
  forum_role: string;
  active: boolean;
  /** What this role was allowed to use. Empty means it had nothing to reach for. */
  granted_tools: string[];
  /** This role's own model (`openai:<name>`); null follows the opener's default. */
  model: string | null;
};

export const DEBATE_ROLES = ["proposer", "reviewer", "referee"] as const;
export type DebateRoleName = (typeof DEBATE_ROLES)[number];

/** Each role's own model; null is a role on the default. */
export type RoleModels = Record<DebateRoleName, string | null>;

export const noRoleModels = (): RoleModels => ({
  proposer: null,
  reviewer: null,
  referee: null,
});

/** The models a debate's roster recorded, one per role. */
export function modelsOf(participants: DebateParticipant[]): RoleModels {
  const models = noRoleModels();
  for (const p of participants) {
    if ((DEBATE_ROLES as readonly string[]).includes(p.debate_role)) {
      models[p.debate_role as DebateRoleName] = p.model ?? null;
    }
  }
  return models;
}

/** The roles with a model of their own, for opening a debate. Omitted roles use the default. */
export function chosenModels(models: RoleModels): Partial<Record<DebateRoleName, string>> {
  const chosen: Partial<Record<DebateRoleName, string>> = {};
  for (const role of DEBATE_ROLES) {
    const model = models[role];
    if (model) chosen[role] = model;
  }
  return chosen;
}

/**
 * The roles whose model differs from `original`, for continuing a debate. A
 * role returned to the default is sent as null; an unchanged role is left out,
 * so the backend leaves its row alone.
 */
export function changedModels(
  current: RoleModels,
  original: RoleModels
): Partial<Record<DebateRoleName, string | null>> {
  const changed: Partial<Record<DebateRoleName, string | null>> = {};
  for (const role of DEBATE_ROLES) {
    if (current[role] !== original[role]) changed[role] = current[role];
  }
  return changed;
}

/**
 * How much this install actually knows about where a post came from.
 *
 * Only `host-observed` means this install wrote it. On the other two the
 * `sender`, `forum_role` and `origin` are whatever the poster wrote — that
 * peer's account of itself, unsigned, and not proof of anything.
 */
export type VouchLane = "host-observed" | "peer-claimed" | "unattributed";

export function isObserved(post: DebatePost): boolean {
  return post.vouch_lane === "host-observed";
}

/**
 * The operator, decided by the lane and not by the sender string.
 *
 * Every install's operator posts as `human`, so on a shared forum an external
 * participant's comment arrives as `human` too. Checking the
 * name alone would present a stranger as the person who owns the thread.
 */
export function isOperator(post: DebatePost): boolean {
  return post.sender === "human" && isObserved(post);
}

/**
 * One tool an agent reached for, and what it saw.
 *
 * `detail` is the scanning label; `receipt` is the evidence under it — the query
 * and the passages that came back, the job id and cluster, the page that was
 * fetched. Null when the call had nothing worth keeping.
 */
export type ToolUse = {
  tool: string;
  detail: string;
  receipt?: string | null;
};

export type DebatePost = {
  id: string;
  run_id: string;
  post_id: string;
  kind: string;
  /** What the poster said. Always their claim, from anyone. */
  body: string;
  sender: string;
  forum_role: string;
  origin: string | null;
  reply_to: string | null;
  ts: string;
  vouch_lane: string | null;
  denied: string | null;
  /**
   * For a post this install wrote: whether the forum remote has it yet. False is
   * "not yet published" — written here, waiting for the next successful sync.
   * Null on a peer's post, which by definition came from the remote.
   */
  published: boolean | null;
  /**
   * False when a post we once read from the remote is no longer there: someone
   * rewrote the thread's history. Kept, because it was said.
   */
  on_remote: boolean | null;
  /** Net votes from participants this host observed. */
  votes: number;
  /**
   * Net votes that arrived over the remote.
   *
   * Separate from `votes` on purpose: one says what this forum's own
   * participants would act on, the other what outside readers think. Summed,
   * neither is legible.
   */
  peer_votes: number;
  /**
   * The VISTA account that wrote this, where this deployment authenticated them.
   *
   * Null on every post from outside, and that is not a gap to fill: every
   * install's operator posts as `human`, so a peer's post carries no name we
   * could believe.
   */
  authored_by: string | null;
  round_index: number | null;
  /**
   * The model that wrote this post, as resolved at the time; null for a human or
   * peer post and for one from before it was recorded. Ours, never the poster's
   * claim.
   */
  model: string | null;
  /**
   * The tools the agent called for this post.
   *
   * Empty on an agent's post is meaningful, not missing: it says the claim rests
   * on the model alone. The UI has to show that, or a grounded claim and an
   * asserted one look the same.
   */
  tools_used: ToolUse[];
};

export type Hypothesis = {
  claim: string;
  mechanism: string;
  predictions: string[];
  confidence: number;
  open_risks: string[];
};

export type Verdict = {
  ranked: Array<{ hypothesis: Hypothesis; standing: string }>;
  rationale: string;
  unresolved: string[];
};

/**
 * A simulation the debate commissioned, and where it got to.
 *
 * A `commission_simulation` chip proves a job was submitted and no more. This is
 * what became of it — the half of the story that lives on the campaign side.
 */
export type CommissionedRun = {
  job_id: string;
  /** Nullable on the job row, so nullable here. */
  job_name: string | null;
  cluster: string;
  prediction: string;
  commissioned_by: string;
  state: string;
  submitted_at: string;
  /** Null means nothing has polled it since submission. */
  last_polled_at: string | null;
  /** Whether the outputs came back and were posted onto the thread. */
  result_collected: boolean;
};

export type DebateState = {
  run: DebateRun;
  participants: DebateParticipant[];
  posts: DebatePost[];
  simulations: CommissionedRun[];
};

/**
 * What to tell a reader about a commissioned run, in one phrase.
 *
 * Deliberately distinguishes "nothing has looked at this" from "still running".
 * They present identically in the raw state, and only the first is a problem
 * with the deployment rather than with the queue.
 */
export function simulationStanding(sim: CommissionedRun): {
  label: string;
  tone: "waiting" | "stalled" | "done" | "failed";
} {
  const state = sim.state.toUpperCase();
  if (sim.result_collected) {
    return { label: "result posted to the thread", tone: "done" };
  }
  if (state.includes("FAIL") || state.includes("CANCEL") || state.includes("TIMEOUT")) {
    return { label: `${sim.state} — no result posted`, tone: "failed" };
  }
  if (state.includes("COMPLET")) {
    return { label: "finished; result not collected yet", tone: "waiting" };
  }
  if (!sim.last_polled_at) {
    return { label: `${sim.state} — not polled yet`, tone: "stalled" };
  }
  return { label: `${sim.state} — waiting`, tone: "waiting" };
}

/** Whether this project's lab can run, and whether its forum is shared. */
export type ForumStatus = {
  /** The lab is usable: the forum is on, the project has a URL, and git works. */
  enabled: boolean;
  shared: boolean;
  remote: string | null;
  /** False when this machine has no usable git; the lab is off until it does. */
  git_ok: boolean;
  /** Why git is unusable, written for the reader. Shown verbatim. */
  git_reason: string | null;
  /** This install's posts the remote does not have yet — e.g. written offline. */
  unpublished: number;
  /** Why publishing last failed; null once a push succeeds. */
  publish_error?: PublishError | null;
};

/** Backend `PublishError`: why posts did not reach the forum. */
export type PublishError = {
  reason: "refused" | "unreachable" | "failed";
  credential: "project" | "settings-github" | "settings-gitlab" | "system-git";
  /** Git's own message. It never holds the token. */
  message: string;
  at: string;
};

const CREDENTIAL_NAMES: Record<PublishError["credential"], string> = {
  project: "This project’s forum token",
  "settings-github": "Your GitHub token from Settings",
  "settings-gitlab": "Your gitlab.com token from Settings",
  "system-git": "Your system git’s credentials",
};

const CREDENTIAL_FIXES: Record<PublishError["credential"], string> = {
  project: "Replace or remove it in the project’s settings on the Projects page.",
  "settings-github": "Replace it in Settings → Repositories, or give this project its own token.",
  "settings-gitlab": "Replace it in Settings → Repositories, or give this project its own token.",
  "system-git":
    "Add a token in Settings → Repositories, or give this project its own token on the Projects page.",
};

/**
 * The page a forum's remote can be opened at in a browser, or null.
 *
 * Only an `https://` remote has one: its URL without the `.git` the clone URL
 * ends in, which GitHub and GitLab both serve as the repository's page. An SSH
 * remote (`git@host:org/repo.git`) is shown as text instead. Credentials in the
 * URL are never put in a link.
 */
export function forumWebUrl(remote: string): string | null {
  let url: URL;
  try {
    url = new URL(remote);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password) return null;
  return `${url.origin}${url.pathname.replace(/\.git\/?$/, "")}`;
}

/**
 * What the lab says about a failed publish: which credential was refused and
 * where to change it, or that the forum could not be reached, or git's message.
 * A refusal is never worded as "unreachable", and only a refusal names a token.
 */
export function publishErrorNotice(error: PublishError): { headline: string; detail: string } {
  if (error.reason === "refused") {
    return {
      headline: `${CREDENTIAL_NAMES[error.credential]} ${
        error.credential === "system-git" ? "were" : "was"
      } refused by the forum.`,
      detail: CREDENTIAL_FIXES[error.credential],
    };
  }
  if (error.reason === "unreachable") {
    return {
      headline: "The forum could not be reached.",
      detail: "Posts are kept here and publish on the next successful sync.",
    };
  }
  return { headline: "Publishing to the forum failed.", detail: `Git said: ${error.message}` };
}

const JSON_HEADERS = { "content-type": "application/json" };

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchForumStatus(
  projectName: string,
  signal?: AbortSignal
): Promise<ForumStatus> {
  const query = new URLSearchParams({ project_name: projectName });
  return unwrap(await fetch(`/api/forum/status?${query}`, { signal }));
}

export async function listDebates(
  projectName: string,
  signal?: AbortSignal
): Promise<DebateRun[]> {
  const params = new URLSearchParams({ project_name: projectName });
  return unwrap(await fetch(`/api/debates?${params}`, { signal }));
}

export async function fetchDebate(
  projectName: string,
  runId: string,
  signal?: AbortSignal
): Promise<DebateState> {
  const params = new URLSearchParams({ project_name: projectName, run_id: runId });
  return unwrap(await fetch(`/api/debates?${params}`, { signal }));
}

export async function openDebate(
  projectName: string,
  body: {
    topic: string;
    framing?: string | null;
    rounds?: number | null;
    /** A model per role; a role left out uses the default. */
    models?: Partial<Record<DebateRoleName, string>>;
  }
): Promise<DebateRun> {
  const params = new URLSearchParams({ project_name: projectName });
  return unwrap(
    await fetch(`/api/debates?${params}`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    })
  );
}

export async function postToDebate(
  projectName: string,
  runId: string,
  body: { body: string; kind?: PostableKind }
): Promise<DebatePost> {
  const params = new URLSearchParams({ project_name: projectName, run_id: runId });
  return unwrap(
    await fetch(`/api/debates/posts?${params}`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    })
  );
}

export async function closeDebate(
  projectName: string,
  runId: string
): Promise<DebateRun> {
  const params = new URLSearchParams({ project_name: projectName, run_id: runId });
  return unwrap(await fetch(`/api/debates/close?${params}`, { method: "POST" }));
}

/**
 * Pick a finished debate back up for more rounds, with the same roster: the
 * extra rounds post under the identities the earlier ones used.
 */
export async function continueDebate(
  projectName: string,
  runId: string,
  rounds: number,
  /** Roles to move to another model (null: back to the default); the rest keep theirs. */
  models: Partial<Record<DebateRoleName, string | null>> = {}
): Promise<DebateRun> {
  const params = new URLSearchParams({ project_name: projectName, run_id: runId });
  return unwrap(
    await fetch(`/api/debates/continue?${params}`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ rounds, models }),
    })
  );
}

/**
 * Who ended a debate: the operator, a peer, or nobody yet.
 *
 * Derived from the CLOSED post's vouch lane, because that is where the fact
 * lives. Both arrive with `sender === "human"` — every install's operator
 * posts that way — so labelling every closure "ended early" would credit you
 * with a decision an outside participant may have made.
 */
export function closedBy(posts: DebatePost[]): "operator" | "peer" | null {
  for (let i = posts.length - 1; i >= 0; i--) {
    if (posts[i].kind === "CLOSED") {
      return isObserved(posts[i]) ? "operator" : "peer";
    }
  }
  return null;
}

/**
 * Who wrote a post, where that is actually known: the VISTA account this
 * deployment authenticated when the post was made. Null for everything else —
 * a peer's post carries no name we could believe.
 */
export function authorOf(post: DebatePost): string | null {
  return post.authored_by ?? null;
}

/** Debate statuses that can still produce new posts. */
export function isActive(status: DebateStatus): boolean {
  return status === "setting_up" || status === "debating";
}

/**
 * Whether a finished debate can still receive posts from outside.
 *
 * A concluded debate is not a closed thread: the agents ran out of rounds, but
 * the forum thread stays open and a peer can still object to the hypothesis —
 * which is the likeliest moment for one to, since there is finally something to
 * object to. The event stream ends at a terminal status and the UI opens none
 * for a finished run, so this is what keeps such a debate watched.
 *
 * `closed` is excluded because a closed thread takes no further posts, and a
 * thread no longer on the forum has nothing left to read.
 */
export function watchesForPeerPosts(run: DebateRun, posts: DebatePost[]): boolean {
  return !isActive(run.status) && acceptsPosts(run, posts);
}

/**
 * Whether anyone may post to this debate from here. A closed thread refuses
 * every post (readers would never show one written after the close), and a
 * thread no longer on the forum has nowhere to put it.
 */
export function acceptsPosts(run: DebateRun, posts: DebatePost[]): boolean {
  // The CLOSED post, not only the status: a debate that concluded and was then
  // closed by a peer keeps its "converged" status, and its thread is closed all
  // the same.
  return run.status !== "closed" && !run.thread_missing && closedBy(posts) === null;
}

/**
 * The scientific role behind a forum identity (`vista-proposer-1a2b` → proposer).
 *
 * Only for a post this install wrote; for anything that arrived over a remote
 * the identity is the peer's own claim, so this returns null there rather than
 * dressing it as one of our roles.
 */
export function debateRoleOf(post: DebatePost): string | null {
  // Only for posts this install wrote. The identity travels in the sender name,
  // which a remote peer controls completely — so on anything else this would
  // render an outsider with one of our role badges.
  if (!isObserved(post)) return null;
  const match = /^vista-(proposer|reviewer|referee)\b/.exec(post.sender);
  return match ? match[1] : null;
}
