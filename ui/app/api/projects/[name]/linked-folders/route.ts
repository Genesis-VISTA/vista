import { NextResponse } from "next/server";
import { jsonBody, relay } from "../../../_relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxies for the backend's `GET`/`POST /projects/{name}/linked-folders`: a project's
 * linked folders, each with its availability, and linking one. A refusal passes
 * through with its status, `detail` and `code`.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ name: string }> },
) {
  const { name } = await params;
  return relay(`/projects/${encodeURIComponent(name)}/linked-folders`);
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> },
) {
  const { name } = await params;
  const body = await jsonBody(request);
  if (body instanceof NextResponse) return body;
  return relay(`/projects/${encodeURIComponent(name)}/linked-folders`, {
    method: "POST",
    body,
  });
}
