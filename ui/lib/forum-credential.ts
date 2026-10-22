import type { LocalSettings } from "./settings";

/**
 * Which credential a project's Hypothesis Lab forum publishes with.
 *
 * The project dialog says so under the forum URL. A project holds no token of
 * its own, so it is the Settings token for the forum's host, else the system
 * git's credentials.
 */

/** Which tokens the signed-in user has saved in Settings → Repositories. */
export type SettingsTokens = Pick<LocalSettings, "has_github_token" | "has_gitlab_token">;

/**
 * The credential a forum's git sends, mirroring the backend's `repo_token`: the
 * Settings token for the forum's host, else the system git's credentials. Only
 * `https://` gets a token, so an SSH or plain-HTTP remote always uses the system
 * git. Null for an empty URL, where there is no forum.
 */
export function forumCredential(
  repoUrl: string,
  tokens: SettingsTokens,
): "settings-github" | "settings-gitlab" | "system-git" | null {
  if (!repoUrl.trim()) return null;
  let parsed: URL;
  try {
    parsed = new URL(repoUrl.trim());
  } catch {
    // `git@host:org/repo.git` is not a URL, but it is an SSH remote.
    return "system-git";
  }
  if (parsed.protocol !== "https:") return "system-git";
  if (parsed.hostname === "github.com" && tokens?.has_github_token) return "settings-github";
  if (parsed.hostname === "gitlab.com" && tokens?.has_gitlab_token) return "settings-gitlab";
  return "system-git";
}
