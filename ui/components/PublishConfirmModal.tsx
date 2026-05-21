"use client";

type Props = {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelActionLabel?: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

export function PublishConfirmModal({
  open,
  title,
  message,
  confirmLabel = "Publish",
  cancelActionLabel = "Cancel",
  busy = false,
  onCancel,
  onConfirm,
}: Props) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={title}
      >
        <div className="panel-header">
          <div className="panel-title">{title}</div>
          <button type="button" className="button ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
        <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p style={{ margin: 0, whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{message}</p>
          <div className="skill-editor-actions">
            <button type="button" className="button" onClick={onCancel} disabled={busy}>
              {cancelActionLabel}
            </button>
            <button type="button" className="button secondary" onClick={onConfirm} disabled={busy}>
              {busy ? "Publishing…" : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
