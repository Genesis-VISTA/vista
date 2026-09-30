import { describe, expect, it } from "vitest";
import { reportUrlTransform, sandboxFileUrl } from "@/lib/report-links";

describe("sandboxFileUrl", () => {
  it.each([
    [
      "/mnt/data/output/salt-plots/density.png",
      "/api/files/outputs/salt-plots/density.png?project_name=molten-salt",
    ],
    [
      "/mnt/data/uploads/reports/eutectic.md",
      "/api/files/uploads/reports/eutectic.md?project_name=molten-salt",
    ],
    [
      "file:///mnt/data/output/44379/log.out",
      "/api/files/outputs/44379/log.out?project_name=molten-salt",
    ],
    // Already percent-encoded in the markdown: kept, not double-encoded.
    [
      "/mnt/data/output/my%20plot.png",
      "/api/files/outputs/my%20plot.png?project_name=molten-salt",
    ],
    [
      "/mnt/data/output/a#b.png",
      "/api/files/outputs/a%23b.png?project_name=molten-salt",
    ],
  ])("maps %s", (url, expected) => {
    expect(sandboxFileUrl(url, "molten-salt")).toBe(expected);
  });

  it("encodes the project name", () => {
    expect(sandboxFileUrl("/mnt/data/output/a.png", "salt & pepper")).toBe(
      "/api/files/outputs/a.png?project_name=salt%20%26%20pepper"
    );
  });

  it.each([
    "/mnt/data/output/../../etc/passwd",
    "/mnt/data/output/./a.png",
    "/mnt/data/output/",
    "/mnt/skills/salt-analysis/SKILL.md",
    "https://example.org/figure.png",
    "/api/files/outputs/a.png?project_name=x",
  ])("leaves %s alone", (url) => {
    expect(sandboxFileUrl(url, "molten-salt")).toBeNull();
  });
});

describe("reportUrlTransform", () => {
  const transform = reportUrlTransform("molten-salt");

  it("rewrites sandbox paths", () => {
    expect(transform("/mnt/data/output/a.png")).toBe(
      "/api/files/outputs/a.png?project_name=molten-salt"
    );
  });

  it("passes other safe URLs through", () => {
    expect(transform("https://example.org/figure.png")).toBe(
      "https://example.org/figure.png"
    );
  });

  it("still strips unsafe protocols", () => {
    expect(transform("javascript:alert(1)")).toBe("");
  });
});
