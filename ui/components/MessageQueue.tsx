"use client";

import { useState } from "react";
import type { InboxItem } from "@/lib/message-queue";

type Props = {
  /** The researcher's queued messages, oldest first. */
  items: InboxItem[];
  /** Save an edit. Resolves when the queue has been told; the chip follows `items`. */
  onEdit(id: number, text: string): Promise<void>;
  onDelete(id: number): Promise<void>;
};

/**
 * Messages sent while the agent was busy, shown above the message box until the
 * running turn takes them (openspec change background-tasks). Each can be
 * edited or deleted until then; a chip leaves when its message is taken, and the
 * message appears in the conversation where the agent received it.
 */
export default function MessageQueue({ items, onEdit, onDelete }: Props) {
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  if (items.length === 0) return null;

  async function save() {
    if (!editing) return;
    const text = editing.text.trim();
    if (!text) return;
    setBusy(editing.id);
    try {
      await onEdit(editing.id, text);
    } finally {
      setBusy(null);
      setEditing(null);
    }
  }

  async function remove(id: number) {
    setBusy(id);
    try {
      await onDelete(id);
    } finally {
      setBusy(null);
    }
  }

  return (
    <ul className="message-queue" aria-label="Queued messages">
      {items.map((item) =>
        editing?.id === item.id ? (
          <li key={item.id} className="message-queue-chip editing">
            <textarea
              className="input"
              rows={2}
              aria-label="Edit queued message"
              value={editing.text}
              autoFocus
              onChange={(event) => setEditing({ id: item.id, text: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void save();
                } else if (event.key === "Escape") {
                  setEditing(null);
                }
              }}
            />
            <div className="message-queue-actions">
              <button
                type="button"
                className="button button-xs"
                onClick={() => void save()}
                disabled={busy === item.id || !editing.text.trim()}
              >
                Save
              </button>
              <button type="button" className="button ghost button-xs" onClick={() => setEditing(null)}>
                Cancel
              </button>
            </div>
          </li>
        ) : (
          <li key={item.id} className="message-queue-chip">
            <span className="message-queue-label">Queued</span>
            <span className="message-queue-text">{item.text}</span>
            <button
              type="button"
              className="message-queue-icon"
              aria-label="Edit queued message"
              title="Edit"
              disabled={busy === item.id}
              onClick={() => setEditing({ id: item.id, text: item.text })}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
              </svg>
            </button>
            <button
              type="button"
              className="message-queue-icon"
              aria-label="Delete queued message"
              title="Delete"
              disabled={busy === item.id}
              onClick={() => void remove(item.id)}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M6 6l12 12" />
                <path d="M18 6L6 18" />
              </svg>
            </button>
          </li>
        ),
      )}
    </ul>
  );
}
