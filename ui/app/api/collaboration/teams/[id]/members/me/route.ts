import { NextResponse } from "next/server";
import { relay, TEAM_ID } from "../../../../../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `DELETE /collaboration/teams/{id}/members/me`: leave
 * the team (refused for its last member). 204 on success.
 */
export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!TEAM_ID.test(id)) return NextResponse.json({ detail: "No such team." }, { status: 404 });
  return relay(`/collaboration/teams/${id}/members/me`, { method: "DELETE" });
}
