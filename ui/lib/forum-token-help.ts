/**
 * How to make an access token for a project's Hypothesis Lab repository.
 *
 * The project dialog offers this under "Don't have a token?". Both forges can
 * pre-fill their token form from the URL, so the link does most of the work and
 * the steps cover only what it cannot: GitHub will not pre-select a repository,
 * and neither forge will press the button.
 */

export interface ForumTokenHelp {
  forge: "GitHub" | "GitLab";
  /** The forge's new-token form, filled in for this repository. */
  url: string;
  /** What to do on that page. */
  steps: string[];
}

/** Days until the token expires. GitHub's longest preset short of "never". */
export const TOKEN_EXPIRY_DAYS = 90;

/**
 * The token help for a repository URL, or null when VISTA cannot tell which forge
 * it is (or the URL is not `https://`, where a token does not apply).
 */
export function forumTokenHelp(repoUrl: string): ForumTokenHelp | null {
  let parsed: URL;
  try {
    parsed = new URL(repoUrl.trim());
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  const parts = parsed.pathname.replace(/\.git$/, "").split("/").filter(Boolean);
  if (parts.length < 2) return null;
  const owner = parts[0];
  const repo = parts[parts.length - 1];
  const name = `VISTA Hypothesis Lab (${repo})`;

  if (parsed.hostname === "github.com") {
    const params = new URLSearchParams({
      name,
      description: `Lets VISTA publish Hypothesis Lab debates to ${owner}/${repo}`,
      target_name: owner,
      expires_in: String(TOKEN_EXPIRY_DAYS),
      contents: "write",
    });
    return {
      forge: "GitHub",
      url: `https://github.com/settings/personal-access-tokens/new?${params}`,
      steps: [
        `Under Repository access, choose Only select repositories, then ${repo}.`,
        "Check that Contents shows Read and write.",
        "Click Generate token, copy it, and paste it above.",
      ],
    };
  }

  if (parsed.hostname.includes("gitlab")) {
    const params = new URLSearchParams({
      name,
      scopes: "read_repository,write_repository",
    });
    return {
      forge: "GitLab",
      url: `${parsed.origin}/-/user_settings/personal_access_tokens?${params}`,
      steps: [
        "Check that read_repository and write_repository are ticked.",
        "Set an expiry date if you want one.",
        "Click Create token, copy it, and paste it above.",
      ],
    };
  }

  return null;
}
