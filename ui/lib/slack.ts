import { extractError } from "./user";

/**
 * The desktop's Slack settings, as `GET /slack/config` reports them. The
 * tokens are write-only: only whether each is set ever comes back.
 */
export type SlackConfig = {
  enabled: boolean;
  handle_here: boolean;
  has_bot_token: boolean;
  has_app_token: boolean;
  secret_store_available: boolean;
  secret_store_problem: string | null;
  allowed_team_ids: string[];
  allowed_channel_ids: string[];
  max_nodes: number;
  max_walltime_minutes: number;
  max_jobs_per_day: number;
  locked: boolean;
  locked_at: string | null;
  locked_by: string | null;
  prevent_sleep: boolean;
  notify_desktop_jobs: boolean;
  /** This machine's connection: off, connected, standby (another machine has Slack) or error. */
  connection: "off" | "connected" | "standby" | "error";
  /** Why it is off, which machine has Slack, or what went wrong. */
  connection_detail: string | null;
  lease_status: Record<string, unknown> | null;
  /** VISTA starts at login with no window, so Slack reaches it while you are away. */
  background_mode: boolean;
  /** Why background mode can't be used here (a development checkout, no systemd), or null. */
  background_problem: string | null;
};

export type SlackConfigUpdate = Partial<
  Omit<
    SlackConfig,
    | "has_bot_token"
    | "has_app_token"
    | "secret_store_available"
    | "secret_store_problem"
    | "locked_at"
    | "locked_by"
    | "connection"
    | "connection_detail"
    | "lease_status"
    | "background_problem"
  >
> & { bot_token?: string; app_token?: string };

/** A Slack account linked to the signed-in researcher. */
export type SlackLink = {
  id: string;
  team_id: string;
  slack_user_id: string;
  /** The project new Slack conversations start in; null is the first project. */
  project_id: string | null;
  /** Job notices in the job's thread rather than a DM. */
  notify_in_thread: boolean;
  created_at: string;
};

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api/slack/${path}`, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const fetchSlackConfig = () => call<SlackConfig>("config");
export const updateSlackConfig = (updates: SlackConfigUpdate) =>
  call<SlackConfig>("config", "PUT", updates);
export const takeOverSlack = () => call<SlackConfig>("take-over", "POST");
export const fetchSlackLinks = () => call<SlackLink[]>("links");
export const linkSlack = (code: string) => call<SlackLink>("links", "POST", { code });
export const updateSlackLink = (
  id: string,
  updates: Partial<Pick<SlackLink, "project_id" | "notify_in_thread">>,
) => call<SlackLink>(`links/${encodeURIComponent(id)}`, "PUT", updates);
export const unlinkSlack = (id: string) =>
  call<void>(`links/${encodeURIComponent(id)}`, "DELETE");

/** The nav entry's note: what a glance at the settings list should say. */
export function slackNote(config: SlackConfig | null): string | null {
  if (!config) return null;
  if (!config.enabled) return "Off";
  if (config.locked) return "Locked";
  switch (config.connection) {
    case "connected":
      return "Connected";
    case "standby":
      return "Other machine";
    case "error":
      return "Problem";
    default:
      return "Not here";
  }
}
