"use client";

import { useState } from "react";
import type { SkillSummary } from "@/lib/types";

type Props = {
  open: boolean;
  onCancel: () => void;
  onImported: (skill: SkillSummary) => void;
};

export function SkillImportModal({ open, onCancel, onImported }: Props) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function submit() {
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/skills/import", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: trimmed }),
      });
      const text = await resp.text();
      let payload: unknown = null;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        payload = { error: text };
      }
      if (!resp.ok) {
        let detail = `Import failed (${resp.status}).`;
        if (payload && typeof payload === "object") {
          if ("detail" in payload) detail = String((payload as { detail: unknown }).detail);
          else if ("error" in payload) detail = String((payload as { error: unknown }).error);
        }
        setError(detail);
        return;
      }
      onImported(payload as SkillSummary);
      setUrl("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Import skill from GitHub"
      >
        <div className="panel-header">
          <div className="panel-title">Import skill from GitHub</div>
          <button type="button" className="button ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
        <div className="modal-body">
          {error && <div className="error">{error}</div>}
          <label className="skill-editor-label">
            <span>Repository URL</span>
            <input
              className="input"
              autoFocus
              placeholder="https://github.com/owner/repo  or  .../tree/main/path/to/skill"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit();
              }}
              disabled={busy}
            />
          </label>
          <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
            Accepts a repo URL (SKILL.md at the root) or a tree URL into a
            sub-directory (e.g. <code>github.com/anthropics/skills/tree/main/skills/docx</code>).
            Imported skills land private; publish them from the /skills tab.
          </p>
          <div className="skill-editor-actions">
            <button
              type="button"
              className="button"
              disabled={busy || !url.trim()}
              onClick={() => void submit()}
            >
              {busy ? "Importing…" : "Import"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
