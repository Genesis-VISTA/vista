/**
 * A conversation's background tasks (openspec change background-tasks): sandbox
 * commands started with `run_bash(background=true)` and job watches from
 * `watch_hpc_job`, as the Background tab shows them.
 */

export type BackgroundTask = {
  id: string;
  /** What the agent and the researcher call it: `bg-3`, `watch-2`. */
  label: string;
  /** `command` or `job_watch`. */
  kind: string;
  /**
   * A command: `running`, then `exited`, `stopped`, `timed_out` or `interrupted`.
   * A watch: `watching` or `paused`, then `ended` or `stopped`.
   */
  state: string;
  command?: string | null;
  job_id?: string | null;
  cluster?: string | null;
  log_path?: string | null;
  timeout_s?: number | null;
  exit_status?: number | null;
  last_job_state?: string | null;
  /** Why a paused watch waits: what the researcher has to do. */
  paused_reason?: string | null;
  created_at: string;
  ended_at?: string | null;
  /** Seconds from start to end, or to when it was read while it runs. */
  runtime_s: number;
};

const LIVE = new Set(["running", "watching", "paused"]);

export function isLive(task: BackgroundTask): boolean {
  return LIVE.has(task.state);
}

export function liveCount(tasks: BackgroundTask[]): number {
  return tasks.filter(isLive).length;
}

/** Live tasks of a conversation, as the delete dialog names them. */
export function liveTasks(tasks: BackgroundTask[]): BackgroundTask[] {
  return tasks.filter(isLive);
}

/** A runtime as a person would say it: 4s, 4m 2s, 3h 2m. */
export function formatRuntime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes}m ${s % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** The runtime now: a live task's keeps advancing from when it was read. */
export function runtimeAt(task: BackgroundTask, readAt: number, now: number): number {
  return isLive(task) ? task.runtime_s + Math.max(0, now - readAt) / 1000 : task.runtime_s;
}

/** What the task is: its command, or the job it watches. */
export function taskSubject(task: BackgroundTask): string {
  if (task.kind === "command") return task.command ?? "";
  const cluster = task.cluster ? task.cluster[0].toUpperCase() + task.cluster.slice(1) : "";
  return `Job ${task.job_id ?? "?"} on ${cluster}`;
}

/** Its state in words: exited 7, timed out, watching (queued), …. */
export function taskState(task: BackgroundTask): string {
  switch (task.state) {
    case "exited":
      return `exited ${task.exit_status ?? "?"}`;
    case "timed_out":
      return "timed out";
    case "watching":
      return task.last_job_state ? `watching · ${task.last_job_state.toLowerCase()}` : "watching";
    case "ended":
      return task.last_job_state ? `ended · ${task.last_job_state.toLowerCase()}` : "ended";
    default:
      return task.state;
  }
}

function query(projectName: string, chatSessionId: string, taskId?: string): string {
  const params = new URLSearchParams({ project_name: projectName, chat_session_id: chatSessionId });
  if (taskId) params.set("task_id", taskId);
  return params.toString();
}

/** The conversation's tasks, oldest first; empty when they could not be read. */
export async function fetchTasks(
  projectName: string,
  chatSessionId: string,
  signal?: AbortSignal,
): Promise<BackgroundTask[]> {
  const response = await fetch(`/api/chat/tasks?${query(projectName, chatSessionId)}`, {
    signal,
    cache: "no-store",
  });
  if (!response.ok) return [];
  const body: unknown = await response.json().catch(() => null);
  return Array.isArray(body) ? (body as BackgroundTask[]) : [];
}

/** Stop a task; the task as it ended, or null if the stop failed. */
export async function stopTask(
  projectName: string,
  chatSessionId: string,
  taskId: string,
): Promise<BackgroundTask | null> {
  const response = await fetch(`/api/chat/tasks/stop?${query(projectName, chatSessionId, taskId)}`, {
    method: "POST",
  });
  if (!response.ok) return null;
  return (await response.json().catch(() => null)) as BackgroundTask | null;
}

/** Where the end of a command's log is read from, for the file preview. */
export function taskLogUrl(projectName: string, chatSessionId: string, taskId: string): string {
  return `/api/chat/tasks/log?${query(projectName, chatSessionId, taskId)}`;
}
