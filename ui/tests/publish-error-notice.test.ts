import { describe, expect, it } from "vitest";
import { publishErrorNotice, type PublishError } from "@/lib/debates";

const error = (overrides: Partial<PublishError>): PublishError => ({
  reason: "refused",
  credential: "settings-github",
  message: "remote: Permission denied",
  at: "2026-10-14T20:00:00Z",
  ...overrides,
});

describe("publishErrorNotice", () => {
  it("names the Settings token and points at Settings", () => {
    const notice = publishErrorNotice(error({ credential: "settings-github" }));
    expect(notice.headline).toBe("Your GitHub token from Settings was refused by the forum.");
    expect(notice.detail).toMatch(/Settings → Repositories/);
  });

  it("says when no token was sent, and where one can be added", () => {
    const notice = publishErrorNotice(error({ credential: "system-git" }));
    expect(notice.headline).toBe("Your system git’s credentials were refused by the forum.");
    expect(notice.detail).toMatch(/Add a token in Settings → Repositories/);
    expect(notice.detail).toMatch(/SSH key or a credential helper/);
    expect(notice.detail).not.toMatch(/project/i);
  });

  it("calls an unreachable forum unreachable, and names no token", () => {
    const notice = publishErrorNotice(
      error({ reason: "unreachable", credential: "settings-github" }),
    );
    expect(notice.headline).toBe("The forum could not be reached.");
    expect(`${notice.headline} ${notice.detail}`).not.toMatch(/token/i);
  });

  it("shows the message for anything else, blaming nothing", () => {
    const notice = publishErrorNotice(error({ reason: "failed", message: "weird thing" }));
    expect(notice.detail).toBe("Git said: weird thing");
    expect(`${notice.headline} ${notice.detail}`).not.toMatch(/token|reached/i);
  });
});
