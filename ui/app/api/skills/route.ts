import { NextResponse } from "next/server";
import { backendUrl } from "../_backend";

export const runtime = "nodejs";

type BackendSkill = {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  metadata?: Record<string, string | string[]> | null;
  tags?: string[];
};

/**
 * Proxy + reshape for the backend's `GET /skills`.
 *
 * Backend returns AgentSkills spec metadata (name, description, metadata, tags).
 * The frontend additionally expects `slug`, `path`, and an `addedAt` mtime hint;
 * `slug` is the kebab-case skill name (the spec requires the directory name to
 * match the skill name), `path` is synthesised, and `addedAt` is not available
 * over the wire so we send null.
 */
export async function GET() {
  try {
    const upstream = await fetch(backendUrl("/skills"), {
      headers: { accept: "application/json" },
    });
    if (!upstream.ok) {
      return NextResponse.json([], { status: 200 });
    }
    const skills = (await upstream.json()) as BackendSkill[];
    const summaries = skills.map((skill) => ({
      slug: skill.name,
      name: skill.name,
      description: skill.description,
      path: `skills/${skill.name}/SKILL.md`,
      metadata: skill.metadata ?? undefined,
      tags: skill.tags ?? [],
      addedAt: null,
    }));
    summaries.sort((a, b) => a.slug.localeCompare(b.slug));
    return NextResponse.json(summaries);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
