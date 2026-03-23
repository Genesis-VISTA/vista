/**
 * @jest-environment node
 */
import path from "path";
import fs from "fs";
import os from "os";

// We'll create real temp skill directories and point config.skillsDir there
let tmpDir: string;

beforeAll(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vista-skills-test-"));
});

afterAll(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

jest.mock("@/app/config", () => {
  const _path = require("path");
  const _os = require("os");
  const _fs = require("fs");
  const dir = _fs.mkdtempSync(_path.join(_os.tmpdir(), "vista-skills-cfg-"));
  return {
    config: {
      skillsDir: dir,
      uploadsDir: _path.join(dir, "uploads"),
    },
    __testSetSkillsDir: (d: string) => {
      const mod = require("@/app/config");
      mod.config.skillsDir = d;
    },
  };
});

beforeEach(() => {
  // Point config to our tmp dir
  const { __testSetSkillsDir } = require("@/app/config");
  __testSetSkillsDir(tmpDir);
});

describe("GET /api/skills", () => {
  it("returns an empty array when no skills exist", async () => {
    const { GET } = await import("@/app/api/skills/route");
    const res = await GET();
    const data = await res.json();
    expect(Array.isArray(data)).toBe(true);
    expect(data).toHaveLength(0);
  });

  it("returns discovered skills", async () => {
    // Create a test skill
    const skillDir = path.join(tmpDir, "test-skill");
    fs.mkdirSync(skillDir, { recursive: true });
    fs.writeFileSync(
      path.join(skillDir, "SKILL.md"),
      `---\nname: test-skill\ndescription: A test skill for testing\n---\n\n# Test Skill\nThis is a test.\n`
    );

    const { GET } = await import("@/app/api/skills/route");
    const res = await GET();
    const data = await res.json();

    expect(Array.isArray(data)).toBe(true);
    expect(data.length).toBeGreaterThanOrEqual(1);

    const skill = data.find((s: { slug: string }) => s.slug === "test-skill");
    expect(skill).toBeTruthy();
    expect(skill.name).toBe("test-skill");
    expect(skill.description).toBe("A test skill for testing");

    // Cleanup
    fs.rmSync(skillDir, { recursive: true });
  });

  it("sorts skills by slug", async () => {
    const skillA = path.join(tmpDir, "alpha-skill");
    const skillB = path.join(tmpDir, "beta-skill");
    fs.mkdirSync(skillA, { recursive: true });
    fs.mkdirSync(skillB, { recursive: true });
    fs.writeFileSync(path.join(skillA, "SKILL.md"), "---\nname: alpha-skill\ndescription: Alpha\n---\n");
    fs.writeFileSync(path.join(skillB, "SKILL.md"), "---\nname: beta-skill\ndescription: Beta\n---\n");

    const { GET } = await import("@/app/api/skills/route");
    const res = await GET();
    const data = await res.json();

    const slugs = data.map((s: { slug: string }) => s.slug);
    expect(slugs.indexOf("alpha-skill")).toBeLessThan(slugs.indexOf("beta-skill"));

    // Cleanup
    fs.rmSync(skillA, { recursive: true });
    fs.rmSync(skillB, { recursive: true });
  });
});

describe("GET /api/skills/[slug]", () => {
  it("returns 404 for invalid slug characters", async () => {
    const { GET } = await import("@/app/api/skills/[slug]/route");
    const res = await GET(new Request("http://localhost/api/skills/../etc"), {
      params: { slug: "../etc" },
    });
    expect(res.status).toBe(404);
  });

  it("returns 404 for non-existent skill", async () => {
    const { GET } = await import("@/app/api/skills/[slug]/route");
    const res = await GET(new Request("http://localhost/api/skills/nonexistent"), {
      params: { slug: "nonexistent" },
    });
    expect(res.status).toBe(404);
  });

  it("returns skill detail with frontmatter and markdown", async () => {
    const skillDir = path.join(tmpDir, "detail-skill");
    fs.mkdirSync(skillDir, { recursive: true });
    fs.writeFileSync(
      path.join(skillDir, "SKILL.md"),
      `---\nname: detail-skill\ndescription: Detailed\nlicense: MIT\n---\n\n# Detailed Skill\n\nBody content here.\n`
    );

    const { GET } = await import("@/app/api/skills/[slug]/route");
    const res = await GET(new Request("http://localhost/api/skills/detail-skill"), {
      params: { slug: "detail-skill" },
    });

    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.slug).toBe("detail-skill");
    expect(data.frontmatter.name).toBe("detail-skill");
    expect(data.frontmatter.license).toBe("MIT");
    expect(data.markdown).toContain("Detailed Skill");

    fs.rmSync(skillDir, { recursive: true });
  });
});
