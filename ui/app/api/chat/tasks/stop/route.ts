import { relay } from "../../../_relay";
import { tasksPath } from "../_path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** POST /api/chat/tasks/stop?project_name&chat_session_id&task_id: stop one task. */
export async function POST(request: Request) {
  const path = tasksPath(request, "stop");
  return typeof path === "string" ? relay(path, { method: "POST" }) : path;
}
