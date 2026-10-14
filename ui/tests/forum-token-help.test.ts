import { describe, expect, it } from "vitest";
import { forumTokenHelp, TOKEN_EXPIRY_DAYS } from "@/lib/forum-token-help";

describe("forumTokenHelp", () => {
  it("pre-fills GitHub's fine-grained token form for the repository's owner", () => {
    const help = forumTokenHelp("https://github.com/sam-baumann/vista-forum-test.git");
    expect(help?.forge).toBe("GitHub");
    const url = new URL(help!.url);
    expect(url.origin + url.pathname).toBe(
      "https://github.com/settings/personal-access-tokens/new"
    );
    expect(url.searchParams.get("target_name")).toBe("sam-baumann");
    expect(url.searchParams.get("contents")).toBe("write");
    expect(url.searchParams.get("expires_in")).toBe(String(TOKEN_EXPIRY_DAYS));
    expect(url.searchParams.get("name")).toContain("vista-forum-test");
    expect(help!.steps[0]).toContain("vista-forum-test");
  });

  it("sends GitLab to its own server with both repository scopes", () => {
    const help = forumTokenHelp("https://gitlab.example.org/group/sub/forum.git");
    expect(help?.forge).toBe("GitLab");
    const url = new URL(help!.url);
    expect(url.origin).toBe("https://gitlab.example.org");
    expect(url.pathname).toBe("/-/user_settings/personal_access_tokens");
    expect(url.searchParams.get("scopes")).toBe("read_repository,write_repository");
    expect(url.searchParams.get("name")).toContain("forum");
  });

  it.each([
    "https://code.example.org/group/forum.git",
    "git@github.com:sam-baumann/forum.git",
    "http://github.com/sam-baumann/forum.git",
    "https://github.com/just-an-owner",
    "not a url",
  ])("has no link it cannot be sure of: %s", (url) => {
    expect(forumTokenHelp(url)).toBeNull();
  });
});
