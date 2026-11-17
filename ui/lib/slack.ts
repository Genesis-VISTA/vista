import { extractError } from "./settings";

/**
 * The desktop's Slack settings, as `GET /slack/config` reports them. The
 * tokens are write-only: only whether each is set ever comes back.
 */
export type SlackConfig = {
  enabled: boolean;
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
  notify_desktop_jobs: boolean;
  /** The connection to Slack: off, connected or error. */
  connection: "off" | "connected" | "error";
  /** Why it is off, or what went wrong. */
  connection_detail: string | null;
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
  >
> & { bot_token?: string; app_token?: string };

/** A Slack account linked to this VISTA: it acts as the researcher here. */
export type SlackLink = {
  id: string;
  team_id: string;
  slack_user_id: string;
  /** Who the researcher was on the collaboration server when it was linked, if signed in. */
  collab_name: string | null;
  /** The project new Slack conversations start in; null asks which one (unless there is only one). */
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
    case "error":
      return "Problem";
    default:
      return "Not running";
  }
}

/** In a conversation started from Slack, instead of the message box: it is answered in Slack only. */
export const CONTINUES_IN_SLACK =
  "This conversation continues in Slack. Reply in its Slack thread; from here you can stop its turn.";

/** On a Slack conversation's prompt, instead of its answer controls. */
export const ANSWER_IN_SLACK = "Answer this in the conversation's Slack thread.";
