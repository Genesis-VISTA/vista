import { NextResponse } from "next/server";
import { postBackend } from "../_imported";

export const runtime = "nodejs";

/**
 * Proxy for `POST /skills/import/scan`.
 *
 * Body: `{ url: string, token?: string }`. Lists every skill in the repository
 * (or the folder the URL links to) without installing any, with the commit to
 * pass back to `/api/skills/import/batch`. The reply passes through unchanged.
 */
export async function POST(request: Request) {
  const result = await postBackend(request, "/skills/import/scan");
  if (result instanceof NextResponse) return result;
  return NextResponse.json(result.payload ?? { error: "Failed" }, { status: result.status });
}
