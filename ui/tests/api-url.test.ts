import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("apiUrl", () => {
  it("is the path itself when there is no base path", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "");
    const { apiUrl } = await import("@/lib/api-url");
    expect(apiUrl("/api/projects?x=1")).toBe("/api/projects?x=1");
  });

  it("puts the base path in front when there is one", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/vista/ui");
    const { apiUrl, BASE_PATH } = await import("@/lib/api-url");
    expect(BASE_PATH).toBe("/vista/ui");
    expect(apiUrl("/api/chat")).toBe("/vista/ui/api/chat");
  });
});

describe("withBasePath", () => {
  it("prefixes only same-origin /api/ paths", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/vista/ui");
    const { withBasePath, basePathUrlTransform } = await import("@/lib/api-url");
    expect(withBasePath("/api/files/outputs/a.png?project_name=x")).toBe(
      "/vista/ui/api/files/outputs/a.png?project_name=x",
    );
    for (const other of [
      "https://example.org/api/x",
      "//host/api/x",
      "data:image/png;base64,AAA",
      "#api",
      "/projects",
      "file:///mnt/data/output/a.png",
    ]) {
      expect(withBasePath(other)).toBe(other);
    }
    expect(basePathUrlTransform("/api/files/outputs/a.png")).toBe("/vista/ui/api/files/outputs/a.png");
    expect(basePathUrlTransform("javascript:alert(1)")).toBe("");
  });

  it("changes nothing without a base path", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "");
    const { withBasePath } = await import("@/lib/api-url");
    expect(withBasePath("/api/files/x")).toBe("/api/files/x");
  });
});
