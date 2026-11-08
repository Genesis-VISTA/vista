import { NextResponse } from "next/server";
import { postBackend, toSummary, type BackendSkill } from "../../import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /skills/{name}/publish`: publish the skill to
 * the collaboration server. Body `{ on_top_of?, as_new? }`. A success is the
 * skill as a `SkillSummary`; an error passes through with its status and body,
 * whose `code` (`version_conflict` with `head_version`, `remote_missing`,
 * `collaboration_unavailable`, …) tells the page what to offer.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;
  if (!/^[a-zA-Z0-9._-]+$/.test(slug)) {
    return NextResponse.json({ detail: "Skill not found" }, { status: 404 });
  }
  const sent = await postBackend(request, `/skills/${encodeURIComponent(slug)}/publish`);
  if (sent instanceof NextResponse) return sent;
  if (sent.status !== 200) {
    return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
  }
  return NextResponse.json(toSummary(sent.payload as BackendSkill));
}
