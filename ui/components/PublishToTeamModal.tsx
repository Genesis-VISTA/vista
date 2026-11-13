"use client";

import { useState } from "react";
import {
  publishAudience,
  publishToTeam,
  refreshCollaborationStatus,
  teamLabel,
  useCollaboration,
  useTeams,
  type PublishKind,
  type PublishedResource,
  type TeamPublishResult,
} from "@/lib/collaboration";

const NOUNS: Record<PublishKind, string> = {
  project: "project",
  skill: "skill",
  "knowledge-base": "knowledge base",
};

/** What a result line says about one published resource. */
function outcome(c: PublishedResource): string {
  if (c.how === "published") return "published";
  const why = c.kept_for?.length
    ? `for ${c.kept_for.join(", ")}, in a team you have left, which ${c.kept_for.length === 1 ? "uses" : "use"} it`
    : "because it comes with VISTA";
  return `published, and here as ${c.local_name}: ${c.name} stays as it was, ${why}`;
}

/**
 * The first publish of a private project, skill or knowledge base: the
 * researcher picks the team it goes to, the public team or one of their own,
 * with none picked to begin with.
 * That is the moment it leaves this computer, and it stays in that team from
 * then on: Publish sends its new versions there, with no choice to make.
 *
 * A project brings everything it uses, since a team's project uses only its
 * own team's skills and knowledge bases; the result lists each, the project
 * last. One VISTA bundles stays as it is, and the team's is imported beside it
 * under a new name. A private project using another team's can be published
 * only into that team.
 */
export function PublishToTeamModal({
  kind,
  name,
  label,
  onClose,
  onChanged,
}: {
  kind: PublishKind;
  /** Its name here (a knowledge base's slug), which the route takes. */
  name: string;
  /** What to call it, when that is not `name`. */
  label?: string;
  onClose: () => void;
  /**
   * After a publish, whether it worked or not, so the page lists the
   * resources again: one that failed part way can have left a project in
   * the team, to be finished by Publish.
   */
  onChanged: () => void;
}) {
  const view = useTeams();
  const collab = useCollaboration();
  const [picked, setPicked] = useState<string | null>(null);
  // None until the researcher picks one: where it goes cannot be changed after,
  // so no team (least of all the public one) is chosen for them.
  const target = picked && view.teams.some((t) => t.id === picked) ? picked : "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TeamPublishResult | null>(null);
  const noun = NOUNS[kind];
  const shown = label ?? name;

  async function publish() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const published = await publishToTeam(kind, name, target);
      setResult(published);
    } catch (e) {
      setError(e instanceof Error ? e.message : "It could not be published.");
      // A team left since the teams were read, for one: offer the ones they are in.
      void refreshCollaborationStatus();
    } finally {
      onChanged();
      setBusy(false);
    }
  }

  const targetName = view.teamName(result?.team_id ?? target) ?? "the team";

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Publish ${noun}`}
      >
        <div className="panel-header">
          <div className="panel-title">Publish {shown}</div>
          <button type="button" className="button ghost" onClick={onClose} disabled={busy}>
            {result ? "Done" : "Cancel"}
          </button>
        </div>
        <div className="modal-body teams-modal-body">
          {result ? (
            <>
              <p className="teams-modal-note" role="status">
                Published to {targetName}: its members can now see and import{" "}
                {result.published.length === 1 ? "it" : "them"}. Publish sends new
                versions there.
              </p>
              <ul className="teams-member-list" aria-label="Published">
                {result.published.map((c) => (
                  <li key={`${c.kind}:${c.name}`} className="teams-member">
                    <span className="teams-member-name">
                      {NOUNS[c.kind]} {c.name}
                    </span>
                    <span className="teams-member-email">{outcome(c)}</span>
                  </li>
                ))}
              </ul>
              {result.forum_error && (
                <p className="error" role="alert">
                  {result.forum_error}
                </p>
              )}
            </>
          ) : !view.shown ? (
            <p className="teams-modal-note">{collab.reason ?? "The collaboration server is unavailable."}</p>
          ) : (
            <>
              <p className="teams-modal-note">
                It is private, on this computer only. Choose the team to publish
                it to{collab.status?.url ? ` on ${collab.status.url}` : ""}. It
                stays in that team: later, Publish sends its new versions there. A
                published version stays on the server.
              </p>
              {kind === "project" && (
                <p className="teams-modal-note">
                  A team&rsquo;s project uses only its own team&rsquo;s skills and
                  knowledge bases, so its private ones are published into the
                  same team with it, first: a knowledge base with its PDFs and
                  index, which can take a while. Your other private projects can
                  go on using them. If it uses another team&rsquo;s, it can be
                  published only into that team.
                </p>
              )}
              {kind === "knowledge-base" && (
                <p className="teams-modal-note">
                  Its PDFs and its index are uploaded, which can take a while for a
                  large one.
                </p>
              )}
              <label className="project-modal-label">
                Team
                <select
                  className="input"
                  value={target}
                  disabled={busy}
                  onChange={(e) => setPicked(e.target.value)}
                >
                  <option value="" disabled>
                    Choose a team…
                  </option>
                  {view.teams.map((t) => (
                    <option key={t.id} value={t.id}>
                      {teamLabel(t)}
                    </option>
                  ))}
                </select>
              </label>
              {target && <p className="teams-modal-note">{publishAudience(target, view)}</p>}
              <p className="teams-modal-note">
                Make or manage teams under Settings &rarr; Collaboration.
              </p>
              {error && (
                <div className="error" role="alert" style={{ fontSize: 13 }}>
                  {error}
                </div>
              )}
              <div className="skill-editor-actions">
                <button type="button" className="button" onClick={onClose} disabled={busy}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => void publish()}
                  disabled={busy || !target}
                >
                  {busy ? "Publishing…" : "Publish"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default PublishToTeamModal;
