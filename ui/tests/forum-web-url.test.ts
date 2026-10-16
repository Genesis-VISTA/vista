import { describe, expect, it } from "vitest";
import { forumWebUrl } from "@/lib/debates";

describe("forumWebUrl", () => {
  it.each([
    ["https://github.com/org/forum.git", "https://github.com/org/forum"],
    ["https://gitlab.com/group/sub/forum.git", "https://gitlab.com/group/sub/forum"],
    ["https://github.com/org/forum", "https://github.com/org/forum"],
  ])("opens %s at its repository page", (remote, page) => {
    expect(forumWebUrl(remote)).toBe(page);
  });

  it.each([
    ["an SSH remote", "git@github.com:org/forum.git"],
    ["a local path", "/Users/me/forum.git"],
    ["plain HTTP", "http://example.org/forum.git"],
    ["credentials in the URL", "https://oauth2:secret@gitlab.com/g/forum.git"],
  ])("gives no link for %s", (_, remote) => {
    expect(forumWebUrl(remote)).toBeNull();
  });
});
