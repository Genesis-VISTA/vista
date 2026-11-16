"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import FilePreview from "@/components/FilePreview";
import {
  fetchTasks,
  formatRuntime,
  isLive,
  liveCount,
  runtimeAt,
  stopTask,
  taskLogUrl,
  taskState,
  taskSubject,
  type BackgroundTask,
} from "@/lib/background-tasks";

/** How often the open tab reads the tasks again. */
export const OPEN_POLL_MS = 3000;
/** How often a closed tab does, while something runs, so the label's count stays true. */
export const CLOSED_POLL_MS = 15000;

type Props = {
  projectName: string | null;
  chatSessionId: string | null;
  /** The tab is showing: read every 3 s rather than every 15 s. */
  active: boolean;
  /** Changes when a run event may have changed the tasks; each change reads them again. */
  refreshKey: number;
  /** Told the conversation's tasks each time they are read: the page shows the tab, and the count. */
  onTasksChange(tasks: BackgroundTask[]): void;
};

/**
 * The conversation's background commands and job watches (openspec change
 * background-tasks). Mounted whichever tab shows, like the Jobs tab's panel,
 * since it owns the reading that decides whether its tab exists at all.
 */
export default function BackgroundPanel({
  projectName,
  chatSessionId,
  active,
  refreshKey,
  onTasksChange,
}: Props) {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const [readAt, setReadAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const [openLog, setOpenLog] = useState<string | null>(null);
  /** Bumped by the log's Refresh: the preview reads a URL once, so a new one reads it again. */
  const [logVersion, setLogVersion] = useState(0);
  const [stopping, setStopping] = useState<Set<string>>(new Set());
  const onChange = useRef(onTasksChange);
  onChange.current = onTasksChange;

  const read = useCallback(
    async (signal?: AbortSignal) => {
      if (!projectName || !chatSessionId) return;
      const next = await fetchTasks(projectName, chatSessionId, signal).catch(() => null);
      if (next === null || signal?.aborted) return;
      setTasks(next);
      setReadAt(Date.now());
      onChange.current(next);
    },
    [projectName, chatSessionId],
  );

  // A new conversation starts empty, then reads its own.
  useEffect(() => {
    setTasks([]);
    setOpenLog(null);
    onChange.current([]);
  }, [projectName, chatSessionId]);

  useEffect(() => {
    const controller = new AbortController();
    void read(controller.signal);
    return () => controller.abort();
  }, [read, refreshKey]);

  const running = liveCount(tasks);
  useEffect(() => {
    if (!active && running === 0) return;
    const timer = window.setInterval(() => void read(), active ? OPEN_POLL_MS : CLOSED_POLL_MS);
    return () => window.clearInterval(timer);
  }, [read, active, running]);

  // Runtimes advance every second while the tab shows something live.
  useEffect(() => {
    if (!active || running === 0) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, running]);

  async function stop(task: BackgroundTask) {
    if (!projectName || !chatSessionId) return;
    setStopping((prev) => new Set(prev).add(task.id));
    try {
      await stopTask(projectName, chatSessionId, task.id);
      await read();
    } finally {
      setStopping((prev) => {
        const next = new Set(prev);
        next.delete(task.id);
        return next;
      });
    }
  }

  if (tasks.length === 0) {
    return <div className="background-empty">No background tasks in this conversation.</div>;
  }

  return (
    <ul className="background-list" aria-label="Background tasks">
      {tasks.map((task) => {
        const live = isLive(task);
        const logOpen = openLog === task.id;
        return (
          <li key={task.id} className="background-task" data-state={task.state}>
            <div className="background-task-head">
              <span className="background-task-label">{task.label}</span>
              <span className="background-task-subject" title={taskSubject(task)}>
                {taskSubject(task)}
              </span>
            </div>
            <div className="background-task-meta">
              <span className="background-task-state">{taskState(task)}</span>
              <span className="background-task-runtime">
                {formatRuntime(runtimeAt(task, readAt, Math.max(now, readAt)))}
              </span>
              <span className="background-task-actions">
                {task.kind === "command" && projectName && chatSessionId && (
                  <button
                    type="button"
                    className="button ghost button-xs"
                    aria-expanded={logOpen}
                    onClick={() => setOpenLog(logOpen ? null : task.id)}
                  >
                    {logOpen ? "Hide log" : "Log"}
                  </button>
                )}
                {live && (
                  <button
                    type="button"
                    className="button ghost button-xs"
                    disabled={stopping.has(task.id)}
                    onClick={() => void stop(task)}
                    title={
                      task.kind === "command"
                        ? "End the command and everything it started"
                        : "Stop watching; the job itself is left as it is"
                    }
                  >
                    {stopping.has(task.id) ? "Stopping…" : "Stop"}
                  </button>
                )}
              </span>
            </div>
            {task.state === "paused" && task.paused_reason && (
              <div className="background-task-paused" role="status">
                Paused: {task.paused_reason}
              </div>
            )}
            {logOpen && projectName && chatSessionId && (
              <div className="background-task-log">
                <div className="background-task-log-bar">
                  <span className="muted">The end of {task.log_path}</span>
                  <button
                    type="button"
                    className="button ghost button-xs"
                    onClick={() => setLogVersion((v) => v + 1)}
                  >
                    Refresh
                  </button>
                </div>
                <FilePreview
                  kind="outputs"
                  path={task.log_path ?? `${task.label}.log`}
                  name={`${task.label}.log`}
                  projectName={projectName}
                  src={`${taskLogUrl(projectName, chatSessionId, task.id)}&v=${logVersion}`}
                />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
