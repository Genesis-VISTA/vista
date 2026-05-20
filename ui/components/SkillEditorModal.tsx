"use client";

import { useEffect, useState } from "react";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";

export type SkillDraftFields = {
  name: string;
  description: string;
  body: string;
  author: string;
  repoUrl: string;
  tags: string;
};

export type SkillSavedPayload = {
  name: string;
  description: string;
  body: string;
  author?: string;
  repoUrl?: string;
  tags: string[];
  isPublic: boolean;
};

type Props = {
  open: boolean;
  /**
   * Initial form values. `null` indicates the draft is still being fetched
   * from the backend; the modal shows a spinner state.
   */
  initial: SkillDraftFields | null;
  /** Banner text shown above the form (e.g. error from a failed save). */
  errorMessage?: string | null;
  onSave: (payload: SkillSavedPayload) => Promise<void> | void;
  onCancel: () => void;
};

const EMPTY: SkillDraftFields = {
  name: "",
  description: "",
  body: "",
  author: "",
  repoUrl: "",
  tags: "",
};

export function SkillEditorModal({
  open,
  initial,
  errorMessage,
  onSave,
  onCancel,
}: Props) {
  const [fields, setFields] = useState<SkillDraftFields>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [showPublishConfirm, setShowPublishConfirm] = useState(false);

  // Sync initial → fields whenever the modal opens or the draft arrives.
  useEffect(() => {
    if (initial) setFields(initial);
  }, [initial]);

  if (!open) return null;

  function update<K extends keyof SkillDraftFields>(key: K, value: SkillDraftFields[K]) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  async function submit(publish: boolean) {
    setSaving(true);
    try {
      await onSave({
        name: fields.name.trim(),
        description: fields.description.trim(),
        body: fields.body,
        author: fields.author.trim() || undefined,
        repoUrl: fields.repoUrl.trim() || undefined,
        tags: fields.tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        isPublic: publish,
      });
    } finally {
      setSaving(false);
    }
  }

  const drafting = initial === null;
  const disableSave =
    saving || drafting || !fields.name.trim() || !fields.description.trim() || !fields.body.trim();

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal skill-editor-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Save as skill"
      >
        <div className="panel-header">
          <div className="panel-title">Save as skill</div>
          <button type="button" className="button ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        </div>
        <div className="modal-body">
          {drafting ? (
            <div style={{ padding: "20px 0", textAlign: "center", color: "var(--muted)" }}>
              Drafting from your conversation…
            </div>
          ) : (
            <>
              {errorMessage && <div className="error">{errorMessage}</div>}
              <label className="skill-editor-label">
                <span>Slug (kebab-case)</span>
                <input
                  className="input"
                  placeholder="my-new-skill"
                  value={fields.name}
                  onChange={(e) => update("name", e.target.value)}
                />
              </label>
              <label className="skill-editor-label">
                <span>Description</span>
                <textarea
                  className="input"
                  rows={3}
                  value={fields.description}
                  onChange={(e) => update("description", e.target.value)}
                />
              </label>
              <div className="skill-editor-row">
                <label className="skill-editor-label">
                  <span>Author (optional)</span>
                  <input
                    className="input"
                    placeholder="Your name"
                    value={fields.author}
                    onChange={(e) => update("author", e.target.value)}
                  />
                </label>
                <label className="skill-editor-label">
                  <span>Repo URL (optional)</span>
                  <input
                    className="input"
                    placeholder="https://..."
                    value={fields.repoUrl}
                    onChange={(e) => update("repoUrl", e.target.value)}
                  />
                </label>
              </div>
              <label className="skill-editor-label">
                <span>Tags (comma separated)</span>
                <input
                  className="input"
                  placeholder="Materials Design, Frontier"
                  value={fields.tags}
                  onChange={(e) => update("tags", e.target.value)}
                />
              </label>
              <label className="skill-editor-label">
                <span>Body (Markdown — no YAML frontmatter)</span>
                <textarea
                  className="input skill-editor-body"
                  rows={14}
                  value={fields.body}
                  onChange={(e) => update("body", e.target.value)}
                />
              </label>
              <div className="skill-editor-actions">
                <button
                  type="button"
                  className="button"
                  disabled={disableSave}
                  onClick={() => void submit(false)}
                >
                  {saving ? "Saving…" : "Save (private)"}
                </button>
                <button
                  type="button"
                  className="button secondary"
                  disabled={disableSave}
                  onClick={() => setShowPublishConfirm(true)}
                  title="Save and publish to the Skill Hub (permanent)"
                >
                  Save & publish
                </button>
              </div>
            </>
          )}
        </div>
      </div>
      <PublishConfirmModal
        open={showPublishConfirm}
        title="Publish skill to Skill Hub"
        message={"Save & publish this skill to the Skill Hub?\n\nPublishing is permanent - once published, a skill cannot be made private again."}
        confirmLabel="Save & publish"
        busy={saving}
        onCancel={() => setShowPublishConfirm(false)}
        onConfirm={() => {
          setShowPublishConfirm(false);
          void submit(true);
        }}
      />
    </div>
  );
}
