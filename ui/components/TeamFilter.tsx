"use client";

import { useCallback, useState } from "react";
import { teamLabel, useTeams, type CollaborationTeam } from "@/lib/collaboration";
import { EXAMPLE_TEAMS, useTourExamples } from "@/lib/onboarding-examples";
import { ExampleTag } from "@/components/ExampleTag";

/** Every team, or one team's id. */
export type TeamFilterValue = "all" | string;

/**
 * The teams to pick between, or null when the filter is hidden: the
 * researcher's while collaboration is shown, and the tour's example teams for
 * everyone during its collaboration step.
 */
function useFilterTeams(): { teams: readonly CollaborationTeam[]; example: boolean } | null {
  const view = useTeams();
  const example = useTourExamples();
  if (example) return { teams: EXAMPLE_TEAMS, example };
  return view.shown ? { teams: view.teams, example } : null;
}

/**
 * A listing's team filter: the team picked, while it is still one of the
 * researcher's and there are teams enough to pick between; otherwise every
 * team. So a team left since it was picked, or the filter hidden, never keeps
 * narrowing the listing. An example team picked during the tour narrows no
 * real listing once the step ends.
 */
export function useTeamFilter(): {
  value: TeamFilterValue;
  setValue: (value: TeamFilterValue) => void;
  /** Whether an item of `teamId` is listed. */
  matches: (teamId: string | null | undefined) => boolean;
} {
  const shown = useFilterTeams();
  const [picked, setPicked] = useState<TeamFilterValue>("all");
  const value =
    shown && shown.teams.length >= 2 && shown.teams.some((t) => t.id === picked) ? picked : "all";
  // The same function while the value is, so a listing can memoize on it.
  const matches = useCallback(
    (teamId: string | null | undefined) => value === "all" || value === teamId,
    [value],
  );
  return { value, setValue: setPicked, matches };
}

/**
 * Narrows a listing of the server's items to one of the researcher's teams.
 * Every listing shows all their teams' items, each with its team, so this is
 * only a convenience: page state, never a setting, and nothing new joins the
 * team chosen here. Hidden with fewer than two teams, where there is nothing
 * to narrow. Its value comes from `useTeamFilter`.
 */
export function TeamFilter({
  value,
  onChange,
  className = "hub-sort",
}: {
  value: TeamFilterValue;
  onChange: (value: TeamFilterValue) => void;
  className?: string;
}) {
  const shown = useFilterTeams();
  if (!shown || shown.teams.length < 2) return null;
  return (
    <>
      <select
        className={className}
        aria-label="Team"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-tour={shown.example ? "collaboration:teams" : undefined}
      >
        <option value="all">All teams</option>
        {shown.teams.map((t) => (
          <option key={t.id} value={t.id}>
            {teamLabel(t)}
          </option>
        ))}
      </select>
      {shown.example && <ExampleTag />}
    </>
  );
}

export default TeamFilter;
