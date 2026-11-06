"use client";

import { useEffect, useRef, useState } from "react";

/**
 * A long value on one line that scrolls sideways, with a copy button at its
 * end: a Globus address is hundreds of characters, and wrapped it filled the
 * section. A copy flashes "Copied" over the line; if the clipboard is
 * unavailable, the button selects the value instead and says so.
 */
export function CopyableLine({
  value,
  label,
  copyLabel = "Copy address",
}: {
  value: string;
  label: string;
  /** The copy button's name. */
  copyLabel?: string;
}) {
  const [copied, setCopied] = useState<"copied" | "selected" | null>(null);
  // Bumped on every copy, so a second click flashes again.
  const [flash, setFlash] = useState(0);
  const lineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(null), 2000);
    return () => clearTimeout(t);
  }, [copied, flash]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied("copied");
      setFlash((n) => n + 1);
    } catch {
      const line = lineRef.current;
      if (line) {
        const range = document.createRange();
        range.selectNodeContents(line);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
      setCopied("selected");
    }
  }

  return (
    <>
      <div className="user-settings-globus-url-row">
        <div className="user-settings-globus-url-wrap">
          <div ref={lineRef} className="user-settings-globus-url" role="textbox" aria-readonly="true" aria-label={label} tabIndex={0}>
            {value}
          </div>
          <span className="user-settings-globus-copied" role="status" aria-live="polite">
            {copied === "copied" && (
              <span key={flash} className="user-settings-globus-copied-flash">
                Copied
              </span>
            )}
          </span>
        </div>
        <button
          type="button"
          className="button ghost button-xs user-settings-copy"
          aria-label={copyLabel}
          title={copyLabel}
          onClick={() => void copy()}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="9" y="9" width="12" height="12" rx="2" />
            <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
          </svg>
        </button>
      </div>
      {copied === "selected" && (
        <div className="user-settings-hint">Couldn&apos;t copy here; it is selected, so press ⌘C or Ctrl+C.</div>
      )}
    </>
  );
}
