"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import DebateThread from "@/components/DebateThread";
import DebateVerdict from "@/components/DebateVerdict";
import {
  DebatePost,
  DebateRun,
  DebateState,
  ForumStatus,
  closeDebate,
  closedBy,
  fetchForumStatus,
  fetchDebate,
  isActive,
  watchesForPeerPosts,
  listDebates,
  openDebate,
  postToDebate,
} from "@/lib/debates";
import { useActiveProject } from "@/lib/projects";

const FINISHED_POLL_MS = 15_000;
/**
 * How often a finished-but-open debate is re-read.
 *
 * Comfortably above the backend's own forum-refresh interval, so a poll finds
 * either fresh news or a cheap cache hit rather than forcing a git fetch of its
 * own. A peer's comment on a concluded debate is not a live conversation.
 */

const STATUS_LABEL: Record<DebateRun["status"], string> = {
  setting_up: "Setting up",
  debating: "Arguing",
  converged: "Concluded",
  closed: "Ended early",
  failed: "Failed",
};

/**
 * The run's status, said accurately.
 *
 * "Ended early" reads as *you* ended it. On a shared forum anyone with push
 * access can close a thread they did not open, so who closed it has to come from
 * the record rather than from the status alone.
 */
function statusLabel(run: DebateRun, posts: DebatePost[]): string {
  if (run.status !== "closed") return STATUS_LABEL[run.status];
  return closedBy(posts) === "peer" ? "Ended by a peer" : "Ended early";
}

/* ---------------------------------------------------------------------- */
/*  Live updates                                                           */
/* ---------------------------------------------------------------------- */

/**
 * Follow a running debate over SSE, falling back to what the page already had.
 *
 * Posts arrive one at a time and are keyed by `post_id`, so a reconnect that
 * replays the thread from the beginning converges instead of duplicating it —
 * the stream deliberately replays rather than resuming from a cursor, because a
 * debate is a few dozen posts and a resumable cursor is a thing to get wrong.
 */
function useDebateStream(
  projectName: string | null,
  run: DebateRun | null,
  // Both callers wrap these in `useCallback`, so they are stable and can sit in
  // the dependency array directly. Stashing them in refs would mean writing a
  // ref during render, which React forbids for good reason.
  onPost: (post: DebatePost) => void,
  onStatus: (run: DebateRun) => void
) {
  const runId = run?.id ?? null;
  const active = run ? isActive(run.status) : false;

  useEffect(() => {
    if (!projectName || !runId || !active) return;

    const params = new URLSearchParams({
      project_name: projectName,
      run_id: runId,
    });
    const source = new EventSource(`/api/debates/events?${params}`);

    source.addEventListener("post", (event) => {
      try {
        onPost(JSON.parse((event as MessageEvent).data));
      } catch {
        /* a malformed frame should not tear down a live debate */
      }
    });
    source.addEventListener("status", (event) => {
      try {
        onStatus(JSON.parse((event as MessageEvent).data));
      } catch {
        /* as above */
      }
      source.close();
    });

    return () => source.close();
  }, [projectName, runId, active, onPost, onStatus]);
}

/* ---------------------------------------------------------------------- */
/*  Page                                                                   */
/* ---------------------------------------------------------------------- */

function DebatesPage() {
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;

  const [runs, setRuns] = useState<DebateRun[]>([]);
  const [state, setState] = useState<DebateState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forum, setForum] = useState<ForumStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const [topic, setTopic] = useState("");
  const [framing, setFraming] = useState("");
  const [rounds, setRounds] = useState(5);
  const [message, setMessage] = useState("");

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!projectName) return;
      try {
        const list = await listDebates(projectName, signal);
        setRuns(list);
        setSelectedId((current) => current ?? list[0]?.id ?? null);
      } catch (err) {
        if (!signal?.aborted) setError(String(err));
      }
    },
    [projectName]
  );

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    fetchForumStatus(controller.signal)
      .then(setForum)
      .catch(() => {
        /* the banner is advisory; its absence should not shout */
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!projectName || !selectedId) {
      setState(null);
      return;
    }
    const controller = new AbortController();
    fetchDebate(projectName, selectedId, controller.signal)
      .then(setState)
      .catch((err) => {
        if (!controller.signal.aborted) setError(String(err));
      });
    return () => controller.abort();
  }, [projectName, selectedId]);

  // Keep watching a finished debate for posts from outside.
  //
  // A live debate is followed by the event stream. A concluded one is not — the
  // stream ends at a terminal status and none is opened for a run that is
  // already finished — yet its thread is still open and a peer reviewer can
  // still post to it. Without this the page would sit on a stale projection
  // until someone reloaded it by hand.
  //
  // Re-fetching is what makes the server read the forum; the backend throttles
  // that to one fetch per thread per interval however many people are watching.
  const watchedStatus = state?.run.status;
  useEffect(() => {
    if (!projectName || !selectedId || !watchedStatus) return;
    if (!watchesForPeerPosts(watchedStatus)) return;

    const controller = new AbortController();
    const timer = setInterval(() => {
      fetchDebate(projectName, selectedId, controller.signal)
        .then(setState)
        .catch(() => {
          /* a failed poll should not disturb a page that is already readable */
        });
    }, FINISHED_POLL_MS);

    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [projectName, selectedId, watchedStatus]);

  useDebateStream(
    projectName,
    state?.run ?? null,
    useCallback((post: DebatePost) => {
      setState((current) => {
        if (!current) return current;
        if (current.posts.some((p) => p.post_id === post.post_id)) {
          // A replayed post can still carry a newer vote tally.
          return {
            ...current,
            posts: current.posts.map((p) =>
              p.post_id === post.post_id ? post : p
            ),
          };
        }
        return { ...current, posts: [...current.posts, post] };
      });
    }, []),
    useCallback((run: DebateRun) => {
      setState((current) => (current ? { ...current, run } : current));
      setRuns((current) => current.map((r) => (r.id === run.id ? run : r)));
    }, [])
  );

  const handleOpen = async () => {
    if (!projectName || !topic.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const run = await openDebate(projectName, {
        topic: topic.trim(),
        framing: framing.trim() || null,
        rounds,
      });
      setTopic("");
      setFraming("");
      setRuns((current) => [run, ...current]);
      setSelectedId(run.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handlePost = async () => {
    if (!projectName || !state || !message.trim()) return;
    setBusy(true);
    try {
      const post = await postToDebate(projectName, state.run.id, {
        body: message.trim(),
        kind: "ASK",
      });
      setMessage("");
      setState((current) =>
        current && !current.posts.some((p) => p.post_id === post.post_id)
          ? { ...current, posts: [...current.posts, post] }
          : current
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleClose = async () => {
    if (!projectName || !state) return;
    setBusy(true);
    try {
      const run = await closeDebate(projectName, state.run.id);
      setState((current) => (current ? { ...current, run } : current));
      setRuns((current) => current.map((r) => (r.id === run.id ? run : r)));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const posts = useMemo(
    () => [...(state?.posts ?? [])].sort((a, b) => a.ts.localeCompare(b.ts)),
    [state]
  );

  if (!projectName) {
    return <main className="debate-page"><p>Select a project first.</p></main>;
  }

  return (
    <main className="debate-page">
      <header className="debate-page__header">
        <h1>Hypothesis Forum</h1>
        <p className="debate-page__lede">
          Three agents — a Proposer, a Reviewer whose job is to falsify it, and
          a Referee who rules — argue a topic until a hypothesis survives. You
          can join the thread at any point, or end it.
        </p>
      </header>

      {error && <p className="debate-error">{error}</p>}

      {forum && !forum.votes_counting && (
        <p className="debate-warning">
          <strong>Votes are not being counted.</strong> This forum counts one
          vote per enrolled account, and no machine has enrolled yet — so every
          vote, including the agents&rsquo; own, is discarded. Each participant
          runs <code>h5i forum enroll</code> once on their own machine.
        </p>
      )}

      {forum?.shared && (
        <p className="debate-note">
          This forum is shared. Posts marked <strong>peer-claimed</strong> came
          from another machine: their name and role are that participant&rsquo;s
          own claim, not verified here.
        </p>
      )}

      <section className="debate-open">
        <h2>Open a debate</h2>
        <label>
          Topic
          <input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Which mechanism best explains the viscosity anomaly in FLiBe near 800 K?"
          />
        </label>
        <label>
          Framing <span className="debate-open__hint">(optional)</span>
          <textarea
            value={framing}
            onChange={(e) => setFraming(e.target.value)}
            rows={2}
            placeholder="Constraints, what to assume, what to ignore."
          />
        </label>
        <label className="debate-open__rounds">
          Rounds
          <input
            type="number"
            min={1}
            max={20}
            value={rounds}
            onChange={(e) => setRounds(Number(e.target.value))}
          />
        </label>
        <button onClick={handleOpen} disabled={busy || !topic.trim()}>
          Open debate
        </button>
      </section>

      <div className="debate-layout">
        <aside className="debate-list">
          {runs.length === 0 && <p>No debates yet.</p>}
          {runs.map((run) => (
            <button
              key={run.id}
              className={`debate-list__item${
                run.id === selectedId ? " debate-list__item--active" : ""
              }`}
              onClick={() => setSelectedId(run.id)}
            >
              <span className="debate-list__topic">{run.topic}</span>
              <span className="debate-list__status">
                {STATUS_LABEL[run.status]} · round {run.rounds_done}/{run.rounds}
              </span>
            </button>
          ))}
        </aside>

        <section className="debate-detail">
          {!state && <p>Select a debate.</p>}
          {state && (
            <>
              <div className="debate-detail__head">
                <h2>{state.run.topic}</h2>
                <span className="debate-detail__status">
                  {statusLabel(state.run, posts)} · round {state.run.rounds_done}/
                  {state.run.rounds}
                </span>
                {isActive(state.run.status) && (
                  <button onClick={handleClose} disabled={busy}>
                    End this debate
                  </button>
                )}
              </div>

              <ul className="debate-roster">
                {state.participants.map((participant) => (
                  <li key={participant.id}>
                    <strong>{participant.debate_role}</strong>{" "}
                    <span>{participant.identity}</span>{" "}
                    <span className="debate-roster__box">
                      {participant.box_id}
                    </span>
                    {!participant.active && (
                      <span className="debate-roster__revoked">revoked</span>
                    )}
                    <span className="debate-roster__tools">
                      {participant.granted_tools.length > 0
                        ? `may use: ${participant.granted_tools.join(", ")}`
                        : "no tools granted"}
                    </span>
                  </li>
                ))}
              </ul>

              <DebateVerdict run={state.run} />
              <DebateThread posts={posts} />

              <div className="debate-say">
                <label>
                  Say something into this debate
                  <textarea
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    rows={3}
                    placeholder="Constrain this to 1 bar; ignore pressure effects."
                  />
                </label>
                <button onClick={handlePost} disabled={busy || !message.trim()}>
                  Post as you
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<main className="debate-page">Loading…</main>}>
      <DebatesPage />
    </Suspense>
  );
}
