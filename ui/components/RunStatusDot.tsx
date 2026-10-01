import type { ChatRunStatusKind } from "@/lib/chat-run-status";

/** What each dot means, read out in place of its colour. */
export const RUN_STATUS_LABELS: Record<ChatRunStatusKind, string> = {
  working: "Working",
  needs_you: "Needs you",
  done: "Finished, not yet opened",
  failed: "Failed, not yet opened",
  interrupted: "Interrupted, not yet opened",
};

/**
 * One conversation's run status as a dot: amber glowing while working, amber
 * with a ring when it needs the researcher, green for a finished turn they have
 * not opened, red for a failed or interrupted one.
 *
 * Colour is not the only signal: the dot carries its meaning as an image label
 * and a tooltip.
 */
export function RunStatusDot({
  status,
  className = "",
}: {
  status: ChatRunStatusKind;
  className?: string;
}) {
  const label = RUN_STATUS_LABELS[status];
  return (
    <span
      className={`run-dot ${className}`.trim()}
      data-status={status}
      role="img"
      aria-label={label}
      title={label}
    />
  );
}
