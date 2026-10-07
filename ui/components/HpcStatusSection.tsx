"use client";

import { useEffect, useRef, useState, type HTMLAttributes } from "react";
import {
  HPC_CLUSTER_TITLES,
  useHpcStatus,
  type HpcCheck,
  type HpcCluster,
  type HpcClusterStatus,
  type HpcDisplayState,
} from "@/lib/hpc-status";

/**
 * The NavRail's HPC section: one card per visible cluster, saying whether
 * VISTA could use it for this researcher right now, and a details popover per
 * card listing each live check. Built to the "HPC Availability Cards" canvas.
 *
 * Every state has its own dot shape as well as its own color, so the cards
 * read without color; every card also carries its status word, and red words
 * are kept for the problems the researcher has to fix.
 */

export const STATE_LABELS: Record<HpcDisplayState, string> = {
  checking: "Checking…",
  ready: "Ready",
  degraded: "Degraded",
  unverifiable: "Couldn't verify",
  not_connected: "Not connected",
  rejected: "Token rejected",
  globus_not_connected: "Globus not connected",
  globus_session_expired: "Globus session expired",
};

/** Status words the researcher must act on are colored; the rest stay muted. */
export const WORD_TONE: Partial<Record<HpcDisplayState, "warn" | "urgent">> = {
  degraded: "warn",
  rejected: "urgent",
  globus_not_connected: "urgent",
  globus_session_expired: "urgent",
};

const SUBTITLES: Record<HpcCluster, string> = {
  frontier: "OLCF · moderate enclave · jobs via AmSC IRI",
  odo: "OLCF · open enclave · jobs via AmSC IRI",
  perlmutter: "NERSC · jobs via NERSC IRI",
  lux: "OLCF · Slurm over SSH",
};

const SHORT: Record<HpcCluster, string> = { frontier: "Fr", odo: "Od", perlmutter: "Pm", lux: "Lx" };

/** A state's dot. Shape and color both vary by state; see globals.css. */
export function HpcStatusDot({ state }: { state: HpcDisplayState }) {
  return <span className={`hpc-dot hpc-dot--${state}`} aria-hidden="true" data-state={state} />;
}

function minutesAgo(then: number, now: number): string {
  const minutes = Math.floor((now - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}

function expiresIn(iso: string, now: number): string {
  const ms = Date.parse(iso) - now;
  if (Number.isNaN(ms)) return "";
  if (ms <= 0) return "expired";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `expires in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `expires in ${hours} h` : `expires in ${Math.round(hours / 24)} days`;
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

type Row = { ok: boolean | null; title: string; detail: string };

function facilityRow(cluster: HpcCluster, check: HpcCheck): Row {
  // Lux is in no facility status feed; its check is whether the hub's SSH
  // server answers, and a failure there is never a reported outage.
  if (cluster === "lux") {
    return check.ok
      ? { ok: true, title: "Hub is reachable", detail: check.message }
      : { ok: null, title: "Couldn't reach the hub", detail: check.message };
  }
  if (check.ok) return { ok: true, title: "Facility is up", detail: check.message };
  if (check.reason === "degraded") {
    const incident = check.incident;
    const window = incident?.start
      ? ` · ${clock(incident.start)}${incident.end ? `–${clock(incident.end)}` : " onward"}`
      : "";
    return {
      ok: false,
      title: incident ? incident.name : "Facility degraded",
      detail: `${check.message}${window}`,
    };
  }
  return { ok: null, title: "Facility status unavailable", detail: check.message };
}

function credentialRow(cluster: HpcCluster, check: HpcCheck, now: number): Row {
  if (cluster === "lux") {
    // Nothing is stored to check: the row says how sign-in works.
    const parts = [check.project ? `Project ${check.project}` : null, check.message].filter(Boolean);
    return { ok: check.ok, title: "Sign in from a chat", detail: parts.join(" · ") };
  }
  const kind = cluster === "perlmutter" ? "NERSC IRI token" : "S3M token";
  if (check.ok) {
    // Only S3M carries an expiry; nothing is said about any other credential's.
    const parts = [
      check.project ? `Project ${check.project}` : null,
      check.expires_at ? expiresIn(check.expires_at, now) : null,
    ].filter(Boolean);
    return { ok: true, title: `${kind} accepted`, detail: parts.join(" · ") || check.message };
  }
  switch (check.reason) {
    case "not_connected":
      return { ok: false, title: `No ${kind} saved`, detail: "Add one in Settings to use this cluster." };
    case "not_active":
      return {
        ok: false,
        title: "Token not active yet",
        detail: check.active_from ? `Starts ${clock(check.active_from)}` : check.message,
      };
    case "rejected":
      return { ok: false, title: "Token rejected", detail: check.message };
    default:
      return { ok: null, title: "Couldn't verify the token", detail: check.message };
  }
}

function settingsRow(cluster: HpcCluster, check: HpcCheck): Row {
  if (check.ok) {
    // Lux's account is already on the sign-in row; Perlmutter's is shown here.
    const parts = [
      check.message,
      cluster === "perlmutter" && check.project ? `Account ${check.project}` : null,
    ].filter(Boolean);
    return { ok: true, title: "Remote directory set", detail: parts.join(" · ") };
  }
  return {
    ok: false,
    title: check.reason === "invalid" ? "A setting can't be used" : "Settings incomplete",
    detail: check.message,
  };
}

/**
 * Why a card isn't Ready, in one line: the message of each check that failed.
 * Shown when the card is hovered, so the reason reads without opening it.
 */
export function failureSummary(status: HpcClusterStatus | null): string | null {
  if (!status) return null;
  const { facility, credential, settings, globus } = status.checks;
  const failed = [facility, credential, settings, globus].filter(
    (c): c is HpcCheck => c != null && !c.ok,
  );
  return failed.length ? failed.map((c) => c.message).join(" ") : null;
}

function globusRow(check: HpcCheck): Row {
  if (check.ok) {
    return { ok: true, title: "Globus file transfer connected", detail: "Your own identity" };
  }
  switch (check.reason) {
    case "not_connected":
      return { ok: false, title: "Globus not connected", detail: "Outputs can't be fetched until you connect it." };
    case "session_expired":
      return { ok: false, title: "Globus session expired", detail: "Connect Globus again to reach this cluster's files." };
    default:
      return { ok: null, title: "Couldn't verify Globus", detail: check.message };
  }
}

function RowIcon({ ok }: { ok: boolean | null }) {
  if (ok === true) {
    return (
      <svg className="hpc-row-icon hpc-row-icon--ok" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-label="Passed" role="img">
        <polyline points="4,12 10,18 20,6" />
      </svg>
    );
  }
  if (ok === false) {
    return (
      <svg className="hpc-row-icon hpc-row-icon--fail" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-label="Failed" role="img">
        <line x1="6" y1="6" x2="18" y2="18" />
        <line x1="18" y1="6" x2="6" y2="18" />
      </svg>
    );
  }
  return (
    <svg className="hpc-row-icon hpc-row-icon--unknown" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-label="Unknown" role="img">
      <circle cx="12" cy="12" r="9" strokeDasharray="3 3" />
      <line x1="12" y1="11" x2="12" y2="16" />
      <line x1="12" y1="8" x2="12" y2="8.01" />
    </svg>
  );
}

type Open = { cluster: HpcCluster; left: number; top: number };

type Props = {
  collapsed: boolean;
  /** Clusters the researcher has chosen to show, in rail order. */
  visibleClusters: HpcCluster[];
  onOpenSettings: (cluster: HpcCluster) => void;
  /** The rail's collapsed-mode tooltip wiring, so these match its other icons. */
  tipProps: (label: string) => HTMLAttributes<HTMLElement>;
};

export function HpcStatusSection({ collapsed, visibleClusters, onOpenSettings, tipProps }: Props) {
  const view = useHpcStatus();
  const [open, setOpen] = useState<Open | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close on Escape or a click anywhere else, like the rail's other overlays.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(null);
    }
    function onDown(e: MouseEvent) {
      const target = e.target as Node;
      if (popoverRef.current?.contains(target)) return;
      if ((target as HTMLElement).closest?.("[data-hpc-card]")) return;
      setOpen(null);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  if (visibleClusters.length === 0) return null;

  // Before the first answer the visible clusters are known from settings but
  // their state is not: Checking, or Couldn't verify if the request failed.
  const byCluster = new Map(view.clusters?.map((c) => [c.cluster, c]) ?? []);
  const cards = visibleClusters.map((cluster) => {
    const entry = byCluster.get(cluster);
    const state: HpcDisplayState = entry
      ? entry.state
      : view.unavailable
        ? "unverifiable"
        : "checking";
    return { cluster, state, status: entry?.status ?? null, rechecking: entry?.rechecking ?? false };
  });

  const stale = view.failing && view.lastSuccessAt !== null;
  const anyRechecking = cards.some((c) => c.rechecking);

  function toggle(cluster: HpcCluster, el: HTMLElement) {
    if (open?.cluster === cluster) {
      setOpen(null);
      return;
    }
    const box = el.getBoundingClientRect();
    const rail = el.closest(".nav-rail")?.getBoundingClientRect();
    setOpen({
      cluster,
      left: (rail?.right ?? box.right) + 8,
      top: Math.max(8, Math.min(box.top, window.innerHeight - 400)),
    });
  }

  const openCard = open ? cards.find((c) => c.cluster === open.cluster) : undefined;

  return (
    <>
      {!collapsed ? (
        <div className="hpc-section-head">
          <span className="nav-rail-section-label hpc-section-label">HPC</span>
          {stale && view.lastSuccessAt !== null && (
            <span className="hpc-stale" title="VISTA couldn't recheck; showing the last result">
              checked {minutesAgo(view.lastSuccessAt, view.now)}
            </span>
          )}
          <button
            type="button"
            className="hpc-recheck-all"
            aria-label="Recheck all clusters"
            title="Recheck all clusters"
            disabled={anyRechecking}
            onClick={() => void view.recheck()}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true" className={anyRechecking ? "hpc-spin" : undefined}>
              <path d="M21 12a9 9 0 1 1-2.6-6.4" />
              <polyline points="21,3 21,9 15,9" />
            </svg>
          </button>
        </div>
      ) : (
        <div className="hpc-collapsed-divider" aria-hidden="true" />
      )}

      {cards.map(({ cluster, state, status, rechecking }) => {
        const title = HPC_CLUSTER_TITLES[cluster];
        const label = STATE_LABELS[state];
        const isOpen = open?.cluster === cluster;
        // Hovering says why a card isn't Ready: the collapsed rail through its
        // own tooltip, the expanded one through the browser's.
        const why = state === "ready" ? null : failureSummary(status);
        const hint = why ? `${title} · ${label}: ${why}` : `${title} · ${label}`;
        return (
          <button
            key={cluster}
            type="button"
            data-hpc-card={cluster}
            className={`hpc-card${collapsed ? " hpc-card--collapsed" : ""}${isOpen ? " open" : ""}`}
            aria-label={`${title}: ${label}`}
            aria-expanded={isOpen}
            aria-haspopup="dialog"
            onClick={(e) => toggle(cluster, e.currentTarget)}
            title={collapsed ? undefined : (why ?? undefined)}
            {...tipProps(hint)}
          >
            <HpcStatusDot state={state} />
            {collapsed ? (
              <span className="hpc-card-short" aria-hidden="true">{SHORT[cluster]}</span>
            ) : (
              <>
                <span className="hpc-card-text">
                  <span className="hpc-card-name">{title}</span>
                  <span className={`hpc-card-word${WORD_TONE[state] ? ` hpc-word--${WORD_TONE[state]}` : ""}`}>
                    {label}
                  </span>
                </span>
                {rechecking && <HpcStatusDot state="checking" />}
              </>
            )}
          </button>
        );
      })}

      {open && openCard && (
        <HpcDetails
          ref={popoverRef}
          cluster={open.cluster}
          state={openCard.state}
          status={openCard.status}
          rechecking={openCard.rechecking}
          left={open.left}
          top={open.top}
          lastSuccessAt={view.lastSuccessAt}
          failing={view.failing}
          now={view.now}
          onRecheck={() => void view.recheck(open.cluster)}
          onSettings={() => {
            setOpen(null);
            onOpenSettings(open.cluster);
          }}
        />
      )}
    </>
  );
}

type DetailsProps = {
  ref: React.Ref<HTMLDivElement>;
  cluster: HpcCluster;
  state: HpcDisplayState;
  status: HpcClusterStatus | null;
  rechecking: boolean;
  left: number;
  top: number;
  lastSuccessAt: number | null;
  failing: boolean;
  now: number;
  onRecheck: () => void;
  onSettings: () => void;
};

function HpcDetails({
  ref,
  cluster,
  state,
  status,
  rechecking,
  left,
  top,
  lastSuccessAt,
  failing,
  now,
  onRecheck,
  onSettings,
}: DetailsProps) {
  const title = HPC_CLUSTER_TITLES[cluster];
  const rows: Row[] = status
    ? [
        facilityRow(cluster, status.checks.facility),
        credentialRow(cluster, status.checks.credential, now),
        settingsRow(cluster, status.checks.settings),
        ...(status.checks.globus ? [globusRow(status.checks.globus)] : []),
      ]
    : [];
  const checked =
    lastSuccessAt === null
      ? failing
        ? "VISTA couldn't check"
        : "Checking…"
      : `Checked ${minutesAgo(lastSuccessAt, now)}${failing ? "; VISTA couldn't recheck" : ""}`;

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={`${title} connection details`}
      className="hpc-popover"
      style={{ left, top }}
    >
      <div className="hpc-popover-head">
        <div className="hpc-popover-title-row">
          <span className="hpc-popover-title">{title}</span>
          <span className={`hpc-pill hpc-pill--${state}`}>{STATE_LABELS[state]}</span>
        </div>
        <div className="hpc-popover-sub">{SUBTITLES[cluster]}</div>
      </div>
      {rows.length > 0 && (
        <ul className="hpc-rows">
          {rows.map((row) => (
            <li key={row.title} className="hpc-row">
              <RowIcon ok={row.ok} />
              <span className="hpc-row-text">
                <span className="hpc-row-title">{row.title}</span>
                <span className="hpc-row-detail">{row.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="hpc-popover-foot">
        <span className="hpc-popover-checked">{checked}</span>
        <button type="button" className="hpc-link" onClick={onSettings}>
          Settings
        </button>
        <button type="button" className="button ghost hpc-recheck" onClick={onRecheck} disabled={rechecking}>
          {rechecking ? "Checking…" : "Recheck"}
        </button>
      </div>
    </div>
  );
}
