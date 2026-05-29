import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for `POST /skills/import`.
 *
 * Body: `{ url: string }` pointing at either
 *   https://github.com/<owner>/<repo>                          (root SKILL.md)
 *   https://github.com/<owner>/<repo>/tree/<ref>/<subpath>     (monorepo entry)
 *
 * Returns the imported skill in the same `SkillSummary` envelope the rest of
 * the UI consumes, so the hub page can append it to its local catalog without
 * a full refetch.
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
    upstream = await fetch(backendUrl("/skills/import"), {
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
  type BackendSkill = {
    name: string;
    description: string;
    metadata?: Record<string, string | string[]> | null;
    tags?: string[];
    author?: string | null;
    repo_url?: string | null;
    is_public?: boolean;
  };
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
