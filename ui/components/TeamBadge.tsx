"use client";

import { useTeams } from "@/lib/collaboration";

/**
 * A resource's team, beside its name, when it is in one ("Public" for the
 * public team). Nothing when collaboration is not shown or the team's name
 * is not known (`TeamsView.badges`), or for a private one: every
 * page lists all of a researcher's projects, skills and knowledge bases
 * together, and this is how they tell which team each is in.
 */
export function TeamBadge({ teamId }: { teamId: string | null | undefined }) {
  const view = useTeams();
  if (!view.badges) return null;
  const name = view.teamName(teamId);
  if (!name) return null;
  return (
    <span className="team-badge" title={`In the team ${name}`}>
      {name}
    </span>
  );
}

export default TeamBadge;
