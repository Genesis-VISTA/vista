import { NextResponse } from "next/server";
import { postBackend } from "../../../../skills/import/_imported";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /collaboration/knowledge-bases/{id}/import`.
 * Body `{ version?, rename_to?, replace_modified? }`. A success is
 * `{ status, knowledge_base }`; an error passes through, its `code`
 * (`name_taken`, `name_invalid`, `modified`, …) saying what to ask. A 409
 * without a code is an index this VISTA cannot search, or a copy indexing.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  if (!/^[0-9a-fA-F-]{36}$/.test(id)) {
    return NextResponse.json({ detail: "No such knowledge base on the server" }, { status: 404 });
  }
  const sent = await postBackend(request, `/collaboration/knowledge-bases/${id}/import`);
  if (sent instanceof NextResponse) return sent;
  return NextResponse.json(sent.payload ?? { detail: "Failed" }, { status: sent.status });
}
