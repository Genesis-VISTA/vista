/**
 * The installation's settings + hook. Mirrors the backend's
 * `LocalSettingsPublic` / `LocalSettingsUpdate` schemas: VISTA has one
 * researcher per installation, so there is no user, only these settings.
 *
 * `useSettings` keeps a module-level copy for the nav rail (which reads the
 * hidden clusters); the settings modal fetches its own on open with
 * `fetchSettings`.
 */

import { useEffect, useReducer } from "react";

/** The first-boot tour's progress, as the backend's `OnboardingTourState` checks it on writes. */
export type OnboardingTourState = {
  status: "in_progress" | "finished" | "exited";
  step: string;
};

/** Backend `LocalSettingsPublic` — returned by `GET /settings` and `PUT /settings`. */
export type LocalSettings = {
  /** This installation's identity, made once when its settings were created. */
  install_id: string;
  /** What the collaboration server will show for this installation. */
  display_name: string | null;
  /**
   * The collaboration server to publish to and import from, as the researcher
   * set it; null when they have not (the installation's configuration may still
   * name one: see `lib/collaboration.ts`).
   */
  collaboration_server_url: string | null;
  /** Where campaign notifications go. */
  email: string | null;
  /** Clusters left out of the NavRail's HPC section. `[]` shows them all. */
  hpc_hidden_clusters?: string[];
  /** The first-boot tour's progress; null when it never started. */
  onboarding_tour?: OnboardingTourState | null;
  /** Whether this installation's configuration allows the tour. Read-only. */
  onboarding_tour_enabled?: boolean;
  inference_model: string | null;
  /** Custom's endpoint; the other providers' come from the backend's presets. */
  inference_base_url: string | null;
  /** The key for an endpoint set in the installation's configuration; no provider reads it. */
  inference_api_key: string | null;
  /** `mag`, `olcf` or `custom`; null until chosen. See `lib/agent-settings.ts`. */
  inference_provider?: string | null;
  inference_mag_api_key?: string | null;
  /** OLCF Inference's S3M token, kept apart from the Odo and Frontier ones. */
  inference_olcf_api_key?: string | null;
  inference_custom_api_key?: string | null;
  /** Optional, user-authored scientific context used to personalize the main assistant. */
  research_role?: string | null;
  research_institution?: string | null;
  research_interests?: string[];
  preferred_units?: "si" | "source" | null;
  technical_depth?: "expert" | "balanced" | "introductory" | null;
  evidence_preference?: "cite_when_available" | "always_cite" | "concise" | null;
  personalize_responses?: boolean;
  nersc_account: string | null;
  nersc_remote_dir: string | null;
  /**
   * Where VISTA puts job sources and outputs on each cluster. No default: where a project keeps its files is theirs to say.
   */
  odo_remote_dir: string | null;
  frontier_remote_dir: string | null;
  lux_remote_dir: string | null;
  /** The OLCF project Lux jobs are charged to; Lux has no token to take one from. */
  lux_account: string | null;
  /**
   * One S3M token per OLCF cluster: a token is scoped to a single project,
   * so one field could only ever authorize one of Odo and Frontier.
   */
  odo_s3m_token: string | null;
  frontier_s3m_token: string | null;
  nersc_iri_token: string | null;
  /**
   * File-transfer credentials, read only to tell whether a cluster is
   * connected. They are deliberately absent from `LocalSettingsUpdate`: a Globus
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
  /** Whether a GitHub token is saved. The token itself is never sent back. */
  has_github_token?: boolean;
  /** Whether a gitlab.com token is saved. The token itself is never sent back. */
  has_gitlab_token?: boolean;
  /** Whether a collaboration server token is saved. Never sent back either. */
  has_collaboration_token?: boolean;
};

/**
 * Backend `LocalSettingsUpdate` — accepted by `PUT /settings`. Every field is
 * optional; only the keys actually present are written, and each accepts
 * `null` to clear it.
 */
export type LocalSettingsUpdate = {
  display_name?: string | null;
  /** An `http(s)://` address; `null` or `""` clears it. */
  collaboration_server_url?: string | null;
  email?: string | null;
  inference_model?: string | null;
  inference_base_url?: string | null;
  inference_api_key?: string | null;
  /** Changing it clears `inference_model` on the backend, unless this update sets one. */
  inference_provider?: string | null;
  inference_mag_api_key?: string | null;
  inference_olcf_api_key?: string | null;
  inference_custom_api_key?: string | null;
  research_role?: string | null;
  research_institution?: string | null;
  research_interests?: string[] | null;
  preferred_units?: "si" | "source" | null;
  technical_depth?: "expert" | "balanced" | "introductory" | null;
  evidence_preference?: "cite_when_available" | "always_cite" | "concise" | null;
  personalize_responses?: boolean;
  nersc_account?: string | null;
  nersc_remote_dir?: string | null;
  odo_remote_dir?: string | null;
  frontier_remote_dir?: string | null;
  lux_remote_dir?: string | null;
  lux_account?: string | null;
  odo_s3m_token?: string | null;
  frontier_s3m_token?: string | null;
  nersc_iri_token?: string | null;
  /** Clusters to leave out of the NavRail. `[]` shows them all. */
  hpc_hidden_clusters?: string[] | null;
  /** The tour's progress; null forgets it, so the tour starts again. */
  onboarding_tour?: OnboardingTourState | null;
  /** Write-only, unlike the fields above: `null` keeps the saved token, `""` removes it. */
  github_token?: string | null;
  /** Write-only, like `github_token`. */
  gitlab_token?: string | null;
  /** The token from the collaboration server's sign-in page. Write-only, like `github_token`. */
  collaboration_token?: string | null;
};

let settingsCache: LocalSettings | null = null;
let settingsLoaded = false;
let settingsError: string | null = null;
let inFlight: Promise<LocalSettings | null> | null = null;
const settingsListeners = new Set<() => void>();

function notifySettings(): void {
  settingsListeners.forEach((cb) => cb());
}

export async function extractError(res: Response): Promise<string> {
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
 * Refresh the cached settings. Concurrent calls share a single in-flight
 * request unless `force` is set — callers that just wrote the settings need a
 * fresh GET, not whatever request happened to be in flight when they were called.
 */
export function refreshSettings(
  { force = false }: { force?: boolean } = {},
): Promise<LocalSettings | null> {
  if (inFlight && !force) return inFlight;
  let settled: Promise<LocalSettings | null>;
  const fetcher = async (): Promise<LocalSettings | null> => {
    try {
      const res = await fetch("/api/settings", {
        headers: { accept: "application/json" },
        cache: "no-store",
      });
      if (!res.ok) {
        settingsCache = null;
        settingsError = await extractError(res);
      } else {
        settingsCache = (await res.json()) as LocalSettings;
        settingsError = null;
      }
    } catch (e) {
      settingsCache = null;
      settingsError = e instanceof Error ? e.message : "Failed to load settings";
    } finally {
      settingsLoaded = true;
      // Only clear `inFlight` if it still points at *our* request — a
      // newer `force` refresh started while we were running shouldn't be
      // cleared by us.
      if (inFlight === settled) inFlight = null;
      notifySettings();
    }
    return settingsCache;
  };
  settled = fetcher();
  inFlight = settled;
  return settled;
}

export interface UseSettingsResult {
  settings: LocalSettings | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<LocalSettings | null>;
}

export function useSettings(): UseSettingsResult {
  const [, forceRender] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    settingsListeners.add(forceRender);
    if (!settingsLoaded && !inFlight) void refreshSettings();
    return () => {
      settingsListeners.delete(forceRender);
    };
  }, []);
  return {
    settings: settingsCache,
    loading: !settingsLoaded,
    error: settingsError,
    refresh: refreshSettings,
  };
}

/**
 * Fetch the settings (with decrypted tokens) without the cache. The settings
 * modal calls this on open and keeps the response in its local state.
 */
export async function fetchSettings(): Promise<LocalSettings> {
  const res = await fetch("/api/settings", {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as LocalSettings;
}

/**
 * PUT `/settings` and return the updated settings, so the modal can refresh
 * its draft from the response without a follow-up GET. The cache is updated
 * too: the rail reads the hidden clusters from it, and leaving it stale would
 * show a cluster the researcher had just hidden until the next page load.
 */
export async function updateSettings(
  updates: LocalSettingsUpdate,
): Promise<LocalSettings> {
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await extractError(res));
  const updated = (await res.json()) as LocalSettings;
  settingsCache = updated;
  settingsLoaded = true;
  settingsError = null;
  notifySettings();
  return updated;
}

/**
 * The OLCF enclaves VISTA transfers files to. Each is authorized on its own:
 * they sit behind different identity providers, and a researcher may have an
 * account on one and not the other.
 */
export type GlobusCluster = "odo" | "frontier";

/** Which settings field holds a cluster's credential. */
export const GLOBUS_TOKEN_FIELD: Record<GlobusCluster, keyof LocalSettings> = {
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
  const res = await fetch(`/api/settings/globus/${cluster}/login`, {
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
  const res = await fetch(`/api/settings/globus/${cluster}/code`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as GlobusConnected;
}
