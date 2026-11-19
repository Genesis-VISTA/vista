"use client";

import type { ReactNode } from "react";
import type { FieldMark } from "@/lib/settings-autosave";

/** The element a field's label, error and jump-to-field all refer to. */
export function fieldId(field: string, suffix?: string): string {
  return `settings-field-${field}${suffix ? `-${suffix}` : ""}`;
}

/**
 * A field's save mark: a spinner for a slow save, a check that fades once
 * saved, a cross while failed, and nothing at all for a save in flight. Visual
 * only: the modal's live region says the same in words.
 */
export function SaveMark({ mark }: { mark?: FieldMark }) {
  if (!mark) return null;
  return (
    <span className="settings-mark" data-mark={mark} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        {mark === "saved" && <path d="M5 12.5l4.5 4.5L19 7.5" />}
        {mark === "failed" && <path d="M6 6l12 12M18 6L6 18" />}
        {mark === "slow" && <path d="M12 3a9 9 0 1 0 9 9" />}
      </svg>
    </span>
  );
}

/** Why a field's last save failed, under the field. */
export function FieldError({ field, error }: { field: string; error?: string }) {
  if (!error) return null;
  return (
    <span id={`${fieldId(field)}-error`} className="error settings-field-error">
      {error}
    </span>
  );
}

export function SectionHeader({
  titleId,
  title,
  place,
  children,
}: {
  titleId?: string;
  title: string;
  /** A line above the title, e.g. the cluster's institution and facility. */
  place?: string;
  /** Below the title: a description, or the cluster's status. */
  children?: ReactNode;
}) {
  return (
    <div className="settings-section-header">
      {place && <span className="settings-section-place">{place}</span>}
      <h2 id={titleId} className="settings-section-title">
        {title}
      </h2>
      {children}
    </div>
  );
}
