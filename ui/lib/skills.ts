/**
 * AgentSkills spec implementation
 * 
 * TS port of https://github.com/agentskills/agentskills/blob/main/skills-ref
 */

import fs from "fs";
import path from "path";
import matter from "gray-matter";


const MAX_SKILL_NAME_LENGTH = 64;
const MAX_DESCRIPTION_LENGTH = 1024;
const MAX_COMPATIBILITY_LENGTH = 500;

const ALLOWED_FIELDS = new Set([
  "name",
  "description",
  "license",
  "allowed-tools",
  "metadata",
  "compatibility",
]);


export class SkillError extends Error {}

export class ParseError extends SkillError {}

export class ValidationError extends SkillError {
  readonly errors: string[];
  constructor(message: string, errors?: string[]) {
    super(message);
    this.errors = errors ?? [message];
  }
}


export interface SkillProperties {
  name: string;
  description: string;
  license?: string;
  compatibility?: string;
  allowedTools?: string;
  metadata?: Record<string, string>;
}

/**
 * Find the SKILL.md file inside `skillDir`.
 *
 * Prefers `SKILL.md` (uppercase) but accepts `skill.md` (lowercase).
 * Returns the absolute path, or `null` if neither is found.
 */
export function findSkillMd(skillDir: string): string | null {
  for (const name of ["SKILL.md", "skill.md"]) {
    const candidate = path.join(skillDir, name);
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * Find all skills in the skill directories.
 */
export function findSkills(searchPaths: string[]): string[] {
  const results: string[] = [];
  for (const searchPath of searchPaths) {
    if (!fs.existsSync(searchPath) || !fs.statSync(searchPath).isDirectory()) continue;
    for (const entry of fs.readdirSync(searchPath, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const candidate = path.join(searchPath, entry.name);
      if (findSkillMd(candidate) !== null) {
        results.push(candidate);
      }
    }
  }
  return results;
}

interface Frontmatter {
  data: Record<string, unknown>;
  content: string;
}

function parseFrontmatter(filePath: string): Frontmatter {
  let raw: string;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch {
    throw new ParseError(`Cannot read file: ${filePath}`);
  }

  if (!raw.startsWith("---")) {
    throw new ParseError("SKILL.md must start with YAML frontmatter (---)");
  }

  let parsed: matter.GrayMatterFile<string>;
  try {
    parsed = matter(raw);
  } catch (err: unknown) {
    throw new ParseError(
      `Invalid YAML in frontmatter: ${err instanceof Error ? err.message : String(err)}`
    );
  }

  if (!parsed.data || typeof parsed.data !== "object" || Array.isArray(parsed.data)) {
    throw new ParseError("SKILL.md frontmatter must be a YAML mapping");
  }

  // Coerce metadata sub-object values to strings (mirrors Python behaviour)
  const data = parsed.data as Record<string, unknown>;
  if (data.metadata && typeof data.metadata === "object" && !Array.isArray(data.metadata)) {
    data.metadata = Object.fromEntries(
      Object.entries(data.metadata as Record<string, unknown>).map(([k, v]) => [
        String(k),
        String(v),
      ])
    );
  }

  return { data, content: parsed.content.trim() };
}

/**
 * Read skill properties from the SKILL.md frontmatter inside `skillDir`.
 *
 * Does NOT perform full validation — use `validate()` for that.
 *
 * @throws {ParseError}      if SKILL.md is absent or the YAML is malformed.
 * @throws {ValidationError} if required fields (`name`, `description`) are missing.
 */
export function readProperties(skillDir: string): SkillProperties {
  const skillMd = findSkillMd(skillDir);
  if (skillMd === null) {
    throw new ParseError(`SKILL.md not found in ${skillDir}`);
  }

  const { data } = parseFrontmatter(skillMd);

  if (!("name" in data)) {
    throw new ValidationError("Missing required field in frontmatter: name");
  }
  if (!("description" in data)) {
    throw new ValidationError("Missing required field in frontmatter: description");
  }

  const name = data.name;
  const description = data.description;

  if (typeof name !== "string" || !name.trim()) {
    throw new ValidationError("Field 'name' must be a non-empty string");
  }
  if (typeof description !== "string" || !description.trim()) {
    throw new ValidationError("Field 'description' must be a non-empty string");
  }

  return {
    name: name.trim(),
    description: description.trim(),
    license: typeof data.license === "string" ? data.license : undefined,
    compatibility: typeof data.compatibility === "string" ? data.compatibility : undefined,
    allowedTools: typeof data["allowed-tools"] === "string" ? data["allowed-tools"] : undefined,
    metadata:
      data.metadata && typeof data.metadata === "object" && !Array.isArray(data.metadata)
        ? (data.metadata as Record<string, string>)
        : undefined,
  };
}

function validateName(name: unknown, skillDir: string): string[] {
  const errors: string[] = [];

  if (!name || typeof name !== "string" || !name.trim()) {
    errors.push("Field 'name' must be a non-empty string");
    return errors;
  }

  // Unicode NFKC normalisation (mirrors Python unicodedata.normalize)
  const normalized = name.trim().normalize("NFKC");

  if (normalized.length > MAX_SKILL_NAME_LENGTH) {
    errors.push(
      `Skill name '${normalized}' exceeds ${MAX_SKILL_NAME_LENGTH} character limit (${normalized.length} chars)`
    );
  }
  if (normalized !== normalized.toLowerCase()) {
    errors.push(`Skill name '${normalized}' must be lowercase`);
  }
  if (normalized.startsWith("-") || normalized.endsWith("-")) {
    errors.push("Skill name cannot start or end with a hyphen");
  }
  if (normalized.includes("--")) {
    errors.push("Skill name cannot contain consecutive hyphens");
  }
  if (!/^[a-z0-9-]+$/.test(normalized)) {
    errors.push(
      `Skill name '${normalized}' contains invalid characters. Only letters, digits, and hyphens are allowed.`
    );
  }

  // Directory name must match skill name
  const dirName = path.basename(skillDir).normalize("NFKC");
  if (dirName !== normalized) {
    errors.push(
      `Directory name '${path.basename(skillDir)}' must match skill name '${normalized}'`
    );
  }

  return errors;
}

function validateDescription(description: unknown): string[] {
  if (!description || typeof description !== "string" || !description.trim()) {
    return ["Field 'description' must be a non-empty string"];
  }
  if (description.length > MAX_DESCRIPTION_LENGTH) {
    return [
      `Description exceeds ${MAX_DESCRIPTION_LENGTH} character limit (${description.length} chars)`,
    ];
  }
  return [];
}

function validateCompatibility(compatibility: unknown): string[] {
  if (typeof compatibility !== "string") {
    return ["Field 'compatibility' must be a string"];
  }
  if (compatibility.length > MAX_COMPATIBILITY_LENGTH) {
    return [
      `Compatibility exceeds ${MAX_COMPATIBILITY_LENGTH} character limit (${compatibility.length} chars)`,
    ];
  }
  return [];
}

function validateMetadataFields(data: Record<string, unknown>): string[] {
  const extra = Object.keys(data).filter((k) => !ALLOWED_FIELDS.has(k));
  if (extra.length === 0) return [];
  const allowed = [...ALLOWED_FIELDS].sort().join(", ");
  return [
    `Unexpected fields in frontmatter: ${extra.sort().join(", ")}. Only ${allowed} are allowed.`,
  ];
}

/**
 * Validate a skill directory against the AgentSkills spec.
 *
 * Returns an array of human-readable error strings.
 * An empty array means the skill is valid.
 */
export function validate(skillDir: string): string[] {
  if (!fs.existsSync(skillDir)) {
    return [`Path does not exist: ${skillDir}`];
  }
  if (!fs.statSync(skillDir).isDirectory()) {
    return [`Not a directory: ${skillDir}`];
  }

  const skillMd = findSkillMd(skillDir);
  if (skillMd === null) {
    return ["Missing required file: SKILL.md"];
  }

  let data: Record<string, unknown>;
  try {
    ({ data } = parseFrontmatter(skillMd));
  } catch (err) {
    return [err instanceof Error ? err.message : String(err)];
  }

  const errors: string[] = [];
  errors.push(...validateMetadataFields(data));

  if (!("name" in data)) {
    errors.push("Missing required field in frontmatter: name");
  } else {
    errors.push(...validateName(data.name, skillDir));
  }

  if (!("description" in data)) {
    errors.push("Missing required field in frontmatter: description");
  } else {
    errors.push(...validateDescription(data.description));
  }

  if ("compatibility" in data) {
    errors.push(...validateCompatibility(data.compatibility));
  }

  return errors;
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * Generate the `<available_skills>` XML block for inclusion in agent prompts.
 *
 * Each entry contains the skill's `<name>`, `<description>`, and the
 * resolved `<location>` path of its SKILL.md file.
 *
 * @param skillDirs Absolute or relative paths to skill directories.
 */
export function toPrompt(skillDirs: string[]): string {
  if (skillDirs.length === 0) {
    return "";
  }

  // instructions for how to use skills for models not pretrained with them
  const SKILL_INSTRUCTIONS = [
    "The `<available_skills>` block lists skills you can use. When a user's request matches a ",
    "skill's description, read the SKILL.md file first and follow the instructions inside it. ",
    "SKILL.md may reference other potentially relevant documentation and scripts to use. If ",
    "multiple skills are relevant use all of them.",
  ].join("")

  const lines: string[] = [
    SKILL_INSTRUCTIONS,
    "<available_skills>",
  ];

  for (const dir of skillDirs) {
    const resolved = path.resolve(dir);
    const props = readProperties(resolved);
    const skillMd = findSkillMd(resolved)!;

    lines.push(
      "  <skill>",
      `    <name>${escapeXml(props.name)}</name>`,
      `    <description>${escapeXml(props.description)}</description>`,
      `    <location>${escapeXml(skillMd)}</location>`,
      "  </skill>"
    );
  }

  lines.push("</available_skills>");
  return lines.join("\n");
}
