import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../_backend";

export const runtime = "nodejs";

type BackendSkill = {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  metadata?: Record<string, string | string[]> | null;
  tags?: string[];
  author?: string | null;
  repo_url?: string | null;
  is_public?: boolean;
  body?: string;
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
      headers: await backendHeaders({ accept: "application/json" }),
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
      author: skill.author ?? null,
      repoUrl: skill.repo_url ?? null,
      isPublic: skill.is_public ?? false,
      addedAt: null,
    }));
    summaries.sort((a, b) => a.slug.localeCompare(b.slug));
    return NextResponse.json(summaries);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

/**
 * Create a new skill on disk. Body shape matches the backend `SkillCreate`
 * model — `{ name, description, body, author?, repo_url?, tags?, is_public? }`.
 * Backend rejects malformed slugs (400) and duplicate names (409).
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/skills"), {
      method: "POST",
      headers: await backendHeaders({
        "content-type": "application/json",
        accept: "application/json",
      }),
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  }

  const text = await upstream.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { error: text };
  }
  if (!upstream.ok) {
    return NextResponse.json(payload ?? { error: "Failed" }, { status: upstream.status });
  }
  // Reshape to match the SkillSummary the rest of the UI consumes — same as
  // GET /skills for a single entry, plus we let through the camel-cased fields.
  const skill = payload as BackendSkill;
  return NextResponse.json(
    {
      slug: skill.name,
      name: skill.name,
      description: skill.description,
      path: `skills/${skill.name}/SKILL.md`,
      metadata: skill.metadata ?? undefined,
      tags: skill.tags ?? [],
      author: skill.author ?? null,
      repoUrl: skill.repo_url ?? null,
      isPublic: skill.is_public ?? false,
      addedAt: null,
    },
    { status: 201 }
  );
}
