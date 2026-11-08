"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  addMember,
  createTeam,
  leaveTeam,
  listMembers,
  searchUsers,
  selectTeam,
  teamLabel,
  useTeams,
  type CollaborationUser,
  type TeamMember,
} from "@/lib/collaboration";

/** How long typing pauses before the people search is sent, ms. */
export const SEARCH_DELAY_MS = 200;

/** A person as a list shows them: their name, else their email, else their id. */
function displayName(user: CollaborationUser): string {
  return user.name || user.email || user.sub;
}

/**
 * Managing collaboration teams: the selected team's members, adding someone
 * to it, leaving it, and making a new team. A new team is selected once made,
 * since making one is nearly always the first step of working in it.
 *
 * Private is not a team, and the public team has no member list: everyone
 * who signs in is in it, and nobody can leave it.
 */
export function TeamsModal({ onClose }: { onClose: () => void }) {
  const view = useTeams();
  const team = view.selected;
  const teamId = team && !team.public ? team.id : null;
  // Kept with the team they are of, so switching teams never shows the last
  // team's members while the new list loads.
  const [loaded, setLoaded] = useState<
    { teamId: string; members: TeamMember[] | null; error: string | null } | null
  >(null);
  const current = loaded && loaded.teamId === teamId ? loaded : null;
  const members = current?.members ?? null;
  const membersError = current?.error ?? null;
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");

  const loadMembers = useCallback(async (id: string) => {
    try {
      setLoaded({ teamId: id, members: await listMembers(id), error: null });
    } catch (e) {
      setLoaded({
        teamId: id,
        members: null,
        error: e instanceof Error ? e.message : "The members could not be listed.",
      });
    }
  }, []);

  useEffect(() => {
    if (teamId) void loadMembers(teamId);
  }, [teamId, loadMembers]);

  async function create() {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const made = await createTeam(name);
      await selectTeam(made.id);
      setNewName("");
      setNotice(`Made the team ${made.name}, and selected it. New projects, skills and knowledge bases join it.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The team could not be made.");
    } finally {
      setBusy(false);
    }
  }

  async function add(user: CollaborationUser) {
    if (!teamId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await addMember(teamId, user.sub);
      setNotice(`Added ${displayName(user)} to ${team?.name}.`);
      await loadMembers(teamId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "They could not be added.");
    } finally {
      setBusy(false);
    }
  }

  async function leave() {
    if (!teamId || !team) return;
    const ok = window.confirm(
      `Leave ${team.name}? Its projects, skills and knowledge bases stay on this computer, but you can no longer publish or update them, and you see none of its items on the server. Only a member can add you back.`,
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await leaveTeam(teamId);
      setNotice(`You left ${team.name}. Private is selected.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "You could not leave the team.");
    } finally {
      setBusy(false);
    }
  }

  const memberSubs = new Set((members ?? []).map((m) => m.sub));

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div
        className="modal teams-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Teams"
      >
        <div className="panel-header">
          <div className="panel-title">Teams</div>
          <button type="button" className="button ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </div>
        <div className="modal-body teams-modal-body">
          {error && (
            <div className="error" role="alert" style={{ fontSize: 13 }}>
              {error}
            </div>
          )}
          {notice && (
            <div className="hub-count" role="status">
              {notice}
            </div>
          )}

          <section className="teams-modal-section">
            <h3 className="teams-modal-heading">{teamLabel(team)}</h3>
            {!team ? (
              <p className="teams-modal-note">
                Private is this computer only: what is made while it is selected,
                and VISTA&rsquo;s own projects, skills and knowledge bases, are
                not on the server, and cannot be published. Copy one to a team to
                share it.
              </p>
            ) : !teamId ? (
              <p className="teams-modal-note">
                Everyone who signs in to the collaboration server is in the public
                team, so what is in it, anyone there can see and import. Select or
                make a team to work with only its members.
              </p>
            ) : (
              <>
                <p className="teams-modal-note">
                  Only its members see its projects, skills and knowledge bases on
                  the server. Any member can add anyone who has signed in there.
                </p>
                {membersError ? (
                  <div className="error" style={{ fontSize: 13 }}>
                    {membersError}
                  </div>
                ) : members === null ? (
                  <div className="hub-count">Loading members…</div>
                ) : (
                  <ul className="teams-member-list" aria-label="Members">
                    {members.map((m) => (
                      <li key={m.sub} className="teams-member">
                        <span className="teams-member-name">
                          {displayName(m)}
                          {m.sub === view.user?.sub ? " (you)" : ""}
                        </span>
                        {m.email && m.name && <span className="teams-member-email">{m.email}</span>}
                      </li>
                    ))}
                  </ul>
                )}
                <MemberPicker exclude={memberSubs} disabled={busy} onPick={(u) => void add(u)} />
                <div>
                  <button
                    type="button"
                    className="button ghost button-sm"
                    disabled={busy}
                    onClick={() => void leave()}
                  >
                    Leave {team?.name}
                  </button>
                </div>
              </>
            )}
          </section>

          <section className="teams-modal-section">
            <h3 className="teams-modal-heading">New team</h3>
            <form
              className="teams-create"
              onSubmit={(e) => {
                e.preventDefault();
                void create();
              }}
            >
              <input
                className="input"
                aria-label="New team name"
                placeholder="Team name"
                maxLength={100}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
              <button type="submit" className="button" disabled={busy || !newName.trim()}>
                Create
              </button>
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}

/**
 * A filterable list of everyone who has signed in to the server, to add one to
 * the team. Each keystroke (after a short pause) asks the backend again, which
 * matches the text against names and emails; only the newest answer is shown.
 */
export function MemberPicker({
  exclude,
  disabled = false,
  onPick,
}: {
  /** Who is already a member, left out of the list. */
  exclude: ReadonlySet<string>;
  disabled?: boolean;
  onPick: (user: CollaborationUser) => void;
}) {
  const listId = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<CollaborationUser[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      searchUsers(query, controller.signal)
        .then((users) => {
          setResults(users);
          setError(null);
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return;
          setResults([]);
          setError(e instanceof Error ? e.message : "The search failed.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, SEARCH_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, open]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const shown = results.filter((u) => !exclude.has(u.sub));

  return (
    <div className="member-picker" ref={rootRef}>
      <label className="project-modal-label">
        Add a member
        <input
          className="input"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          placeholder="Search by name or email"
          value={query}
          disabled={disabled}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
          }}
        />
      </label>
      {open && (
        <div className="project-switcher-menu member-picker-menu" role="listbox" id={listId} aria-label="People">
          {error ? (
            <div className="project-switcher-empty">{error}</div>
          ) : shown.length === 0 ? (
            <div className="project-switcher-empty">
              {searching
                ? "Searching…"
                : "Nobody matches. Only someone who has signed in to the server can be added."}
            </div>
          ) : (
            shown.map((user) => (
              <button
                key={user.sub}
                type="button"
                role="option"
                aria-selected={false}
                className="project-switcher-option member-picker-option"
                onClick={() => {
                  setOpen(false);
                  setQuery("");
                  onPick(user);
                }}
              >
                <span className="member-picker-name">{user.name || user.sub}</span>
                {user.email && <span className="member-picker-email">{user.email}</span>}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default TeamsModal;
