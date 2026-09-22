/**
 * Client types + fetchers for the agent-forum debate view.
 *
 * Mirrors the backend's `DebateStatePublic` (snake_case wire shape). All calls go
 * through the BFF proxy under `/api/debates`.
 *
 * The one thing to preserve when touching these types: a post's `body` is the
 * only field its author wrote. `sender`, `forum_role`, `box_id`,
 * `policy_digest`, `origin` and `vouch_lane` are stamped by the h5i host, and the
 * record format has no field an agent could write them through. Anything
 * rendering a post has to keep that boundary visible — flattening it into one
 * blob of text would let a post claim an identity it does not have.
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
};

export type DebateParticipant = {
  id: string;
  run_id: string;
  identity: string;
  debate_role: string;
  forum_role: string;
  box_slug: string;
  box_id: string;
  policy_digest: string | null;
  active: boolean;
  /** What this role was allowed to use. Empty means it had nothing to reach for. */
  granted_tools: string[];
};

/**
 * How much the host actually knows about where a post came from.
 *
 * Only `host-observed` means this host watched it happen. On the other two the
 * `sender`, `forum_role` and `origin` are whatever the *remote* host stamped —
 * that peer's account of itself, unsigned, and not proof of anything.
 */
export type VouchLane = "host-observed" | "peer-claimed" | "unattributed";

export function isObserved(post: DebatePost): boolean {
  return post.vouch_lane === "host-observed";
}

/**
 * The operator, decided by the lane and not by the sender string.
 *
 * Every h5i host stamps its own operator's posts as `human`, so on a shared
 * forum an external participant's comment arrives as `human` too. Checking the
 * name alone would present a stranger as the person who owns the thread.
 */
export function isOperator(post: DebatePost): boolean {
  return post.sender === "human" && isObserved(post);
}

/** One tool an agent called while producing a post. */
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
  /** The only agent-authored field. Everything else here was stamped by the host. */
  body: string;
  sender: string;
  forum_role: string;
  box_id: string | null;
  policy_digest: string | null;
  origin: string | null;
  reply_to: string | null;
  ts: string;
  vouch_lane: string | null;
  denied: string | null;
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
   * Null on every post from outside, and that is not a gap to fill: h5i stamps
   * `sender="human"` for every host's operator, so a peer's post carries no
   * name we could believe.
   */
  authored_by: string | null;
  round_index: number | null;
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
  /** Origin host id → the forge account enrolled on that machine. */
  enrolled_origins: Record<string, EnrolledOrigin>;
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

/** The forum's federation state — whether it is shared, and whether votes count. */
export type ForumStatus = {
  enabled: boolean;
  shared: boolean;
  remote: string | null;
  vote_policy: string | null;
  enrolled: number;
  /** False when the policy is `principal` and nobody has enrolled. */
  votes_counting: boolean;
};

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
  body: { topic: string; framing?: string | null; rounds?: number | null }
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
 * Pick a finished debate back up for more rounds.
 *
 * A new roster is attached under fresh identities, because the previous one was
 * revoked when the debate concluded and a revoked identity cannot post.
 */
export async function continueDebate(
  projectName: string,
  runId: string,
  rounds: number
): Promise<DebateRun> {
  const params = new URLSearchParams({ project_name: projectName, run_id: runId });
  return unwrap(
    await fetch(`/api/debates/continue?${params}`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ rounds }),
    })
  );
}

/**
 * Who ended a debate: the operator, a peer, or nobody yet.
 *
 * Derived from the CLOSED post's vouch lane, because that is where the fact
 * lives. Both arrive with `sender === "human"` — every host stamps its own
 * operator that way — so labelling every closure "ended early" would credit you
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

/** Debate statuses that can still produce new posts. */
/**
 * A forge account bound to one machine.
 *
 * What it licenses saying is "this came from a machine <name> enrolled" — not
 * "<name> wrote this". Anyone with access to that machine posts as `human` from
 * that origin, so the binding is to hardware, not authorship.
 */
export interface EnrolledOrigin {
  principal: string;
  name: string | null;
}

/**
 * The best available account of who wrote a post.
 *
 * Three cases, and they are genuinely different kinds of statement:
 *  - `account`  — this deployment authenticated them. A fact.
 *  - `machine`  — an enrolled origin. A fact about the machine, not the person.
 *  - `null`     — nothing is known, and the origin is all there is to show.
 */
export function authorOf(
  post: DebatePost,
  enrolled: Record<string, EnrolledOrigin> = {}
): { kind: "account" | "machine"; label: string } | null {
  if (post.authored_by) return { kind: "account", label: post.authored_by };
  const binding = post.origin ? enrolled[post.origin] : undefined;
  if (binding) return { kind: "machine", label: binding.name ?? binding.principal };
  return null;
}

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
 * `closed` is excluded because h5i moves a closed thread to the attic and
 * nothing further can arrive on it.
 */
export function watchesForPeerPosts(status: DebateStatus): boolean {
  return !isActive(status) && status !== "closed";
}

/**
 * The scientific role behind a forum identity (`vista-proposer-1a2b` → proposer).
 *
 * h5i's own role vocabulary is only worker/reviewer/observer, so the meaningful
 * role travels in the identity. That is host-stamped data only for a post this
 * host observed; for anything that arrived over a remote it is the peer's own
 * claim, so this returns null there rather than dressing it as a role.
 */
export function debateRoleOf(post: DebatePost): string | null {
  // Only for posts this host observed. The identity travels in the sender name,
  // which a remote peer controls completely — so on anything else this would
  // render an outsider with one of our role badges.
  if (!isObserved(post)) return null;
  const match = /^vista-(proposer|reviewer|referee)\b/.exec(post.sender);
  return match ? match[1] : null;
}
