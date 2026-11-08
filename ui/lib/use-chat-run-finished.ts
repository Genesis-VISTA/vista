"use client";

import { useEffect, useRef } from "react";
import { useChatRunStatus, type ChatRunStatusEntry } from "@/lib/chat-run-status";

/** A conversation is still producing files while it works or waits for the researcher. */
function activeIds(entries: readonly ChatRunStatusEntry[] | null): Set<string> {
  return new Set(
    (entries ?? [])
      .filter((e) => e.status === "working" || e.status === "needs_you")
      .map((e) => e.chat_session_id),
  );
}

/**
 * Conversations active in `before` that are not active in `after`. A conversation
 * that has dropped out of the status answer altogether has finished too, so
 * absence counts. Pure.
 */
export function finishedChats(
  before: readonly ChatRunStatusEntry[] | null,
  after: readonly ChatRunStatusEntry[] | null,
): string[] {
  const now = activeIds(after);
  return [...activeIds(before)].filter((id) => !now.has(id));
}

/**
 * Calls `onFinished` when any conversation in the project leaves `working` (or
 * `needs_you`) for something else. This rides the poll the nav rail already
 * runs, so it adds no request of its own.
 */
export function useOnChatRunFinished(
  projectName: string | null,
  onFinished: () => void,
): void {
  const { entries } = useChatRunStatus(projectName);
  const previous = useRef<{ project: string | null; entries: ChatRunStatusEntry[] | null }>({
    project: null,
    entries: null,
  });
  const callback = useRef(onFinished);
  useEffect(() => {
    callback.current = onFinished;
  });
  useEffect(() => {
    // Another project's answer is not a transition of this one's.
    const before = previous.current.project === projectName ? previous.current.entries : null;
    previous.current = { project: projectName, entries };
    if (before !== null && finishedChats(before, entries).length > 0) callback.current();
  }, [entries, projectName]);
}
