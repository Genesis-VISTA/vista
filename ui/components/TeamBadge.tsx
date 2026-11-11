"use client";

import { useTeams } from "@/lib/collaboration";
import { useTourExamples } from "@/lib/onboarding-examples";

/**
 * A resource's team, beside its name, when it is in one ("Public" for the
 * public team). Nothing when collaboration is not shown or the team's name
 * is not known (`TeamsView.badges`), or for a private one: every
 * page lists all of a researcher's projects, skills and knowledge bases
 * together, and this is how they tell which team each is in.
 *
 * One of the tour's examples passes `exampleName`, its team's name (null for a
 * private one), shown during the collaboration step whatever the researcher's
 * state: the real teams don't hold the examples'.
 */
export function TeamBadge({
  teamId,
  exampleName,
}: {
  teamId: string | null | undefined;
  exampleName?: string | null;
}) {
  const view = useTeams();
  const examples = useTourExamples();
  const name =
    exampleName !== undefined ? (examples ? exampleName : null) : view.badges ? view.teamName(teamId) : null;
  if (!name) return null;
  return (
    <span className="team-badge" title={`In the team ${name}`}>
      {name}
    </span>
  );
}

export default TeamBadge;
