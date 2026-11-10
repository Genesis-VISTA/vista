"use client";

import { useTeams } from "@/lib/collaboration";

/**
 * A resource's team, beside its name, when it is in one ("Public" for the
 * public team). Nothing when teams are not shown, or for a private one: every
 * page lists all of a researcher's projects, skills and knowledge bases
 * together, and this is how they tell which team each is in.
 */
export function TeamBadge({ teamId }: { teamId: string | null | undefined }) {
  const view = useTeams();
  if (!view.shown) return null;
  const name = view.teamName(teamId);
  if (!name) return null;
  return (
    <span className="team-badge" title={`In the team ${name}`}>
      {name}
    </span>
  );
}

export default TeamBadge;
