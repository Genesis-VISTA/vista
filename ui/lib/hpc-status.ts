"use client";

import { useSyncExternalStore } from "react";

/**
 * Live HPC availability for the NavRail's cards and the settings modal's
 * cluster headers, from `GET /api/users/me/hpc-status`.
 *
 * One store for the whole page, so the rail and the modal always show the
 * same answer and a recheck from either updates both. It fetches when the
 * first component subscribes, then every `POLL_MS` while the tab is visible.
 * A failed fetch keeps the last result -- a backend restarting should not make
 * every card flicker -- until that result is `STALE_MS` old, after which every
 * card reads Couldn't verify: a result nobody can refresh must not stay green.
 */

export type HpcCluster = "frontier" | "odo" | "perlmutter" | "lux";

export const HPC_CLUSTERS: readonly HpcCluster[] = ["frontier", "odo", "perlmutter", "lux"];

export const HPC_CLUSTER_TITLES: Record<HpcCluster, string> = {
  frontier: "Frontier",
  odo: "Odo",
  perlmutter: "Perlmutter",
  lux: "Lux",
};

/** The backend's per-cluster state. */
export type HpcState =
  | "degraded"
  | "unverifiable"
  | "not_connected"
  | "rejected"
  | "wrong_project"
  | "globus_not_connected"
  | "globus_session_expired"
  | "ready";

/** What a card shows: the backend's state, or "checking" before the first answer. */
export type HpcDisplayState = HpcState | "checking";

export type HpcCheckReason =
  | "degraded"
  | "unreachable"
  | "unverifiable"
  | "not_connected"
  | "rejected"
  | "not_active"
  | "wrong_project"
  | "session_expired";

export type HpcCheck = {
  ok: boolean;
  reason: HpcCheckReason | null;
  message: string;
  http_status?: number | null;
  incident?: { name: string; start: string | null; end: string | null } | null;
  project?: string | null;
  expected_project?: string | null;
  expires_at?: string | null;
  active_from?: string | null;
  identity?: "own" | "deployment" | null;
  /** The host a Lux facility check probed. */
  host?: string | null;
};

export type HpcClusterStatus = {
  cluster: HpcCluster;
  state: HpcState;
  checked_at: string;
  checks: {
    facility: HpcCheck;
    credential: HpcCheck;
    globus: HpcCheck | null;
  };
};

export const POLL_MS = 5 * 60_000;
export const STALE_MS = 15 * 60_000;
const TICK_MS = 30_000;

type Snapshot = {
  /** The last successful answer, or null before one arrives. */
  clusters: HpcClusterStatus[] | null;
  /** Client clock, ms, of the last successful answer. */
  lastSuccessAt: number | null;
  /** The most recent attempt failed. */
  failing: boolean;
  /** A request is in flight: every cluster, or the one being rechecked. */
  inFlight: HpcCluster | "all" | null;
  /** Advanced by the ticker so "checked N min ago" and staleness re-render. */
  now: number;
};

const initial = (): Snapshot => ({
  clusters: null,
  lastSuccessAt: null,
  failing: false,
  inFlight: null,
  now: Date.now(),
});

let snapshot: Snapshot = initial();
let lastAttemptAt = 0;
let request = 0;
const listeners = new Set<() => void>();
let ticker: ReturnType<typeof setInterval> | null = null;

function set(update: Partial<Snapshot>): void {
  snapshot = { ...snapshot, ...update };
  listeners.forEach((cb) => cb());
}

async function load(opts: { fresh?: boolean; cluster?: HpcCluster } = {}): Promise<void> {
  const id = ++request;
  lastAttemptAt = Date.now();
  const params = new URLSearchParams();
  if (opts.fresh) params.set("fresh", "true");
  if (opts.cluster) params.set("cluster", opts.cluster);
  const query = params.toString();
  set({ inFlight: opts.cluster ?? "all" });
  try {
    const res = await fetch(`/api/users/me/hpc-status${query ? `?${query}` : ""}`, {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`HPC status request failed (${res.status})`);
    const body = (await res.json()) as { clusters: HpcClusterStatus[] };
    // A slower, older request must not overwrite a newer answer.
    if (id !== request) return;
    const now = Date.now();
    // The backend answers for every visible cluster even when only one was
    // rechecked, so the answer replaces the whole list.
    set({ clusters: body.clusters, lastSuccessAt: now, failing: false, inFlight: null, now });
  } catch {
    if (id !== request) return;
    set({ failing: true, inFlight: null, now: Date.now() });
  }
}

function tick(): void {
  const now = Date.now();
  const visible = typeof document === "undefined" || document.visibilityState === "visible";
  if (visible && snapshot.inFlight === null && now - lastAttemptAt >= POLL_MS) {
    void load();
  } else {
    set({ now });
  }
}

function onVisibility(): void {
  if (document.visibilityState === "visible") tick();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (listeners.size === 1) {
    ticker = setInterval(tick, TICK_MS);
    document.addEventListener("visibilitychange", onVisibility);
    if (snapshot.inFlight === null && Date.now() - lastAttemptAt >= POLL_MS) void load();
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0) {
      if (ticker) clearInterval(ticker);
      ticker = null;
      document.removeEventListener("visibilitychange", onVisibility);
    }
  };
}

const getSnapshot = () => snapshot;
const getServerSnapshot = () => SERVER_SNAPSHOT;
const SERVER_SNAPSHOT: Snapshot = { ...initial(), now: 0 };

/** Rerun the checks now: every cluster, or just `cluster`. */
export function recheckHpcStatus(cluster?: HpcCluster): Promise<void> {
  return load({ fresh: true, cluster });
}

/**
 * Fetch again without forcing the checks, e.g. after the visible clusters
 * changed: the backend's own cache still spares the facilities.
 */
export function refreshHpcStatus(): Promise<void> {
  return load();
}

/** For tests: forget everything, as a fresh page load would. */
export function resetHpcStatusForTests(): void {
  request++;
  snapshot = initial();
  lastAttemptAt = 0;
}

export type HpcStatusView = {
  /** Null until the first answer; the rail shows its visible clusters as Checking. */
  clusters: Array<{
    cluster: HpcCluster;
    state: HpcDisplayState;
    status: HpcClusterStatus;
    rechecking: boolean;
  }> | null;
  /** Client-clock ms of the last successful answer. */
  lastSuccessAt: number | null;
  /** The last request failed; what is shown is older than it looks. */
  failing: boolean;
  /** No answer has ever arrived and the last attempt failed. */
  unavailable: boolean;
  now: number;
  recheck: (cluster?: HpcCluster) => Promise<void>;
};

/** Derive what the UI shows from the raw store. Pure, for testing. */
export function viewOf(s: Snapshot): Omit<HpcStatusView, "recheck"> {
  const expired =
    s.failing && s.lastSuccessAt !== null && s.now - s.lastSuccessAt > STALE_MS;
  return {
    clusters:
      s.clusters?.map((status) => ({
        cluster: status.cluster,
        state: expired ? "unverifiable" : status.state,
        status,
        rechecking: s.inFlight === "all" || s.inFlight === status.cluster,
      })) ?? null,
    lastSuccessAt: s.lastSuccessAt,
    failing: s.failing,
    unavailable: s.clusters === null && s.failing,
    now: s.now,
  };
}

export function useHpcStatus(): HpcStatusView {
  const s = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return { ...viewOf(s), recheck: recheckHpcStatus };
}
