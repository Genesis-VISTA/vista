import { relay } from "../../_relay";
import { tasksPath } from "./_path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * GET /api/chat/tasks?project_name&chat_session_id: the conversation's
 * background commands and job watches (`lib/background-tasks.ts`), passed
 * through from the backend's `/projects/{p}/chat-sessions/{id}/tasks`.
 */
export async function GET(request: Request) {
  const path = tasksPath(request);
  return typeof path === "string" ? relay(path) : path;
}
