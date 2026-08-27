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
  votes: number;
  round_index: number | null;
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

export type DebateState = {
  run: DebateRun;
  participants: DebateParticipant[];
  posts: DebatePost[];
};

const JSON_HEADERS = { "content-type": "application/json" };

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
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

/** Debate statuses that can still produce new posts. */
export function isActive(status: DebateStatus): boolean {
  return status === "setting_up" || status === "debating";
}

/**
 * The scientific role behind a forum identity (`vista-proposer-1a2b` → proposer).
 *
 * h5i's own role vocabulary is only worker/reviewer/observer, so the meaningful
 * role travels in the identity — which the host also stamps, so reading it here
 * is reading host-stamped data, not an agent's claim about itself.
 */
export function debateRoleOf(sender: string): string | null {
  const match = /^vista-(proposer|reviewer|referee)\b/.exec(sender);
  return match ? match[1] : null;
}
