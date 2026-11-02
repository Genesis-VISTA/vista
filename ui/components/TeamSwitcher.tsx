"use client";

import { useEffect, useRef, useState, type DOMAttributes } from "react";
import { selectTeam, teamLabel, useTeams } from "@/lib/collaboration";
import { TeamsModal } from "./TeamsModal";

type TipProps = (label: string) => DOMAttributes<HTMLButtonElement>;

/**
 * The collaboration team switcher, at the top of the navigation rail.
 *
 * It sits in the rail rather than the page header because the team is wider
 * than any one page: it decides what the projects list, the Skills and
 * Knowledge Bases pages, the Skill Hub and every import list show, and which
 * team the next new project joins, which is why it sits just above "New
 * project". The header's crumb is the active project's, and a project is
 * inside a team, not beside it.
 *
 * Private comes first and is always there: what is on this computer only,
 * where everything made without a server, and VISTA's own seeds, are. Then
 * the public team, which everyone is in, then the researcher's own teams.
 *
 * Nothing at all is drawn unless a collaboration server answered and signed
 * this installation in: without one there is only Private, nothing to choose
 * between, and every page lists everything.
 */
export function TeamSwitcher({
  collapsed = false,
  tipProps = () => ({}),
}: {
  collapsed?: boolean;
  /** The rail's tooltip for a collapsed icon. */
  tipProps?: TipProps;
}) {
  const view = useTeams();
  const [open, setOpen] = useState(false);
  const [managing, setManaging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!view.shown) return null;

  const current = teamLabel(view.selected);
  // Private (null) first, then the server's teams in its order: Public first.
  const choices: { id: string | null; label: string }[] = [
    { id: null, label: "Private" },
    ...view.teams.map((t) => ({ id: t.id, label: teamLabel(t) })),
  ];

  async function choose(id: string | null) {
    setOpen(false);
    buttonRef.current?.focus();
    if (id === view.selectedId) return;
    setError(null);
    try {
      await selectTeam(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The team could not be selected.");
    }
  }

  return (
    <div className={`team-switcher${collapsed ? " collapsed" : ""}`} ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="team-switcher-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Team: ${current}. Switch team`}
        onClick={() => setOpen((prev) => !prev)}
        {...tipProps(`Team: ${current}`)}
      >
        <span className="nav-rail-icon" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="9" cy="8" r="3.2" />
            <path d="M3 20a6 6 0 0 1 12 0" />
            <path d="M16 5.2a3 3 0 0 1 0 5.6M18 14.3A6 6 0 0 1 21 20" />
          </svg>
        </span>
        {!collapsed && (
          <>
            <span className="team-switcher-text">
              <span className="team-switcher-label">Team</span>
              <span className="team-switcher-name">{current}</span>
            </span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </>
        )}
      </button>
      {error && !collapsed && (
        <div className="team-switcher-error" role="alert">
          {error}
        </div>
      )}

      {open && (
        <div className="project-switcher-menu team-switcher-menu" role="listbox" aria-label="Teams">
          {choices.map((choice) => {
            const isActive = choice.id === view.selectedId;
            return (
              <button
                key={choice.id ?? "private"}
                type="button"
                role="option"
                aria-selected={isActive}
                className="project-switcher-option"
                data-active={isActive ? "true" : "false"}
                onClick={() => void choose(choice.id)}
              >
                <span className="project-switcher-dot" aria-hidden="true" />
                <span className="project-switcher-option-name">{choice.label}</span>
              </button>
            );
          })}
          <button
            type="button"
            className="project-switcher-manage"
            onClick={() => {
              setOpen(false);
              setManaging(true);
            }}
          >
            Manage teams…
          </button>
        </div>
      )}

      {managing && <TeamsModal onClose={() => setManaging(false)} />}
    </div>
  );
}

/**
 * A resource's team, beside its name, when it is in one ("Public" for the
 * public team). Nothing when teams are not shown, or for a private one.
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

export default TeamSwitcher;
