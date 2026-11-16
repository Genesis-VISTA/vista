import { NextResponse } from "next/server";

const UUID = /^[0-9a-fA-F-]{36}$/;

/**
 * The backend path of a conversation's tasks (`…/chat-sessions/{id}/tasks`),
 * with `/{task_id}/<suffix>` when `suffix` is given, from the request's query.
 */
export function tasksPath(request: Request, suffix?: string): string | NextResponse {
  const url = new URL(request.url);
  const projectName = url.searchParams.get("project_name");
  const chatSessionId = url.searchParams.get("chat_session_id");
  const taskId = url.searchParams.get("task_id");
  if (!projectName) return NextResponse.json({ detail: "Missing project_name." }, { status: 400 });
  if (!chatSessionId || !UUID.test(chatSessionId)) {
    return NextResponse.json({ detail: "Missing or invalid chat_session_id." }, { status: 400 });
  }
  const base = `/projects/${encodeURIComponent(projectName)}/chat-sessions/${chatSessionId}/tasks`;
  if (suffix === undefined) return base;
  if (!taskId || !UUID.test(taskId)) {
    return NextResponse.json({ detail: "Missing or invalid task_id." }, { status: 400 });
  }
  return `${base}/${taskId}/${suffix}`;
}
