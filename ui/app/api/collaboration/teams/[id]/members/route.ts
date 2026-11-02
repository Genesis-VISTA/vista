import { NextResponse } from "next/server";
import { jsonBody, relay, TEAM_ID } from "../../../_relay";

export const runtime = "nodejs";

function noSuchTeam() {
  return NextResponse.json({ detail: "No such team." }, { status: 404 });
}

/** Proxy for the backend's `GET /collaboration/teams/{id}/members`; refused for the public team. */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!TEAM_ID.test(id)) return noSuchTeam();
  return relay(`/collaboration/teams/${id}/members`);
}

/**
 * Proxy for the backend's `POST /collaboration/teams/{id}/members`, body
 * `{ sub }`: add someone who has signed in to the server to the team.
 */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!TEAM_ID.test(id)) return noSuchTeam();
  const body = await jsonBody(request);
  if (body instanceof NextResponse) return body;
  return relay(`/collaboration/teams/${id}/members`, { method: "POST", body });
}
