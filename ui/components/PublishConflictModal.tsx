"use client";

import type { SharingRefusal } from "@/lib/skill-sharing";
import type { SkillSummary } from "@/lib/types";

/** A publish the server refused until the researcher chooses what to do. */
export type Conflict = { skill: SkillSummary; refusal: SharingRefusal };

/**
 * The server's head moved past the version this skill or project was published
 * as, or it is gone from the server: the researcher decides what happens next.
 */
export function PublishConflictModal({
  name,
  noun,
  refusal,
  onCancel,
  onTakeServers,
  onPublishOnTop,
  onPublishAsNew,
}: {
  name: string;
  noun: "skill" | "project";
  refusal: SharingRefusal;
  onCancel: () => void;
  onTakeServers: () => void;
  onPublishOnTop: (head: number) => void;
  onPublishAsNew: () => void;
}) {
  const head = refusal.code === "version_conflict" ? refusal.headVersion : null;
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Publish conflict"
      >
        <div className="panel-header">
          <div className="panel-title">Not published</div>
          <button type="button" className="button ghost" onClick={onCancel}>
            Cancel
          </button>
        </div>
        <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p style={{ margin: 0 }}>{refusal.detail}</p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {head !== null && (
              <>
                <button type="button" className="button secondary" onClick={onTakeServers}>
                  Use the server&apos;s version {head}
                </button>
                <button type="button" className="button secondary" onClick={() => onPublishOnTop(head)}>
                  Publish mine as version {head + 1}
                </button>
              </>
            )}
            <button type="button" className="button" onClick={onPublishAsNew}>
              Publish {name} as a new {noun}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
