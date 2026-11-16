import { NextResponse } from "next/server";
import { jsonBody, relay } from "../../_relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The messages queued for a conversation's running turn (`lib/message-queue.ts`).
 *
 * - GET    `?project_name&chat_session_id`          what is queued, in arrival order
 * - PATCH  `?project_name&chat_session_id&item_id`  `{text}`: edit a queued message
 * - DELETE `?project_name&chat_session_id&item_id`  delete a queued message
 *
 * Each proxies the backend's `/projects/{p}/chat-sessions/{id}/inbox[/{item}]`
 * and passes its answer through, so a 409 once the agent has taken the message
 * keeps its `code` and the item as delivered.
 */

const ITEM_ID = /^\d+$/;

function inboxPath(request: Request, withItem: boolean): string | NextResponse {
  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const chatSessionId = url.searchParams.get("chat_session_id");
  const itemId = url.searchParams.get("item_id");
  if (!projectName) return NextResponse.json({ detail: "Missing project_name." }, { status: 400 });
  if (!chatSessionId) {
    return NextResponse.json({ detail: "Missing chat_session_id." }, { status: 400 });
  }
  const base = `/projects/${encodeURIComponent(projectName)}/chat-sessions/${encodeURIComponent(chatSessionId)}/inbox`;
  if (!withItem) return base;
  if (!itemId || !ITEM_ID.test(itemId)) {
    return NextResponse.json({ detail: "Missing or invalid item_id." }, { status: 400 });
  }
  return `${base}/${itemId}`;
}

export async function GET(request: Request) {
  const path = inboxPath(request, false);
  return typeof path === "string" ? relay(path) : path;
}

export async function PATCH(request: Request) {
  const path = inboxPath(request, true);
  if (typeof path !== "string") return path;
  const body = await jsonBody(request);
  return typeof body === "string" ? relay(path, { method: "PATCH", body }) : body;
}

export async function DELETE(request: Request) {
  const path = inboxPath(request, true);
  return typeof path === "string" ? relay(path, { method: "DELETE" }) : path;
}
