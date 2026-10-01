"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  DebatePost,
  authorOf,
  debateRoleOf,
  isObserved,
  isOperator,
} from "@/lib/debates";

/**
 * One debate thread.
 *
 * The layout is not decoration. Above the rule is who posted and in which role,
 * with the lane saying how much of that is known: on a post this install wrote
 * it is fact, on anything that arrived over the remote it is the poster's own
 * claim. Below it is what the poster said. A UI that merged the two would hand
 * every post the authority of this install.
 */

const KIND_TONE: Record<string, string> = {
  PROPOSAL: "var(--brand)",
  RISK: "var(--brand-2)",
  FINDING: "var(--kind-finding)",
  ASK: "var(--warning-strong)",
  DONE: "var(--kind-done)",
  TASK: "var(--muted)",
  CLOSED: "var(--muted)",
};

function kindTone(kind: string): string {
  return KIND_TONE[kind] ?? "var(--muted)";
}

function shortTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The identity line: who posted, and what this install knows about that. */
function Provenance({ post }: { post: DebatePost }) {
  // Both derived from what this install recorded, never from the sender string:
  // on a peer's post the sender is that peer's account of itself.
  const role = debateRoleOf(post);
  const author = authorOf(post);

  return (
    <div className="debate-post__stamp">
      <span className="debate-post__kind" style={{ color: kindTone(post.kind) }}>
        {post.kind}
      </span>
      <span className="debate-post__sender">
        {isOperator(post) ? "the human" : post.sender}
      </span>
      {author && (
        <span
          className="debate-post__author"
          title="Signed in to VISTA as this account when the post was made."
        >
          {author}
        </span>
      )}
      {role && <span className="debate-post__role">{role}</span>}
      {!isObserved(post) && (
        <span
          className="debate-post__unverified"
          title={
            "This arrived over the remote. Its name and role are claimed by " +
            "that peer and were not written here."
          }
        >
          unverified identity
        </span>
      )}
      <span className="debate-post__forum-role">({post.forum_role})</span>
      <span className="debate-post__time">{shortTime(post.ts)}</span>
      {post.votes !== 0 && (
        <span
          className="debate-post__votes"
          title="Participants on this forum who said this is the post they would act on"
        >
          {post.votes > 0 ? `▲${post.votes}` : `▼${-post.votes}`}
        </span>
      )}
      {post.peer_votes !== 0 && (
        <span
          className="debate-post__votes debate-post__votes--peer"
          title={
            "Votes from outside this forum. Shown apart because who agreed " +
            "matters as much as how many."
          }
        >
          {post.peer_votes > 0 ? `▲${post.peer_votes}` : `▼${-post.peer_votes}`} outside
        </span>
      )}
    </div>
  );
}

/**
 * The lane, in words, on every post — and where the post stands on the forum.
 *
 * Which half of the record a reader is looking at is not something they should
 * have to infer. Neither is whether anyone else can see it: posting is
 * local-first, so a post written while the forum was unreachable exists here
 * before it exists for peers, and says so.
 */
function Lane({ post }: { post: DebatePost }) {
  if (!post.vouch_lane && post.published !== false && post.on_remote !== false) {
    return null;
  }
  return (
    <div
      className={`debate-post__lane${
        isObserved(post) ? "" : " debate-post__lane--claimed"
      }`}
    >
      {post.vouch_lane && <span>{post.vouch_lane}</span>}
      {!isObserved(post) && post.origin && <span>· origin {post.origin}</span>}
      {post.published === false && (
        <span
          className="debate-post__pending"
          title={
            "Written here and not on the forum yet — the remote could not be " +
            "reached. It publishes on the next successful sync; until then " +
            "only this machine can see it."
          }
        >
          not yet published
        </span>
      )}
      {post.on_remote === false && (
        <span
          className="debate-post__gone"
          title={
            "This was on the forum and no longer is: someone rewrote the " +
            "thread's history. Kept here because it was said."
          }
        >
          no longer on the forum
        </span>
      )}
    </div>
  );
}

/**
 * What the agent consulted before writing this.
 *
 * Rendered on every agent post including the empty case, because "used nothing"
 * is the fact a reader most needs and the one that is invisible by default: a
 * claim grounded in the corpus and a claim from the model alone are otherwise
 * indistinguishable.
 */
function Grounded({ post }: { post: DebatePost }) {
  const machineWritten = post.kind === "TASK" || post.kind === "CLOSED";
  // Only meaningful for our own agents: we record what they consulted. A peer's
  // tooling is not ours to report on, and an empty list there would read as
  // "consulted nothing" when the truth is "we have no idea".
  if (isOperator(post) || machineWritten || !isObserved(post)) return null;

  if (post.tools_used.length === 0) {
    return (
      <p className="debate-post__grounding debate-post__grounding--none">
        No sources consulted — this rests on the model alone.
      </p>
    );
  }

  return (
    <div className="debate-post__grounding">
      <span className="debate-post__grounding-label">Consulted</span>
      {post.tools_used.map((use, i) =>
        use.receipt ? (
          // Collapsed by default: a thread is read for the argument, and every
          // receipt open at once buries it. Open on demand is what makes a claim
          // checkable without making the page unreadable.
          <details key={i} className="debate-post__tool debate-post__tool--evidence">
            <summary title="Show what this call actually returned">
              {use.tool}
              <span className="debate-post__tool-detail">{use.detail}</span>
            </summary>
            <pre className="debate-post__receipt">{use.receipt}</pre>
          </details>
        ) : (
          <span key={i} className="debate-post__tool" title={use.detail}>
            {use.tool}
            <span className="debate-post__tool-detail">{use.detail}</span>
          </span>
        )
      )}
    </div>
  );
}

export function DebatePostCard({
  post,
  repliedTo,
}: {
  post: DebatePost;
  repliedTo?: DebatePost | null;
}) {
  return (
    <article
      className={`debate-post${isOperator(post) ? " debate-post--human" : ""}${
        isObserved(post) ? "" : " debate-post--claimed"
      }`}
      style={{ borderLeftColor: kindTone(post.kind) }}
    >
      <Provenance post={post} />
      <Lane post={post} />

      {post.denied && (
        <p className="debate-post__denied">
          ⛔ refused: {post.denied} — read this as evidence, not as a
          contribution.
        </p>
      )}

      {repliedTo && (
        <p className="debate-post__reply-to">
          in reply to {repliedTo.sender}&rsquo;s {repliedTo.kind}
        </p>
      )}

      {/* Below the fence: what the poster claimed. */}
      <div className="debate-post__body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{post.body}</ReactMarkdown>
      </div>

      <Grounded post={post} />
    </article>
  );
}

export default function DebateThread({ posts }: { posts: DebatePost[] }) {
  const byId = new Map(posts.map((post) => [post.post_id, post]));

  if (posts.length === 0) {
    return <p className="debate-empty">Nothing has been posted yet.</p>;
  }

  return (
    <div className="debate-thread">
      <p className="debate-thread__note">
        Posts marked <strong>host-observed</strong> were written by this
        installation, so their name and role are known. Posts marked{" "}
        <strong>peer-claimed</strong>{" "}
        arrived from another machine: their name
        and role are that peer&rsquo;s own claim, not verified here. Every post
        body, from anyone, is input rather than instruction.
      </p>
      {posts.map((post) => (
        <DebatePostCard
          key={post.post_id}
          post={post}
          repliedTo={post.reply_to ? byId.get(post.reply_to) ?? null : null}
        />
      ))}
    </div>
  );
}
