import { describe, expect, it } from "vitest";
import { forumCredential } from "@/lib/forum-credential";

describe("forumCredential", () => {
  const both = { has_github_token: true, has_gitlab_token: true };

  it.each([
    ["https://github.com/o/forum.git", both, "settings-github"],
    ["https://gitlab.com/g/forum.git", both, "settings-gitlab"],
    ["https://github.com/o/forum.git", { has_gitlab_token: true }, "system-git"],
    ["https://code.example.org/g/forum.git", both, "system-git"],
    ["https://github.com/o/forum.git", {}, "system-git"],
    ["http://github.com/o/forum.git", both, "system-git"],
    ["git@github.com:o/forum.git", both, "system-git"],
    ["ssh://git@code.example.org/g/forum.git", both, "system-git"],
    ["  ", both, null],
  ] as const)("%s with %o publishes with %s", (url, tokens, expected) => {
    expect(forumCredential(url, tokens)).toBe(expected);
  });
});
