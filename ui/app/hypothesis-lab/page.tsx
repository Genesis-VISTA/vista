"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import DebateThread from "@/components/DebateThread";
import DebateVerdict from "@/components/DebateVerdict";
import { RoleModelSelects } from "@/components/RoleModelSelects";
import {
  DEBATE_ROLES,
  DebatePost,
  DebateState,
  ForumStatus,
  ForumThread,
  RoleModels,
  acceptsPosts,
  canClose,
  canContinue,
  changedModels,
  chosenModels,
  closeDebate,
  continueDebate,
  closedBy,
  fetchForumStatus,
  fetchDebate,
  forumWebUrl,
  isActive,
  isArguing,
  liveRunLine,
  modelsOf,
  noRoleModels,
  readablePosts,
  simulationStanding,
  threadStatusLabel,
  listDebates,
  openDebate,
  postToDebate,
  publishErrorNotice,
} from "@/lib/debates";
import { useAgentSettings } from "@/lib/agent-settings";
import { displayModelName } from "@/lib/models";
import { useActiveProject } from "@/lib/projects";

const LIST_POLL_MS = 30_000;
/**
 * How often the list of threads is re-read while the lab is open, so a thread a
 * teammate opens appears without a reload. The backend fetches the forum at most
 * this often per project, and not at all while no lab is open.
 */

const QUIET_POLL_MS = 15_000;
/**
 * How often an open thread nobody is arguing in is re-read, for a teammate's
 * comment or run. Above the backend's per-thread refresh interval, so a poll
 * finds fresh news or a cheap cache hit.
 */

const FORUM_STATUS_POLL_MS = 15_000;
/**
 * How often the forum's status is re-read while the lab is open, so a failed
 * publish is explained without a reload. Reading it touches only this machine's
 * copy of the forum, never the remote.
 */

/* ---------------------------------------------------------------------- */
/*  Live updates                                                           */
/* ---------------------------------------------------------------------- */

/**
 * Follow the open thread over SSE: its posts, this install's run's activity, and
 * the thread itself (status, a live run, the current verdict).
 *
 * Open while someone is arguing in the thread, here or on another install, so
 * a teammate's run arrives live as well as ours. Posts are
 * keyed by `post_id`, so a reconnect that replays the thread converges.
 */
function useThreadStream(
  projectName: string | null,
  threadId: string | null,
  open: boolean,
  // Both callers wrap these in `useCallback`, so they are stable and can sit in
  // the dependency array directly.
  onPost: (post: DebatePost) => void,
  onThread: (thread: ForumThread) => void,
  onActivity: (activity: string | null, since: string | null) => void
) {
  useEffect(() => {
    if (!projectName || !threadId || !open) return;

    const params = new URLSearchParams({ project_name: projectName, thread_id: threadId });
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
    source.addEventListener("thread", (event) => {
      try {
        onThread(JSON.parse((event as MessageEvent).data));
      } catch {
        /* as above */
      }
    });

    return () => source.close();
  }, [projectName, threadId, open, onPost, onThread, onActivity]);
}

/**
 * What this install's run is doing, and for how long.
 *
 * The elapsed count is the part that carries the weight: a number that keeps
 * moving is the difference between waiting and wondering.
 */
function Working({ activity, since }: { activity: string; since: string | null }) {
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

/** Another install's run, as the thread shows it; re-said every half minute. */
function PeerRun({ thread }: { thread: ForumThread }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  if (!thread.live_run || thread.live_run.ours) return null;
  return (
    <p className="debate-working" aria-live="polite">
      <span className="debate-working__pulse" aria-hidden="true" />
      {liveRunLine(thread.live_run, now)}
    </p>
  );
}

/** "round 2/5" for this install's run in the thread, or nothing. */
function roundsOf(thread: ForumThread): string {
  return thread.run ? ` · round ${thread.run.rounds_done}/${thread.run.rounds}` : "";
}

/* ---------------------------------------------------------------------- */
/*  Page                                                                   */
/* ---------------------------------------------------------------------- */

function DebatesPage() {
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;

  const [threads, setThreads] = useState<ForumThread[]>([]);
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
  const { settings: agentSettings } = useAgentSettings();
  /** A model per role for the debate being opened; null is the default. */
  const [roleModels, setRoleModels] = useState<RoleModels>(noRoleModels);
  /** The same for Continue, starting from the models this install last used here. */
  const [continueModels, setContinueModels] = useState<RoleModels>(noRoleModels);

  /** Put a thread's latest copy everywhere it is shown. */
  const showThread = useCallback((thread: ForumThread) => {
    setThreads((current) =>
      current.some((t) => t.thread_id === thread.thread_id)
        ? current.map((t) => (t.thread_id === thread.thread_id ? thread : t))
        : [thread, ...current]
    );
    setState((current) =>
      current && current.thread.thread_id === thread.thread_id
        ? { ...current, thread }
        : current
    );
  }, []);

  // Every thread on the forum, re-read while the lab is open: a thread a
  // teammate opens appears within a poll. The backend fetches the forum at
  // most every 30 s per project however many tabs ask.
  useEffect(() => {
    if (!projectName) return;
    const controller = new AbortController();
    const load = async () => {
      try {
        const list = await listDebates(projectName, controller.signal);
        setThreads(list);
        setSelectedId((current) => current ?? list[0]?.thread_id ?? null);
      } catch (err) {
        if (!controller.signal.aborted) setError(String(err));
      }
    };
    void load();
    const timer = setInterval(load, LIST_POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [projectName]);

  // The forum's status, kept current while the page is open, and re-read at
  // once whenever the open thread's count of unpublished posts changes, which is
  // when a push has just been tried. A failed read keeps what is shown.
  const unpublishedHere =
    state?.posts.filter((post) => post.published === false).length ?? 0;
  useEffect(() => {
    if (!projectName) {
      setForum(null);
      return;
    }
    const controller = new AbortController();
    const load = () =>
      fetchForumStatus(projectName, controller.signal)
        .then(setForum)
        .catch(() => {
          /* the banner is advisory; its absence should not shout */
        });
    void load();
    const timer = setInterval(load, FORUM_STATUS_POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [projectName, unpublishedHere]);

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

  // Someone arguing in the thread, here or on another install: follow it live.
  const streaming = state
    ? acceptsPosts(state.thread, state.posts) && isArguing(state.thread)
    : false;
  // Nobody arguing, but the thread is still open: a teammate may comment or
  // start a run, so re-read it now and then. Re-fetching is what makes the
  // server read the forum; it throttles that per thread however many watch.
  const quiet = state ? acceptsPosts(state.thread, state.posts) && !streaming : false;
  useEffect(() => {
    if (!projectName || !selectedId || !quiet) return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      fetchDebate(projectName, selectedId, controller.signal)
        .then(setState)
        .catch(() => {
          /* a failed poll should not disturb a page that is already readable */
        });
    }, QUIET_POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [projectName, selectedId, quiet]);

  useThreadStream(
    projectName,
    state?.thread.thread_id ?? null,
    streaming,
    useCallback((post: DebatePost) => {
      setState((current) => {
        if (!current) return current;
        if (current.posts.some((p) => p.post_id === post.post_id)) {
          // A replayed post can still carry a newer vote tally.
          return {
            ...current,
            posts: current.posts.map((p) => (p.post_id === post.post_id ? post : p)),
          };
        }
        return { ...current, posts: [...current.posts, post] };
      });
    }, []),
    showThread,
    useCallback((activity: string | null, since: string | null) => {
      setState((current) =>
        current && current.thread.run
          ? {
              ...current,
              thread: {
                ...current.thread,
                run: { ...current.thread.run, activity, activity_since: since },
              },
            }
          : current
      );
    }, [])
  );

  const reload = async (threadId: string) => {
    if (!projectName) return;
    try {
      setState(await fetchDebate(projectName, threadId));
    } catch {
      /* the stream will catch up */
    }
  };

  const handleOpen = async () => {
    if (!projectName || !topic.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const thread = await openDebate(projectName, {
        topic: topic.trim(),
        framing: framing.trim() || null,
        rounds,
        models: chosenModels(roleModels),
      });
      setTopic("");
      setFraming("");
      setRoleModels(noRoleModels());
      showThread(thread);
      setSelectedId(thread.thread_id);
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
      const post = await postToDebate(projectName, state.thread.thread_id, {
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
      showThread(await closeDebate(projectName, state.thread.thread_id));
      await reload(state.thread.thread_id);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleContinue = async () => {
    if (!projectName || !state) return;
    setBusy(true);
    setError(null);
    try {
      const thread = await continueDebate(
        projectName,
        state.thread.thread_id,
        moreRounds,
        changedModels(continueModels, participantModels)
      );
      // The response is the thread as it stood when the request returned; the
      // run raises its budget from its own session a moment later. Show it as
      // arguing, and re-read so this install's new roster appears.
      showThread(
        thread.run ? { ...thread, run: { ...thread.run, status: "debating" } } : thread
      );
      await reload(state.thread.thread_id);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  // In the order the forum accepted them, which is the order the backend sends.
  const allPosts = useMemo(() => state?.posts ?? [], [state]);
  const posts = useMemo(() => readablePosts(allPosts), [allPosts]);

  // The models this install's roster recorded here. Continue starts from them,
  // or from the defaults in a thread this install has not argued in.
  const participantModels = useMemo(
    () => modelsOf(state?.participants ?? []),
    [state?.participants]
  );
  const participantModelsKey = JSON.stringify(participantModels);
  useEffect(() => {
    setContinueModels(participantModels);
    // Only when the thread or its recorded models change, not on every re-read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.thread.thread_id, participantModelsKey]);

  /**
   * This install's agents in the thread, one row per role, in role order: its
   * model (a role on the default says so) and the tools it may use. A thread
   * this install has not argued in has none, and shows no roster.
   */
  const roster = DEBATE_ROLES.flatMap((role) => {
    const participant = state?.participants.find((p) => p.debate_role === role);
    if (!participant) return [];
    const model = participantModels[role];
    const shown = model
      ? displayModelName(model)
      : `Default (${agentSettings?.model ? displayModelName(agentSettings.model) : "default"})`;
    return [{ role, participant, model: shown }];
  });

  // Why posts are not reaching the forum. An error, so it is drawn as one, and
  // it sits on the open thread, whose posts are the ones marked "not yet
  // published". With no thread open it falls back to the top.
  const publishNotice = forum?.publish_error ? (
    <p className="debate-publish-error" role="alert">
      <strong>{publishErrorNotice(forum.publish_error).headline}</strong>{" "}
      {publishErrorNotice(forum.publish_error).detail}
      {forum.unpublished > 0 && (
        <>
          {" "}
          {forum.unpublished === 1 ? "1 post is" : `${forum.unpublished} posts are`} kept here,
          marked <em>not yet published</em>, until it works.
        </>
      )}
    </p>
  ) : null;

  const syncedTo =
    forum && forum.shared && forum.remote && !forum.publish_error && forum.unpublished === 0
      ? forum.remote
      : null;
  const syncedLink = syncedTo ? forumWebUrl(syncedTo) : null;
  const forumNotice =
    publishNotice ??
    (syncedTo ? (
      <p className="debate-note" role="status">
        Synced with{" "}
        {syncedLink ? (
          <a href={syncedLink} target="_blank" rel="noreferrer">
            {syncedTo}
          </a>
        ) : (
          <code>{syncedTo}</code>
        )}
      </p>
    ) : null);

  if (!projectName) {
    return <main className="debate-page"><p>Select a project first.</p></main>;
  }

  // No git on this machine turns every project's lab off, and the fix is on the
  // machine rather than in the project, so it gets its own message.
  if (forum !== null && !forum.enabled && !forum.git_ok) {
    return (
      <main className="debate-page">
        <header className="debate-page__header">
          <h1>Hypothesis Lab</h1>
          <p className="debate-warning">
            <strong>The Hypothesis Lab needs git.</strong>{" "}
            {forum.git_reason ?? "Git could not be found on this machine."}
          </p>
          <p className="debate-page__lede debate-page__lede--muted">
            Debates are kept in a git repository and published with your own git
            and its credentials, so the lab turns on once git 2.34 or later is
            installed. Reload this page afterwards. The rest of VISTA works
            without it.
          </p>
        </header>
      </main>
    );
  }

  // A project with no forum repository has no lab, and the honest thing is to
  // say so and point at the one place it can be turned on.
  if (forum !== null && !forum.enabled) {
    return (
      <main className="debate-page">
        <header className="debate-page__header">
          <h1>Hypothesis Lab</h1>
          <p className="debate-page__lede">
            <strong>{projectName}</strong> has no lab yet.
          </p>
          <p className="debate-page__lede debate-page__lede--muted">
            A lab needs a git repository to publish its debates to: push access
            to that repository is who may post, so it is the guest list for the
            room. Add one under <strong>Hypothesis Lab</strong>{" "}
            in the project&rsquo;s settings and this page turns on — along with any threads the
            repository already holds.
          </p>
          <p className="debate-page__lede">
            <Link className="debate-link" href="/projects">
              Open project settings
            </Link>
          </p>
        </header>
      </main>
    );
  }

  const thread = state?.thread ?? null;
  const othersRun = thread?.live_run && !thread.live_run.ours ? thread.live_run : null;

  return (
    <main className="debate-page">
      <header className="debate-page__header">
        <h1>Hypothesis Lab</h1>
        <p className="debate-page__lede">
          Three local agents — a Proposer, a Reviewer whose job is to falsify it, and a Referee who rules — argue a topic until a hypothesis survives. You can join any thread on the forum, continue it with your own agents, or end a thread you opened.
        </p>
        {/* `forum` is null until the status fetch lands, and null is "not known
            yet" rather than "not shared". */}
        {forum === null ? null : forum.shared ? (
          <p className="debate-page__lede">
            The forum lives in a git repository this deployment publishes to. Everyone
            with access sees every thread here, can post into it, and can continue it
            with their own agents.
          </p>
        ) : (
          <p className="debate-page__lede debate-page__lede--muted">
            This lab is on this machine only. Point it at a git remote to share
            threads with colleagues and their agents — see the hosting runbook.
          </p>
        )}
      </header>

      {error && <p className="debate-error">{error}</p>}

      {!state && publishNotice}

      {forum !== null && !forum.publish_error && forum.unpublished > 0 && (
        <p className="debate-note">
          <strong>
            {forum.unpublished === 1
              ? "1 post has"
              : `${forum.unpublished} posts have`}{" "}
            not reached the forum yet.
          </strong>{" "}
          {forum.unpublished === 1 ? "It was" : "They were"} written while the
          forum could not be reached and {forum.unpublished === 1 ? "is" : "are"}{" "}
          safe here, publishing on the next successful sync, and marked{" "}
          <em>not yet published</em> until then.
        </p>
      )}

      {forum?.shared && (
        <p className="debate-note">
          This forum is shared. Posts marked <strong>peer-claimed</strong>{" "}
          came from another machine: their name and role are that participant&rsquo;s
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
        <div>
          <span className="debate-open__hint">Model per role (optional)</span>
          <RoleModelSelects value={roleModels} onChange={setRoleModels} />
        </div>
        <button onClick={handleOpen} disabled={busy || !topic.trim()}>
          Open debate
        </button>
      </section>

      <div className="debate-layout">
        <aside className="debate-list">
          {threads.length === 0 && <p>No debates yet.</p>}
          {threads.map((item) => (
            <button
              key={item.thread_id}
              className={`debate-list__item${
                item.thread_id === selectedId ? " debate-list__item--active" : ""
              }`}
              onClick={() => setSelectedId(item.thread_id)}
            >
              <span className="debate-list__topic">{item.title}</span>
              <span className="debate-list__status">
                {threadStatusLabel(item, [])}
                {roundsOf(item)}
                {" · "}
                {item.opened_here ? "opened here" : "opened by another install"}
              </span>
            </button>
          ))}
        </aside>

        <section className="debate-detail">
          {!state && <p>Select a debate.</p>}
          {state && thread && (
            <>
              <div className="debate-detail__head">
                <h2>{thread.title}</h2>
                <span className="debate-detail__status">
                  {threadStatusLabel(thread, allPosts)}
                  {thread.run && isActive(thread.run.status) ? roundsOf(thread) : ""}
                  {thread.opened_here ? "" : " · opened by another install"}
                </span>
                {canClose(thread, allPosts) && (
                  <button onClick={handleClose} disabled={busy}>
                    End this debate
                  </button>
                )}
                {canContinue(thread, allPosts) && (
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
                        thread.run
                          ? "Your agents argue on in the same thread with the roster " +
                            "they used here, reading everything posted since."
                          : "Your own Proposer, Reviewer and Referee argue in this " +
                            "thread, reading everything in it, and your Referee rules."
                      }
                    >
                      {thread.run ? "Continue the debate" : "Continue with your agents"}
                    </button>
                    <RoleModelSelects
                      value={continueModels}
                      onChange={setContinueModels}
                    />
                  </span>
                )}
              </div>

              {forumNotice}

              {othersRun && (
                <p className="debate-note">
                  Another user&rsquo;s agents are arguing in this thread. You
                  can post into it now; you can continue it with your own agents
                  once their run ends.
                </p>
              )}

              {thread.thread_missing && (
                <p className="debate-warning">
                  <strong>This thread is no longer on the forum.</strong>{" "}
                  It was
                  deleted from the repository, or it was written before the lab
                  moved to plain git. What is shown here is this machine&rsquo;s
                  copy; it can be read but not posted to or continued.
                </p>
              )}

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

              {roster.length > 0 && (
                <ul className="debate-roster">
                  {roster.map(({ role, participant, model }) => (
                    <li key={participant.id} title={participant.identity}>
                      <strong>{role}</strong>{" "}
                      <span className="debate-roster__model">{model}</span>
                      {" · "}
                      <span className="debate-roster__tools">
                        {participant.granted_tools.length > 0
                          ? `may use: ${participant.granted_tools.join(", ")}`
                          : "no tools granted"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <DebateVerdict
                current={thread.current_verdict}
                endedEarly={thread.status === "closed" || closedBy(allPosts) !== null}
              />
              {thread.run?.activity && (
                <Working
                  activity={thread.run.activity}
                  since={thread.run.activity_since}
                />
              )}
              <PeerRun thread={thread} />

              <DebateThread posts={posts} />

              {acceptsPosts(thread, allPosts) && (
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
              )}
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
