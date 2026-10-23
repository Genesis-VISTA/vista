import { NextResponse } from "next/server";
import {
  postBackend,
  toSummary,
  type BackendSkill,
} from "../../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /collaboration/skills/{id}/import`. Body
 * `{ version?, rename_to?, replace_modified?, project? }`. A success is
 * `{ status, skill }` with the skill as a `SkillSummary`; an error passes
 * through, its `code` (`name_taken`, `modified`, …) saying what to ask.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  if (!/^[0-9a-fA-F-]{36}$/.test(id)) {
    return NextResponse.json({ detail: "No such skill on the server" }, { status: 404 });
  }
  const sent = await postBackend(request, `/collaboration/skills/${id}/import`);
  if (sent instanceof NextResponse) return sent;
  if (sent.status !== 200) {
    return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
  }
  const { status, skill } = sent.payload as { status: string; skill: BackendSkill };
  return NextResponse.json({ status, skill: toSummary(skill) });
}
