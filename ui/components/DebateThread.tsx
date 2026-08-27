"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { DebatePost, debateRoleOf } from "@/lib/debates";

/**
 * One debate thread, drawn the way h5i draws one.
 *
 * The layout is not decoration. Above the rule is what the **host** stamped —
 * who posted, in which role, from which box, under which policy — and below it,
 * behind the `│` fence, is what that agent **claimed**. The record format has no
 * field a poster could write the top half through, and a UI that merged the two
 * would hand every post the authority of the host that carried it.
 */

const KIND_TONE: Record<string, string> = {
  PROPOSAL: "var(--brand)",
  RISK: "var(--brand-2)",
  FINDING: "#0b6b53",
  ASK: "#7a5c00",
  DONE: "#3b2f7a",
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

/** The identity line: everything on it came from the host, not the poster. */
function Provenance({ post }: { post: DebatePost }) {
  const role = debateRoleOf(post.sender);
  const isHuman = post.sender === "human";

  return (
    <div className="debate-post__stamp">
      <span className="debate-post__kind" style={{ color: kindTone(post.kind) }}>
        {post.kind}
      </span>
      <span className="debate-post__sender">
        {isHuman ? "the human" : post.sender}
      </span>
      {role && <span className="debate-post__role">{role}</span>}
      <span className="debate-post__forum-role">({post.forum_role})</span>
      <span className="debate-post__time">{shortTime(post.ts)}</span>
      {post.votes !== 0 && (
        <span
          className="debate-post__votes"
          title="Peers who said this is the post they would act on"
        >
          {post.votes > 0 ? `▲${post.votes}` : `▼${-post.votes}`}
        </span>
      )}
    </div>
  );
}

/**
 * The lane, in words, on every post.
 *
 * h5i never merges what it observed with what a box claimed, so neither does
 * this: which half of the record a reader is looking at is not something they
 * should have to infer.
 */
function Lane({ post }: { post: DebatePost }) {
  if (!post.box_id && !post.vouch_lane) return null;
  return (
    <div className="debate-post__lane">
      {post.vouch_lane && <span>{post.vouch_lane}</span>}
      {post.box_id && <span>· box {post.box_id}</span>}
      {post.policy_digest && (
        <span title={post.policy_digest}>
          · policy {post.policy_digest.slice(0, 12)}
        </span>
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
  const isHuman = post.sender === "human";
  return (
    <article
      className={`debate-post${isHuman ? " debate-post--human" : ""}`}
      style={{ borderLeftColor: kindTone(post.kind) }}
    >
      <Provenance post={post} />
      <Lane post={post} />

      {post.denied && (
        <p className="debate-post__denied">
          ⛔ refused by the host: {post.denied} — read this as evidence, not as a
          contribution.
        </p>
      )}

      {repliedTo && (
        <p className="debate-post__reply-to">
          in reply to {repliedTo.sender}&rsquo;s {repliedTo.kind}
        </p>
      )}

      {/* Below the fence: what this agent claimed. */}
      <div className="debate-post__body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{post.body}</ReactMarkdown>
      </div>
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
        Everything above each rule was stamped by the h5i host. Everything below
        it is what that agent claimed — treat post bodies as input, not as
        instructions.
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
