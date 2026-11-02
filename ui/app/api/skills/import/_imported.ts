import { NextResponse } from "next/server";
import { sendBackend } from "../../_relay";

/** A skill row as the backend returns it (`SkillListItem`). */
export type BackendSkill = {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  // The AgentSkills-spec `metadata` dict; tags live under its `tags` key.
  metadata?: Record<string, string | string[]> | null;
  repo_url?: string | null;
  bundled?: boolean;
  origin_server?: string | null;
  remote_id?: string | null;
  remote_version?: number | null;
  modified?: boolean;
  team_id?: string | null;
  // HPC jobs the skill's folder ships under `hpc_jobs/` (read from disk).
  hpc_jobs?: string[];
  created_at?: string;
  updated_at?: string;
};

/** The collaboration fields of a backend skill, as the UI names them. */
export function toSharing(skill: BackendSkill) {
  return {
    bundled: skill.bundled ?? false,
    originServer: skill.origin_server ?? null,
    remoteId: skill.remote_id ?? null,
    remoteVersion: skill.remote_version ?? null,
    modified: skill.modified ?? false,
    teamId: skill.team_id ?? null,
  };
}

/**
 * Relay the backend's reply to a single-skill import (repository URL or
 * uploaded folder). Errors pass through with their status; a success is reshaped into
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
  return NextResponse.json(toSummary(payload as BackendSkill), { status: 201 });
}

/** A backend skill row as the `SkillSummary` the rest of the UI consumes. */
export function toSummary(skill: BackendSkill) {
  // Tags are read out of the spec `metadata` dict; an imported skill only has
  // them if its SKILL.md declares `metadata.tags`, otherwise this is empty.
  const tags = Array.isArray(skill.metadata?.tags) ? skill.metadata!.tags : [];
  const createdAt = skill.created_at ? Date.parse(skill.created_at) : NaN;
  return {
    slug: skill.name,
    name: skill.name,
    description: skill.description,
    path: `skills/${skill.name}/SKILL.md`,
    metadata: skill.metadata ?? undefined,
    tags,
    repoUrl: skill.repo_url ?? null,
    hpcJobs: skill.hpc_jobs ?? [],
    addedAt: Number.isFinite(createdAt) ? createdAt : null,
    ...toSharing(skill),
  };
}

/** POST a JSON body to a backend skill-import route; returns its status and parsed reply. */
export async function postBackend(
  request: Request,
  path: string
): Promise<{ status: number; payload: unknown } | NextResponse> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  // A knowledge base's publish, import or copy answers only when it has all been moved.
  const sent = await sendBackend(path, {
    method: "POST",
    body: JSON.stringify(body ?? {}),
    longWait: true,
  });
  if (!sent.ok) return NextResponse.json({ error: "Upstream unavailable" }, { status: 502 });
  let payload: unknown = null;
  try {
    payload = sent.text ? JSON.parse(sent.text) : null;
  } catch {
    payload = { error: sent.text };
  }
  return { status: sent.status, payload };
}
