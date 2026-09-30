import { NextResponse } from "next/server";

type BackendSkill = {
  name: string;
  description: string;
  metadata?: Record<string, string | string[]> | null;
  author?: string | null;
  repo_url?: string | null;
  is_public?: boolean;
  created_at?: string;
};

/**
 * Relay the backend's reply to either import route (GitHub URL or uploaded
 * folder). Errors pass through with their status; a success is reshaped into
 * the `SkillSummary` envelope the rest of the UI consumes, so the hub page can
 * append it to its local catalog without a full refetch.
 */
export async function relayImported(upstream: Response): Promise<NextResponse> {
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
  const skill = payload as BackendSkill;
  // Tags are read out of the spec `metadata` dict; an imported skill only has
  // them if its SKILL.md declares `metadata.tags`, otherwise this is empty.
  const tags = Array.isArray(skill.metadata?.tags) ? skill.metadata!.tags : [];
  const createdAt = skill.created_at ? Date.parse(skill.created_at) : NaN;
  return NextResponse.json(
    {
      slug: skill.name,
      name: skill.name,
      description: skill.description,
      path: `skills/${skill.name}/SKILL.md`,
      metadata: skill.metadata ?? undefined,
      tags,
      author: skill.author ?? null,
      repoUrl: skill.repo_url ?? null,
      isPublic: skill.is_public ?? false,
      addedAt: Number.isFinite(createdAt) ? createdAt : null,
    },
    { status: 201 }
  );
}
