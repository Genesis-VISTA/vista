/**
 * @jest-environment node
 */
import path from "path";
import fs from "fs";
import os from "os";
import { findSkillMd, findSkills, readProperties, validate, toPrompt } from "@/lib/skills";

let tmpDir: string;

beforeAll(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vista-skills-lib-test-"));
});

afterAll(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function createSkill(
  name: string,
  frontmatter: Record<string, unknown>,
  body = "# Skill\n\nBody content."
): string {
  const dir = path.join(tmpDir, name);
  fs.mkdirSync(dir, { recursive: true });

  const yamlLines = Object.entries(frontmatter).map(([k, v]) => {
    if (typeof v === "object") return `${k}:\n` + Object.entries(v as Record<string, string>).map(([mk, mv]) => `  ${mk}: ${mv}`).join("\n");
    return `${k}: ${v}`;
  });
  const content = `---\n${yamlLines.join("\n")}\n---\n\n${body}`;
  fs.writeFileSync(path.join(dir, "SKILL.md"), content);
  return dir;
}

function cleanup(dir: string) {
  if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true });
}

describe("findSkillMd", () => {
  it("finds SKILL.md (uppercase)", () => {
    const dir = createSkill("find-upper", { name: "find-upper", description: "test" });
    expect(findSkillMd(dir)).toBe(path.join(dir, "SKILL.md"));
    cleanup(dir);
  });

  it("finds skill.md (lowercase)", () => {
    const dir = path.join(tmpDir, "find-lower");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "skill.md"), "---\nname: find-lower\ndescription: test\n---\n");
    expect(findSkillMd(dir)).toBe(path.join(dir, "skill.md"));
    cleanup(dir);
  });

  it("returns null when no SKILL.md exists", () => {
    const dir = path.join(tmpDir, "no-skill");
    fs.mkdirSync(dir, { recursive: true });
    expect(findSkillMd(dir)).toBeNull();
    cleanup(dir);
  });
});

describe("findSkills", () => {
  it("discovers skill directories within search paths", () => {
    const searchDir = path.join(tmpDir, "search-parent");
    fs.mkdirSync(searchDir, { recursive: true });

    const s1 = path.join(searchDir, "skill-a");
    const s2 = path.join(searchDir, "skill-b");
    const notSkill = path.join(searchDir, "not-a-skill");
    fs.mkdirSync(s1, { recursive: true });
    fs.mkdirSync(s2, { recursive: true });
    fs.mkdirSync(notSkill, { recursive: true });
    fs.writeFileSync(path.join(s1, "SKILL.md"), "---\nname: skill-a\ndescription: A\n---\n");
    fs.writeFileSync(path.join(s2, "SKILL.md"), "---\nname: skill-b\ndescription: B\n---\n");
    // notSkill has no SKILL.md

    const results = findSkills([searchDir]);
    expect(results).toHaveLength(2);
    expect(results).toContain(s1);
    expect(results).toContain(s2);

    cleanup(searchDir);
  });

  it("returns empty array for non-existent directory", () => {
    expect(findSkills(["/nonexistent/path"])).toEqual([]);
  });
});

describe("readProperties", () => {
  it("reads name and description from frontmatter", () => {
    const dir = createSkill("read-props", { name: "read-props", description: "A readable skill" });
    const props = readProperties(dir);
    expect(props.name).toBe("read-props");
    expect(props.description).toBe("A readable skill");
    cleanup(dir);
  });

  it("reads optional fields", () => {
    const dir = createSkill("read-optional", {
      name: "read-optional",
      description: "Optional fields test",
      license: "MIT",
      compatibility: "Claude 3.5+",
    });
    const props = readProperties(dir);
    expect(props.license).toBe("MIT");
    expect(props.compatibility).toBe("Claude 3.5+");
    cleanup(dir);
  });

  it("throws ParseError when SKILL.md is missing", () => {
    const dir = path.join(tmpDir, "no-skillmd");
    fs.mkdirSync(dir, { recursive: true });
    expect(() => readProperties(dir)).toThrow("SKILL.md not found");
    cleanup(dir);
  });

  it("throws ValidationError when name is missing", () => {
    const dir = path.join(tmpDir, "missing-name");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "SKILL.md"), "---\ndescription: No name\n---\n");
    expect(() => readProperties(dir)).toThrow("name");
    cleanup(dir);
  });
});

describe("validate", () => {
  it("returns empty array for valid skill", () => {
    const dir = createSkill("valid-skill", { name: "valid-skill", description: "A valid skill" });
    const errors = validate(dir);
    expect(errors).toEqual([]);
    cleanup(dir);
  });

  it("detects uppercase in skill name", () => {
    const dir = createSkill("uppercase-name", { name: "UpperCase", description: "Bad name" });
    const errors = validate(dir);
    expect(errors.some((e) => e.includes("lowercase"))).toBe(true);
    cleanup(dir);
  });

  it("detects name/directory mismatch", () => {
    const dir = createSkill("dir-mismatch", { name: "different-name", description: "Mismatch" });
    const errors = validate(dir);
    expect(errors.some((e) => e.includes("must match"))).toBe(true);
    cleanup(dir);
  });

  it("detects missing description", () => {
    const dir = path.join(tmpDir, "no-desc");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "SKILL.md"), "---\nname: no-desc\n---\n");
    const errors = validate(dir);
    expect(errors.some((e) => e.includes("description"))).toBe(true);
    cleanup(dir);
  });

  it("detects consecutive hyphens in name", () => {
    const dir = createSkill("bad--name", { name: "bad--name", description: "Bad" });
    const errors = validate(dir);
    expect(errors.some((e) => e.includes("consecutive hyphens"))).toBe(true);
    cleanup(dir);
  });

  it("detects unexpected fields", () => {
    const dir = createSkill("extra-fields", {
      name: "extra-fields",
      description: "Has extra",
      unknown_field: "should not be here",
    });
    const errors = validate(dir);
    expect(errors.some((e) => e.includes("Unexpected fields"))).toBe(true);
    cleanup(dir);
  });

  it("returns error for non-existent path", () => {
    const errors = validate("/nonexistent/path");
    expect(errors.some((e) => e.includes("does not exist"))).toBe(true);
  });
});

describe("toPrompt", () => {
  it("returns empty string for no skills", () => {
    expect(toPrompt([])).toBe("");
  });

  it("generates XML prompt with skill info", () => {
    const dir = createSkill("prompt-skill", { name: "prompt-skill", description: "For prompt generation" });
    const prompt = toPrompt([dir]);
    expect(prompt).toContain("<available_skills>");
    expect(prompt).toContain("</available_skills>");
    expect(prompt).toContain("<name>prompt-skill</name>");
    expect(prompt).toContain("<description>For prompt generation</description>");
    expect(prompt).toContain("<location>");
    cleanup(dir);
  });

  it("escapes XML special characters", () => {
    const dir = createSkill("xml-escape", {
      name: "xml-escape",
      description: 'Uses <tags> & "quotes"',
    });
    const prompt = toPrompt([dir]);
    expect(prompt).toContain("&lt;tags&gt;");
    expect(prompt).toContain("&amp;");
    expect(prompt).toContain("&quot;quotes&quot;");
    cleanup(dir);
  });
});
