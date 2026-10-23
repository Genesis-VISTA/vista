import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../_backend";
import { toSummary, type BackendSkill } from "./import/_imported";

export const runtime = "nodejs";

/**
 * Proxy + reshape for the backend's `GET /skills`.
 *
 * Backend returns AgentSkills spec metadata (name, description, metadata) plus
 * DB fields (created_at, updated_at). The frontend additionally expects `slug`,
 * `path`, `tags`, and an `addedAt` hint; `slug` is the kebab-case skill name
 * (the spec requires the directory name to match the skill name), `path` is
 * synthesised, `tags` are read out of the spec `metadata` dict, and `addedAt`
 * comes from the skill's `created_at`.
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
    const summaries = skills.map(toSummary);
    summaries.sort((a, b) => a.slug.localeCompare(b.slug));
    return NextResponse.json(summaries);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

/**
 * Create a new skill. Body shape matches the backend `SkillCreate` model —
 * `{ name, description, body, metadata?, project? }`.
 * The UI still sends a top-level `tags: string[]`; we fold it into the spec
 * `metadata` dict (under `tags`) here so the rest of the UI's tag handling is
 * unchanged. Backend rejects malformed slugs (400) and duplicate names (409).
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  // Move `tags` into `metadata.tags` so the outgoing body matches SkillCreate.
  const { tags, metadata, ...rest } = (body ?? {}) as Record<string, unknown>;
  const baseMeta = (metadata as Record<string, unknown> | undefined) ?? undefined;
  const folded =
    Array.isArray(tags) && tags.length
      ? { ...rest, metadata: { ...(baseMeta ?? {}), tags } }
      : { ...rest, ...(baseMeta ? { metadata: baseMeta } : {}) };

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/skills"), {
      method: "POST",
      headers: await backendHeaders({
        "content-type": "application/json",
        accept: "application/json",
      }),
      body: JSON.stringify(folded),
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
  // GET /skills for a single entry.
  return NextResponse.json(toSummary(payload as BackendSkill), { status: 201 });
}
