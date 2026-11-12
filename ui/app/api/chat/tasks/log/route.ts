import { relay } from "../../../_relay";
import { tasksPath } from "../_path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * GET /api/chat/tasks/log?project_name&chat_session_id&task_id: the end of a
 * command's log as plain text, which the Background tab's file preview shows.
 */
export async function GET(request: Request) {
  const path = tasksPath(request, "log");
  return typeof path === "string" ? relay(path) : path;
}
