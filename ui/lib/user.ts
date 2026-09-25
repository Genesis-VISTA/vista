/**
 * Current-user model + hook. Mirrors the backend's `UserPublic` /
 * `UserPublicWithConfig` / `UserSelfUpdate` schemas. Auth (SSO) happens
 * upstream — the backend's dev placeholder picks a single seeded user, and
 * this layer just surfaces "who is logged in right now" to the UI.
 *
 * Two views:
 *   - `UserPublic` — id/email/is_admin only. Cached module-level and used
 *     by the nav rail; fetched on first mount, never carries secrets.
 *   - `UserPublicWithConfig` — adds the per-user config (inference model /
 *     endpoint / key, NERSC account, decrypted S3M / IRI tokens). Fetched on
 *     demand by the settings modal so we don't decrypt or surface secrets on
 *     every page load.
 */

import { useEffect, useReducer } from "react";

/** Backend `UserPublic` — returned by `GET /users/me`. */
export type UserPublic = {
  id: string;
  email: string;
  is_admin: boolean;
  /**
   * Clusters left out of the NavRail's HPC section. Not a secret, so it is on
   * the light view the rail reads. Optional because an older backend omits it.
   */
  hpc_hidden_clusters?: string[];
};

/** Backend `UserPublicWithConfig` — returned by `GET /users/me?config=true` and `PUT /users/me`. */
export type UserPublicWithConfig = UserPublic & {
  inference_model: string | null;
  inference_base_url: string | null;
  inference_api_key: string | null;
  nersc_account: string | null;
  nersc_remote_dir: string | null;
  /**
   * One S3M token per OLCF cluster: a token is scoped to a single project,
   * so one field could only ever authorize one of Odo and Frontier.
   */
  odo_s3m_token: string | null;
  frontier_s3m_token: string | null;
  nersc_iri_token: string | null;
  /**
   * File-transfer credentials, read only to tell whether a cluster is
   * connected. They are deliberately absent from `UserSelfUpdate`: a Globus
   * credential arrives from an authorization, not from something typed into
   * the form, so there is nothing here for the save diff to carry.
   */
  globus_token: string | null;
  odo_globus_token: string | null;
  frontier_globus_token: string | null;
  /**
   * The second half of each credential: the OLCF collection's own token, which
   * is what reads and writes file contents over the Globus HTTPS interface.
   * A cluster counts as connected only with both — a connection made before
   * VISTA moved to that interface has the Transfer token alone and has to be
   * made again.
   */
  globus_https_token: string | null;
  odo_globus_https_token: string | null;
  frontier_globus_https_token: string | null;
};

/**
 * Backend `UserSelfUpdate` — accepted by `PUT /users/me`. Every field is
 * optional; only the keys actually present are written, and each accepts
 * `null` to clear it.
 */
export type UserSelfUpdate = {
  inference_model?: string | null;
  inference_base_url?: string | null;
  inference_api_key?: string | null;
  nersc_account?: string | null;
  nersc_remote_dir?: string | null;
  odo_s3m_token?: string | null;
  frontier_s3m_token?: string | null;
  nersc_iri_token?: string | null;
};

let userCache: UserPublic | null = null;
let userLoaded = false;
let userError: string | null = null;
let inFlight: Promise<UserPublic | null> | null = null;
const userListeners = new Set<() => void>();

function notifyUser(): void {
  userListeners.forEach((cb) => cb());
}

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown; error?: unknown }).detail ??
      (data as { error?: unknown }).error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) =>
          typeof d === "object" && d && "msg" in d
            ? String((d as { msg: unknown }).msg)
            : JSON.stringify(d)
        )
        .join("; ");
    }
    if (detail != null) return JSON.stringify(detail);
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

/**
 * Refresh the cached light user. Concurrent calls share a single in-flight
 * request unless `force` is set — callers that just wrote to the user (e.g.
 * after `updateCurrentUser`) need a fresh GET, not whatever request happened
 * to be in flight when they were called.
 */
export function refreshCurrentUser(
  { force = false }: { force?: boolean } = {},
): Promise<UserPublic | null> {
  if (inFlight && !force) return inFlight;
  let settled: Promise<UserPublic | null>;
  const fetcher = async (): Promise<UserPublic | null> => {
    try {
      const res = await fetch("/api/users/me", {
        headers: { accept: "application/json" },
        cache: "no-store",
      });
      if (!res.ok) {
        userCache = null;
        userError = await extractError(res);
      } else {
        userCache = (await res.json()) as UserPublic;
        userError = null;
      }
    } catch (e) {
      userCache = null;
      userError = e instanceof Error ? e.message : "Failed to load user";
    } finally {
      userLoaded = true;
      // Only clear `inFlight` if it still points at *our* request — a
      // newer `force` refresh started while we were running shouldn't be
      // cleared by us.
      if (inFlight === settled) inFlight = null;
      notifyUser();
    }
    return userCache;
  };
  settled = fetcher();
  inFlight = settled;
  return settled;
}

export interface UseCurrentUserResult {
  user: UserPublic | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<UserPublic | null>;
}

export function useCurrentUser(): UseCurrentUserResult {
  const [, forceRender] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    userListeners.add(forceRender);
    if (!userLoaded && !inFlight) void refreshCurrentUser();
    return () => {
      userListeners.delete(forceRender);
    };
  }, []);
  return {
    user: userCache,
    loading: !userLoaded,
    error: userError,
    refresh: refreshCurrentUser,
  };
}

/**
 * Fetch the full `UserPublicWithConfig` view (includes decrypted tokens).
 * Not cached — the settings modal calls this on open, and the response is
 * only kept in the modal's local state.
 */
export async function fetchCurrentUserWithConfig(): Promise<UserPublicWithConfig> {
  const res = await fetch("/api/users/me?config=true", {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as UserPublicWithConfig;
}

/**
 * PUT `/users/me` and return the updated user. The backend returns the new
 * `UserPublicWithConfig`, so the modal can refresh its draft from the
 * response without a follow-up GET. The light cache is also updated in case
 * `email`/`is_admin` ever change (today they can't via this endpoint).
 */
export async function updateCurrentUser(
  updates: UserSelfUpdate,
): Promise<UserPublicWithConfig> {
  const res = await fetch("/api/users/me", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await extractError(res));
  const updated = (await res.json()) as UserPublicWithConfig;
  userCache = { id: updated.id, email: updated.email, is_admin: updated.is_admin };
  userLoaded = true;
  userError = null;
  notifyUser();
  return updated;
}

/**
 * The OLCF enclaves VISTA transfers files to. Each is authorized on its own:
 * they sit behind different identity providers, and a researcher may have an
 * account on one and not the other.
 */
export type GlobusCluster = "odo" | "frontier";

/** Which field on the user holds a cluster's credential. */
export const GLOBUS_TOKEN_FIELD: Record<GlobusCluster, keyof UserPublicWithConfig> = {
  odo: "odo_globus_token",
  frontier: "frontier_globus_token",
};

/** Backend `GlobusLoginStarted`. */
export type GlobusLoginStarted = {
  authorize_url: string;
};

/** Backend `GlobusConnected`. */
export type GlobusConnected = {
  cluster: GlobusCluster;
  identity: string;
};

/**
 * Begin an authorization and get the address to send the researcher to.
 *
 * Starting again abandons any address already outstanding for this cluster,
 * so only the most recent one will produce a code the backend accepts.
 */
export async function startGlobusLogin(
  cluster: GlobusCluster,
): Promise<GlobusLoginStarted> {
  const res = await fetch(`/api/users/me/globus/${cluster}/login`, {
    method: "POST",
    headers: { accept: "application/json" },
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as GlobusLoginStarted;
}

/**
 * Hand over the code the researcher pasted. On success the credential is
 * stored server-side and the reply names the Globus account it belongs to.
 */
export async function completeGlobusLogin(
  cluster: GlobusCluster,
  code: string,
): Promise<GlobusConnected> {
  const res = await fetch(`/api/users/me/globus/${cluster}/code`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as GlobusConnected;
}

/**
 * Two-letter initials for the user avatar. Falls back to the first character
 * of the email, then to `?`. Designed to give a reasonable label for an SSO
 * email like `first.last@org` or `flast@org` without bringing in a real name
 * field (which the backend doesn't store today).
 */
export function userInitials(user: UserPublic | null): string {
  if (!user || !user.email) return "?";
  const local = user.email.split("@")[0] ?? "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  if (parts.length === 1 && parts[0].length >= 2) {
    return parts[0].slice(0, 2).toUpperCase();
  }
  return local.slice(0, 2).toUpperCase() || "?";
}

/**
 * The portion of the email shown next to the avatar — local part of the
 * address, since the org domain is usually noise in a UI footer.
 */
export function userDisplayName(user: UserPublic | null): string {
  if (!user || !user.email) return "Unknown";
  return user.email.split("@")[0] ?? user.email;
}
