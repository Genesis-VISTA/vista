import { NextResponse } from "next/server";
import { postBackend } from "../../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /collaboration/projects/{id}/import`. Body
 * `{ version?, rename_to?, replace_modified? }`. A success is
 * `{ status, project, skills, forum_error }`; an error
 * passes through, its `code` (`name_taken`, `modified`, …) saying what to ask.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  if (!/^[0-9a-fA-F-]{36}$/.test(id)) {
    return NextResponse.json({ detail: "No such project on the server" }, { status: 404 });
  }
  const sent = await postBackend(request, `/collaboration/projects/${id}/import`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
