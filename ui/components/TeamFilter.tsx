"use client";

import { useState } from "react";
import { teamLabel, useTeams } from "@/lib/collaboration";

/** Every team, or one team's id. */
export type TeamFilterValue = "all" | string;

/**
 * A listing's team filter: the team picked, while it is still one of the
 * researcher's and there are teams enough to pick between; otherwise every
 * team. So a team left since it was picked, or the filter hidden, never keeps
 * narrowing the listing.
 */
export function useTeamFilter(): {
  value: TeamFilterValue;
  setValue: (value: TeamFilterValue) => void;
  /** Whether an item of `teamId` is listed. */
  matches: (teamId: string | null | undefined) => boolean;
} {
  const view = useTeams();
  const [picked, setPicked] = useState<TeamFilterValue>("all");
  const value =
    view.shown && view.teams.length >= 2 && view.teams.some((t) => t.id === picked) ? picked : "all";
  return {
    value,
    setValue: setPicked,
    matches: (teamId) => value === "all" || value === teamId,
  };
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
  const view = useTeams();
  if (!view.shown || view.teams.length < 2) return null;
  return (
    <select className={className} aria-label="Team" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="all">All teams</option>
      {view.teams.map((t) => (
        <option key={t.id} value={t.id}>
          {teamLabel(t)}
        </option>
      ))}
    </select>
  );
}

export default TeamFilter;
