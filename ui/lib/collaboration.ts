"use client";

import { useMemo, useSyncExternalStore } from "react";
import { extractError } from "@/lib/settings";

/**
 * Whether publishing and importing can work right now, from
 * `GET /api/collaboration/status`, and the one hook every Publish and Import
 * control reads.
 *
 * One store for the page, the same pattern as `lib/agent-settings.ts`: the
 * Settings section and every control show the same answer, and saving the
 * server address calls `refreshCollaborationStatus()` so they all follow. A
 * control is disabled whenever `available` is false, and shows `reason`.
 *
 * It also carries the researcher's teams. Here a project, skill or knowledge
 * base is either private (`team_id` null: on this computer only, as everything
 * made here is, unless made for a team's project, and VISTA's own seeds) or in
 * one team on the server: the public team, which everyone is in, or one they
 * made or were added to, whose items only its members see. Every page lists
 * all of them, each with its team. A private one joins a team when it is first
 * published, into the team the researcher picks (`publishToTeam`), and stays
 * there: Publish sends its new versions to that team.
 */

/** The public team's id, the same on every server. It is a team like any other, listed in `teams`. */
export const PUBLIC_TEAM_ID = "00000000-0000-4000-8000-000000000000";

/** Someone who has signed in to the collaboration server (backend `RemoteUser`). */
export type CollaborationUser = {
  sub: string;
  email: string | null;
  name: string | null;
};

/** A team on the collaboration server (backend `RemoteTeam`). */
export type CollaborationTeam = {
  id: string;
  name: string;
  /** The public team: everyone is in it, so it has no member list. */
  public: boolean;
  created_by: string;
  created_by_name: string | null;
  created_at: string;
};

/** A team's member (backend `RemoteMember`). */
export type TeamMember = CollaborationUser & {
  added_by: string;
  added_at: string;
};

/** Backend `CollaborationStatus`. */
export type CollaborationStatus = {
  /** A server address is set, in Settings or the installation's configuration. */
  configured: boolean;
  /** It answered, and speaks the API this VISTA does. */
  reachable: boolean;
  /** Why publishing and importing are off; null when they are on. */
  reason: string | null;
  /** The address in effect. */
  url: string | null;
  /** The researcher's own address, or the installation's. */
  source: "settings" | "config" | null;
  server_version: string | null;
  /** The server accepts this installation's token (or, in development, its install id). */
  signed_in: boolean;
  /** The server, or a gateway in front of it, refused the token or its absence. */
  sign_in_required: boolean;
  /** The server's sign-in page, where the researcher gets a token to paste. */
  sign_in_url: string | null;
  /** Who the server takes this installation to be; null unless signed in. */
  user: CollaborationUser | null;
  /** The researcher's teams, the public team first; empty unless signed in. */
  teams: CollaborationTeam[];
};

type Snapshot = {
  /** The last answer; null until the first. Kept when a later request fails. */
  status: CollaborationStatus | null;
  /** Why the last request failed, if it did; beside the last answer, not instead of it. */
  error: string | null;
  checking: boolean;
  /** Client clock, ms, of the last answer. */
  checkedAt: number | null;
};

/** A new subscriber re-asks once the answer is older than this. */
export const STALE_MS = 30_000;

const initial = (): Snapshot => ({ status: null, error: null, checking: false, checkedAt: null });

let snapshot: Snapshot = initial();
let request = 0;
const listeners = new Set<() => void>();

function set(update: Partial<Snapshot>): void {
  snapshot = { ...snapshot, ...update };
  listeners.forEach((cb) => cb());
}

/**
 * Ask again. Every subscriber follows. `forget` drops the last answer first:
 * after the address changes, an answer about the old address says nothing
 * about the new one, so controls go back to "Checking…" until it arrives.
 */
export async function refreshCollaborationStatus(
  { forget = false }: { forget?: boolean } = {},
): Promise<void> {
  const id = ++request;
  set(forget ? { checking: true, status: null, error: null } : { checking: true });
  try {
    const res = await fetch("/api/collaboration/status", {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`request failed (${res.status})`);
    const status = (await res.json()) as CollaborationStatus;
    // A slower, older request must not overwrite a newer answer.
    if (id !== request) return;
    set({ status, error: null, checking: false, checkedAt: Date.now() });
  } catch (e) {
    if (id !== request) return;
    // One failed request (a backend restarting) does not throw away the last
    // answer about the same address: the controls keep it, and Settings shows
    // the error beside it.
    set({
      error: e instanceof Error ? e.message : "request failed",
      checking: false,
      checkedAt: Date.now(),
    });
  }
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const stale = snapshot.checkedAt === null || Date.now() - snapshot.checkedAt >= STALE_MS;
  if (!snapshot.checking && stale) void refreshCollaborationStatus();
  return () => {
    listeners.delete(cb);
  };
}

const getSnapshot = () => snapshot;
const SERVER_SNAPSHOT: Snapshot = initial();
const getServerSnapshot = () => SERVER_SNAPSHOT;

/** For tests: forget everything, as a fresh page load would. */
export function resetCollaborationForTests(): void {
  request++;
  snapshot = initial();
}

/**
 * Whether Publish and Import are enabled, and if not, the sentence to show.
 * Pure, for testing. Off until the first answer: a control enabled while
 * checking would fail for a reason the researcher was not shown. After that,
 * the last answer stands even if a later check could not be made.
 */
export function availability(s: Pick<Snapshot, "status" | "error">): {
  available: boolean;
  reason: string | null;
} {
  if (s.status?.reachable && s.status.signed_in) return { available: true, reason: null };
  if (s.status) {
    return { available: false, reason: s.status.reason ?? "The collaboration server is unavailable." };
  }
  if (s.error) return { available: false, reason: `Couldn't check the collaboration server: ${s.error}.` };
  return { available: false, reason: "Checking the collaboration server…" };
}

export type CollaborationView = {
  status: CollaborationStatus | null;
  checking: boolean;
  /** Publish and Import can be used. */
  available: boolean;
  /** Why they cannot, for the disabled control; null when they can. */
  reason: string | null;
  /** Why the last check could not be made, beside the last answer it kept. */
  error: string | null;
  refresh: () => Promise<void>;
};

export function useCollaboration(): CollaborationView {
  const s = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return {
    status: s.status,
    checking: s.checking,
    error: s.error,
    ...availability(s),
    refresh: () => refreshCollaborationStatus(),
  };
}

/* ------------------------------------------------------------------ */
/*  Teams                                                              */
/* ------------------------------------------------------------------ */

/**
 * A resource's team as this installation treats it: its team when that is one
 * of `knownTeamIds`, the researcher's current teams (the public team among
 * them), else null, Private. A team not known is one they have left, or one on
 * a server they no longer use: what is in it here is on this computer only in
 * all but name, and is published into a team as a private one is. The team
 * itself is unchanged, and still decides what a project may use
 * (`usableInProject`).
 */
export function effectiveTeam(
  resourceTeamId: string | null | undefined,
  knownTeamIds: ReadonlySet<string>,
): string | null {
  return resourceTeamId && knownTeamIds.has(resourceTeamId) ? resourceTeamId : null;
}

/**
 * Whether a project of `projectTeamId` can use a skill or knowledge base of
 * `resourceTeamId`, the rule the backend enforces (`services/teams.usable_in`):
 * a team's project, only one of exactly its own team; a private project, seen
 * by no one else, any. Unlike publishing, a team the researcher is not in is
 * not treated as private here: the backend would refuse it.
 */
export function usableInProject(
  resourceTeamId: string | null | undefined,
  projectTeamId: string | null | undefined,
): boolean {
  return projectTeamId == null || (resourceTeamId ?? null) === projectTeamId;
}

export type TeamsView = {
  /**
   * Teams can be shown: a server answered and signed this installation in.
   * When false everything here is treated as private.
   */
  shown: boolean;
  /** The researcher's teams, the public team first. Private is not one of them. */
  teams: CollaborationTeam[];
  user: CollaborationUser | null;
  /**
   * A resource's team as this installation treats it (`effectiveTeam`): null,
   * private, when it is not one of `teams`. Until the teams are known (no
   * server answered, or not signed in) it is the team the resource records,
   * as the backend falls back to, so Publish stays offered with its reason.
   */
  teamOf: (resourceTeamId: string | null | undefined) => string | null;
  /** A known team's name ("Public" for the public team); null for Private or one not known. */
  teamName: (resourceTeamId: string | null | undefined) => string | null;
};

/** How a team is named here: the public team is "Public", whatever the server calls it. */
export function teamLabel(team: CollaborationTeam | null): string {
  if (!team) return "Private";
  return team.public ? "Public" : team.name;
}

/** The teams part of a status. Pure, for testing. */
export function teamsView(s: Pick<Snapshot, "status" | "error">): TeamsView {
  const status = s.status;
  const teams = status?.teams ?? [];
  const shown = availability(s).available && teams.length > 0;
  const known = new Set(teams.map((t) => t.id));
  const names = new Map(teams.map((t) => [t.id, teamLabel(t)]));
  return {
    shown,
    teams,
    user: status?.user ?? null,
    teamOf: (teamId) => (shown ? effectiveTeam(teamId, known) : (teamId ?? null)),
    teamName: (teamId) => (teamId ? (names.get(teamId) ?? null) : null),
  };
}

/** The researcher's teams. */
export function useTeams(): TeamsView {
  const s = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return useMemo(() => teamsView(s), [s]);
}

/**
 * Where publishing a resource of `teamId` puts it, for its confirmation: its
 * own team, or for a private one the team picked for its first publish.
 */
export function publishAudience(teamId: string | null | undefined, view: TeamsView): string {
  if (!teamId) return "It is private, on this computer only: choose a team to publish it to.";
  if (teamId === PUBLIC_TEAM_ID) {
    return "It is published to the public team, visible to everyone who signs in to that server.";
  }
  const name = view.teamName(teamId);
  if (name) return `It is published to the team ${name}: only its members can see and import it.`;
  return "It belongs to a team you are not in on that server, which will refuse it.";
}

/** What can be published into a team (backend `team_publish.Kind`). */
export type PublishKind = "project" | "skill" | "knowledge-base";

/** One thing a first publish into a team published (backend `PublishedResource`). */
export type PublishedResource = {
  kind: PublishKind;
  /** Its name (a knowledge base's slug) here before the publish. */
  name: string;
  /**
   * The team's item's name here: `name`, the resource having joined the team,
   * or a new one, the team's item imported beside an original that stays as
   * it was: one VISTA bundles, or one a project of another team here uses.
   */
  local_name: string;
  /** The resource joined the team, or stayed as it was with the team's item imported beside it. */
  how: "published" | "imported";
  /** `imported` only: the projects of another team it stayed for; empty for one VISTA bundles. */
  kept_for?: string[];
};

/** Backend `TeamPublishResult`: what was published, a project last. */
export type TeamPublishResult = {
  team_id: string;
  published: PublishedResource[];
  /** A project's: why its hosted forum was not linked to it; the publish stands. */
  forum_error?: string | null;
};

/** A refused team request, with the backend's sentence. */
export class TeamRequestError extends Error {}

async function teamRequest<T>(url: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: { accept: "application/json", ...(init.body ? { "content-type": "application/json" } : {}) },
      cache: "no-store",
    });
  } catch (e) {
    throw new TeamRequestError(e instanceof Error ? e.message : "Request failed.");
  }
  if (!res.ok) throw new TeamRequestError(await extractError(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** Make a team, with the researcher as its first member. 409 when the name is taken, in any case. */
export async function createTeam(name: string): Promise<CollaborationTeam> {
  const team = await teamRequest<CollaborationTeam>("/api/collaboration/teams", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  await refreshCollaborationStatus();
  return team;
}

/** A team's members. Refused for the public team, which has no list. */
export function listMembers(teamId: string): Promise<TeamMember[]> {
  return teamRequest<TeamMember[]>(`/api/collaboration/teams/${encodeURIComponent(teamId)}/members`);
}

/** Add someone who has signed in to the server (`searchUsers`) to a team the researcher is in. */
export function addMember(teamId: string, sub: string): Promise<TeamMember> {
  return teamRequest<TeamMember>(`/api/collaboration/teams/${encodeURIComponent(teamId)}/members`, {
    method: "POST",
    body: JSON.stringify({ sub }),
  });
}

/**
 * Leave a team; refused for its last member. Its resources here stay, and can
 * be published into another team, as private ones are.
 */
export async function leaveTeam(teamId: string): Promise<void> {
  await teamRequest<void>(`/api/collaboration/teams/${encodeURIComponent(teamId)}/members/me`, {
    method: "DELETE",
  });
  await refreshCollaborationStatus();
}

/** Everyone who has signed in to the server whose name or email contains `q`, at most 20. */
export function searchUsers(q: string, signal?: AbortSignal): Promise<CollaborationUser[]> {
  const query = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  return teamRequest<CollaborationUser[]>(`/api/collaboration/users${query}`, { signal });
}

const PUBLISH_BASE: Record<PublishKind, string> = {
  project: "/api/projects",
  skill: "/api/skills",
  "knowledge-base": "/api/knowledge-bases",
};

/**
 * Publish a private project, skill or knowledge base (by name; a knowledge
 * base by slug) for the first time, into a team the researcher is in. A
 * project brings its skills and knowledge bases. Refused (400, code
 * `not_in_team`) for a team they are not in, and (400) for one in a team
 * already, or a project using something missing or in another team.
 */
export function publishToTeam(kind: PublishKind, name: string, teamId: string): Promise<TeamPublishResult> {
  return teamRequest<TeamPublishResult>(`${PUBLISH_BASE[kind]}/${encodeURIComponent(name)}/publish-to-team`, {
    method: "POST",
    body: JSON.stringify({ team_id: teamId }),
  });
}
