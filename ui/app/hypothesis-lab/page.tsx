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
  continueDebate,
  closedBy,
  fetchForumStatus,
  fetchDebate,
  isActive,
  simulationStanding,
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
  onStatus: (run: DebateRun) => void,
  onActivity: (activity: string | null, since: string | null) => void
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
    source.addEventListener("activity", (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        onActivity(data.activity ?? null, data.since ?? null);
      } catch {
        /* as above */
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
  }, [projectName, runId, active, onPost, onStatus, onActivity]);
}

/**
 * What the debate is doing, and for how long.
 *
 * The elapsed count is the part that carries the weight. "Proposer is thinking"
 * alone is still ambiguous after two minutes — it could be a stuck request — and
 * a number that keeps moving is the difference between waiting and wondering.
 * Ticks on its own interval rather than on stream events, because the whole point
 * is to keep changing when nothing is arriving.
 */
function Working({
  activity,
  since,
}: {
  activity: string;
  since: string | null;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [activity]);

  const started = since ? Date.parse(since) : NaN;
  const seconds = Number.isNaN(started)
    ? null
    : Math.max(0, Math.round((now - started) / 1000));

  return (
    <p className="debate-working" aria-live="polite">
      <span className="debate-working__pulse" aria-hidden="true" />
      {activity}
      {seconds !== null && (
        <span className="debate-working__elapsed">
          {seconds < 90 ? `${seconds}s` : `${Math.floor(seconds / 60)}m`}
        </span>
      )}
    </p>
  );
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
  const [moreRounds, setMoreRounds] = useState(3);

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
    }, []),
    useCallback((activity: string | null, since: string | null) => {
      setState((current) =>
        current
          ? { ...current, run: { ...current.run, activity, activity_since: since } }
          : current
      );
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

  const handleContinue = async () => {
    if (!projectName || !state) return;
    setBusy(true);
    try {
      const run = await continueDebate(projectName, state.run.id, moreRounds);
      // The response is the run as it stood when the request returned; the
      // orchestrator raises the budget from its own session a moment later. Show
      // it as arguing so the stream opens and the rest arrives live.
      const arguing = { ...run, status: "debating" as const };
      setState((current) => (current ? { ...current, run: arguing } : current));
      setRuns((current) => current.map((r) => (r.id === run.id ? arguing : r)));
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
        <h1>Hypothesis Lab</h1>
        <p className="debate-page__lede">
          Three local agents — a Proposer, a Reviewer whose job is to falsify it, and a Referee who rules — argue a topic until a hypothesis survives. You can join the thread at any point, or end it.
        </p>
        {/* The invitation is conditional on the forum actually publishing
            somewhere. Saying "invite your colleagues" on a deployment whose
            forum is a local git repository would be describing a capability
            nobody outside this machine can reach. */}
        {/* `forum` is null until the status fetch lands, and null is "not known
            yet" rather than "not shared" — keyed off `forum?.shared` alone, a
            shared lab flashed "on this machine only" on first paint. */}
        {forum === null ? null : forum.shared ? (
          <p className="debate-page__lede">
            The forum lives in a user-managed git repository this deployment publishes to. Give someone access and they can read the
            debate and post into it — an outside domain expert from their machine, or their own agents under their identities.
          </p>
        ) : (
          <p className="debate-page__lede debate-page__lede--muted">
            This lab is on this machine only. Point it at a git remote to share a
            thread with outside experts and their agents — see the hosting
            runbook.
          </p>
        )}
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
                {watchesForPeerPosts(state.run.status) && (
                  <span className="debate-detail__continue">
                    <label>
                      <input
                        type="number"
                        min={1}
                        max={10}
                        value={moreRounds}
                        onChange={(event) =>
                          setMoreRounds(Number(event.target.value) || 1)
                        }
                        disabled={busy}
                      />
                      more rounds
                    </label>
                    <button
                      onClick={handleContinue}
                      disabled={busy}
                      title={
                        "Attaches a fresh roster and argues on in the same " +
                        "thread, so an objection raised after the verdict gets " +
                        "answered against the argument that produced it."
                      }
                    >
                      Continue the debate
                    </button>
                  </span>
                )}
              </div>

              {state.simulations && state.simulations.length > 0 && (
                <ul className="debate-sims">
                  {state.simulations.map((sim) => {
                    const standing = simulationStanding(sim);
                    return (
                      <li
                        key={sim.job_id}
                        className={`debate-sims__row debate-sims__row--${standing.tone}`}
                      >
                        <strong>{sim.job_name ?? sim.job_id}</strong>
                        <span className="debate-sims__where">
                          {sim.cluster} · job {sim.job_id}
                        </span>
                        <span className="debate-sims__standing">{standing.label}</span>
                        <span
                          className="debate-sims__why"
                          title={`Commissioned by ${sim.commissioned_by} to test: ${sim.prediction}`}
                        >
                          {sim.prediction}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}

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
              {state.run.activity && (
                <Working
                  activity={state.run.activity}
                  since={state.run.activity_since}
                />
              )}

              <DebateThread
                posts={posts}
                enrolled={state.enrolled_origins ?? {}}
              />

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
