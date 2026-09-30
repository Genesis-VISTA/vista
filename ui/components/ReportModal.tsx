"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { reportUrlTransform } from "@/lib/report-links";

/** A report draft as the backend returns it from `/reports/generate`. */
export type ReportDraft = {
  title: string;
  slug_suggestion: string;
  summary: string;
  body: string;
};

/** What "Save to project" sends: the draft as the user left it. */
export type ReportSavePayload = {
  title: string;
  summary: string;
  slug: string;
  body: string;
};

type Props = {
  open: boolean;
  projectName: string;
  /** `null` while a draft is being fetched; the modal shows a drafting state. */
  draft: ReportDraft | null;
  /** Banner text from a failed draft or save. */
  errorMessage?: string | null;
  /** Set once the report is saved: its path relative to the project uploads. */
  savedPath?: string | null;
  onRegenerate: (hint: string) => void;
  onSave: (payload: ReportSavePayload) => Promise<void> | void;
  /** Receives the current (possibly edited) body, used to steer the skill draft. */
  onSaveAsSkill: (body: string) => void;
  onClose: () => void;
};

const EMPTY: ReportDraft = { title: "", slug_suggestion: "", summary: "", body: "" };

export function ReportModal({
  open,
  projectName,
  draft,
  errorMessage,
  savedPath,
  onRegenerate,
  onSave,
  onSaveAsSkill,
  onClose,
}: Props) {
  const [fields, setFields] = useState<ReportDraft>(EMPTY);
  const [editing, setEditing] = useState(false);
  const [hint, setHint] = useState("");
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const urlTransform = useMemo(() => reportUrlTransform(projectName), [projectName]);

  // A new draft (first load or regenerate) replaces whatever was on screen.
  useEffect(() => {
    if (draft) {
      setFields(draft);
      setEditing(false);
    }
  }, [draft]);

  if (!open) return null;

  function update<K extends keyof ReportDraft>(key: K, value: ReportDraft[K]) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  async function save() {
    setSaving(true);
    try {
      await onSave({
        title: fields.title.trim(),
        summary: fields.summary.trim(),
        slug: fields.slug_suggestion.trim() || fields.title.trim(),
        body: fields.body,
      });
    } finally {
      setSaving(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(fields.body);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be unavailable (permissions, insecure origin); the body
      // is still selectable in the editor.
    }
  }

  const drafting = draft === null;
  const hasBody = !!fields.body.trim();
  const disableSave = saving || drafting || !hasBody || !fields.title.trim();

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal report-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Conversation report"
      >
        <div className="panel-header">
          <div className="panel-title">{drafting ? "Generating report" : fields.title || "Report"}</div>
          <button type="button" className="button ghost" onClick={onClose} disabled={saving}>
            Close
          </button>
        </div>
        <div className="modal-body">
          {errorMessage && <div className="error">{errorMessage}</div>}
          {drafting ? (
            <div className="report-status">Writing a report of your conversation…</div>
          ) : (
            <>
              {fields.summary && !editing && <p className="report-summary">{fields.summary}</p>}
              {editing ? (
                <>
                  <label className="skill-editor-label">
                    <span>Title</span>
                    <input
                      className="input"
                      value={fields.title}
                      onChange={(e) => update("title", e.target.value)}
                    />
                  </label>
                  <label className="skill-editor-label">
                    <span>One-line summary</span>
                    <input
                      className="input"
                      value={fields.summary}
                      onChange={(e) => update("summary", e.target.value)}
                    />
                  </label>
                  <label className="skill-editor-label">
                    <span>Report (Markdown)</span>
                    <textarea
                      className="input skill-editor-body"
                      rows={18}
                      value={fields.body}
                      onChange={(e) => update("body", e.target.value)}
                    />
                  </label>
                </>
              ) : hasBody ? (
                <div className="report-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={urlTransform}>
                    {fields.body}
                  </ReactMarkdown>
                </div>
              ) : null}

              <div className="report-regenerate">
                <input
                  className="input"
                  placeholder="Focus for a new draft, e.g. the density results"
                  aria-label="Regenerate focus"
                  value={hint}
                  onChange={(e) => setHint(e.target.value)}
                />
                <button
                  type="button"
                  className="button ghost"
                  disabled={saving}
                  onClick={() => onRegenerate(hint.trim())}
                  title="Draft the report again; replaces the current text, including edits"
                >
                  Regenerate with focus…
                </button>
              </div>

              {savedPath && (
                <div className="report-status">
                  Saved to project uploads as <code>{savedPath}</code>
                </div>
              )}

              <div className="skill-editor-actions">
                <button
                  type="button"
                  className="button ghost"
                  disabled={!hasBody}
                  onClick={() => setEditing((e) => !e)}
                >
                  {editing ? "Preview" : "Edit"}
                </button>
                <button type="button" className="button ghost" disabled={!hasBody} onClick={() => void copy()}>
                  {copied ? "Copied" : "Copy markdown"}
                </button>
                <button
                  type="button"
                  className="button secondary"
                  disabled={saving || !hasBody}
                  onClick={() => onSaveAsSkill(fields.body)}
                  title="Draft a reusable skill from this conversation, steered by the report"
                >
                  Save as skill
                </button>
                <button type="button" className="button" disabled={disableSave} onClick={() => void save()}>
                  {saving ? "Saving…" : savedPath ? "Save again" : "Save to project"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
