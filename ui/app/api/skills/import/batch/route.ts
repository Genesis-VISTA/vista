import { NextResponse } from "next/server";
import { postBackend, toSummary, type BackendSkill } from "../_imported";

export const runtime = "nodejs";

type BackendResult = {
  path: string;
  name: string | null;
  status: string;
  detail: string | null;
  skill: BackendSkill | null;
};

/**
 * Proxy for `POST /skills/import/batch`.
 *
 * Body: `{ url, commit?, project?, token?, skills: [{ path, on_conflict?, rename_to? }] }`.
 * Replies with one result per chosen skill; each installed skill is reshaped
 * into a `SkillSummary`, like the single import's reply.
 */
export async function POST(request: Request) {
  const result = await postBackend(request, "/skills/import/batch");
  if (result instanceof NextResponse) return result;
  if (result.status !== 200 || !Array.isArray(result.payload)) {
    return NextResponse.json(result.payload ?? { error: "Failed" }, { status: result.status });
  }
  return NextResponse.json(
    (result.payload as BackendResult[]).map((r) => ({
      ...r,
      skill: r.skill ? toSummary(r.skill) : null,
    }))
  );
}
