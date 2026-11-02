"use client";

import { useState } from "react";
import {
  copyToTeam,
  teamLabel,
  useTeams,
  type CopiedResource,
  type CopyKind,
  type TeamCopyResult,
} from "@/lib/collaboration";

const NOUNS: Record<CopyKind, string> = {
  project: "project",
  skill: "skill",
  "knowledge-base": "knowledge base",
};

const HOW: Record<CopiedResource["how"], string> = {
  copied: "copied on the server",
  published: "published from this computer",
  imported: "imported beside the original",
};

/** The team a copy could go to: any of the researcher's but the one the resource is in. */
export function copyTargets(
  teams: ReturnType<typeof useTeams>["teams"],
  currentTeamId: string | null | undefined,
) {
  return teams.filter((t) => t.id !== (currentTeamId ?? null));
}

/**
 * Copy a project, skill or knowledge base to a team: how a private one is
 * shared, and how one already in a team reaches another. The original item on
 * the server stays where it is; here the resource becomes the team's copy,
 * except one another project here still uses, which stays as it was with the
 * copy imported beside it under a new name, so that project keeps working.
 *
 * A project brings everything it uses, since a project uses only its own
 * team's skills and knowledge bases; the result lists each, the project last.
 */
export function CopyToTeamModal({
  kind,
  name,
  label,
  teamId,
  onClose,
  onCopied,
}: {
  kind: CopyKind;
  /** Its name here (a knowledge base's slug), which the copy route takes. */
  name: string;
  /** What to call it, when that is not `name`. */
  label?: string;
  /** Its team now; null when private. */
  teamId: string | null | undefined;
  onClose: () => void;
  /** After a copy, so the page can list the resources again. */
  onCopied: (result: TeamCopyResult) => void;
}) {
  const view = useTeams();
  const targets = copyTargets(view.teams, teamId);
  const [picked, setPicked] = useState<string | null>(null);
  // The first team until one is picked: the teams may arrive after the modal opens.
  const target = picked && targets.some((t) => t.id === picked) ? picked : (targets[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TeamCopyResult | null>(null);
  const noun = NOUNS[kind];
  const shown = label ?? name;

  async function copy() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const copied = await copyToTeam(kind, name, target);
      setResult(copied);
      onCopied(copied);
    } catch (e) {
      setError(e instanceof Error ? e.message : "It could not be copied.");
    } finally {
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
        aria-label={`Copy ${noun} to a team`}
      >
        <div className="panel-header">
          <div className="panel-title">Copy {shown} to a team</div>
          <button type="button" className="button ghost" onClick={onClose} disabled={busy}>
            {result ? "Done" : "Cancel"}
          </button>
        </div>
        <div className="modal-body teams-modal-body">
          {result ? (
            <>
              <p className="teams-modal-note" role="status">
                Copied to {targetName}: its members can now see and import{" "}
                {result.copied.length === 1 ? "it" : "them"}. Here,{" "}
                {result.copied.length === 1 ? "it is" : "they are"} listed under{" "}
                {targetName} in the team menu.
              </p>
              <ul className="teams-member-list" aria-label="Copied">
                {result.copied.map((c) => (
                  <li key={`${c.kind}:${c.name}`} className="teams-member">
                    <span className="teams-member-name">
                      {NOUNS[c.kind]} {c.name}
                    </span>
                    <span className="teams-member-email">
                      {HOW[c.how]}
                      {c.local_name !== c.name
                        ? `, here as ${c.local_name}: another project here still uses ${c.name}, so it stays as it was`
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : targets.length === 0 ? (
            <p className="teams-modal-note">
              There is no other team to copy it to. Make one from the team menu in the sidebar.
            </p>
          ) : (
            <>
              <p className="teams-modal-note">
                {teamId
                  ? `The copy is a new item in that team; the one in ${view.teamName(teamId) ?? "its team"} stays as it is.`
                  : `It is private, on this computer only. The copy is published into that team, whose members can then see and import it.`}{" "}
                Here, it becomes the team&rsquo;s copy.
              </p>
              {kind === "project" && (
                <p className="teams-modal-note">
                  A project uses only its own team&rsquo;s skills and knowledge
                  bases, so its skills and knowledge bases are copied with it,
                  first. One that another project here still uses is left as it
                  was for that project, and its copy is imported beside it under a
                  new name.
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
                  {targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {teamLabel(t)}
                    </option>
                  ))}
                </select>
              </label>
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
                  onClick={() => void copy()}
                  disabled={busy || !target}
                >
                  {busy ? "Copying…" : "Copy"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default CopyToTeamModal;
