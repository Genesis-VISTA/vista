"use client";

import { useSyncExternalStore } from "react";
import { HPC_CLUSTER_TITLES } from "@/lib/hpc-status";
import { extractError } from "@/lib/settings";

/**
 * Which VISTA this is and whether a newer release exists, from
 * `GET /api/version`, and the download and restart that install it
 * (openspec change `in-app-updates`).
 *
 * One store for the page, the same pattern as `lib/collaboration.ts`, so the
 * rail's settings marker and Settings > About always agree. The backend checks
 * GitHub on its own schedule; this only asks the backend what it found, when a
 * subscriber appears with a stale answer and every half hour while one stays.
 */

/** Backend `VersionInfo`. */
export type VersionInfo = {
  version: string;
  commit: string | null;
  /** `dev_build`: a checkout or local build, which never checks. */
  checks: "on" | "off" | "dev_build";
  checked_at: string | null;
  /** The backend has not finished its first check since it started, so `latest` may be about to change. */
  pending?: boolean;
  /** A newer release's version, when the last check found one. */
  latest: string | null;
  notes_url: string | null;
  /** Whether installing it will ask to reset local data; null when the release does not say. */
  data_reset: boolean | null;
};

/** Backend `DownloadStatusPublic`. */
export type DownloadStatus = {
  state: "idle" | "downloading" | "ready" | "failed";
  version: string | null;
  bytes: number;
  total: number;
  error: string | null;
};

/** Backend `Activity`: what a restart would interrupt. */
export type Activity = {
  chat_runs: Array<{
    chat_session_id: string;
    project_id: string;
    needs_you: boolean;
    title: string | null;
    project_name: string | null;
  }>;
  debates: number;
  watched_jobs: Array<{ job_id: string; cluster: string; job_name: string | null; state: string }>;
};

/** The window's update bridge (`electron/src/main-preload.cjs`); absent in a browser. */
export type VistaAppBridge = {
  isVistaApp: true;
  restartToUpdate(version: string): Promise<{ ok: boolean; reason?: string }>;
};

declare global {
  interface Window {
    vistaApp?: VistaAppBridge;
  }
}

/** The bridge, when the page is inside VISTA's own window. */
export function vistaApp(): VistaAppBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = window.vistaApp;
  return bridge?.isVistaApp === true ? bridge : null;
}

type Snapshot = {
  info: VersionInfo | null;
  error: string | null;
  checking: boolean;
  checkedAt: number | null;
  download: DownloadStatus | null;
};

/** A new subscriber re-asks once the answer is older than this. */
export const STALE_MS = 5 * 60_000;
/** How often the answer is refreshed while anything shows it. */
export const REFRESH_MS = 30 * 60_000;
const POLL_MS = 1000;
/** How soon to ask again while the backend's first check is still out (`pending`). */
export const PENDING_MS = 3000;

const initial = (): Snapshot => ({
  info: null,
  error: null,
  checking: false,
  checkedAt: null,
  download: null,
});

let snapshot: Snapshot = initial();
let request = 0;
let timer: ReturnType<typeof setInterval> | null = null;
let polling: ReturnType<typeof setTimeout> | null = null;
let soon: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function set(update: Partial<Snapshot>): void {
  snapshot = { ...snapshot, ...update };
  listeners.forEach((cb) => cb());
}

async function ask(path: string, init?: RequestInit): Promise<VersionInfo> {
  const res = await fetch(path, {
    headers: { accept: "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as VersionInfo;
}

async function load(path: string, init?: RequestInit): Promise<void> {
  const id = ++request;
  set({ checking: true });
  try {
    const info = await ask(path, init);
    if (id !== request) return;
    set({ info, error: null, checking: false, checkedAt: Date.now() });
    // The page can load before the backend's startup check lands: ask again soon.
    if (info.pending && soon === null) {
      soon = setTimeout(() => {
        soon = null;
        void refreshVersion();
      }, PENDING_MS);
    }
  } catch (e) {
    if (id !== request) return;
    set({ error: e instanceof Error ? e.message : "request failed", checking: false, checkedAt: Date.now() });
  }
}

/** Ask the backend again what it knows. Every subscriber follows. */
export function refreshVersion(): Promise<void> {
  return load("/api/version");
}

/** About's "Check now": the backend asks GitHub, then answers as `refreshVersion`. */
export function checkNow(): Promise<void> {
  return load("/api/updates/check", { method: "POST" });
}

async function pollDownload(): Promise<void> {
  polling = null;
  try {
    const res = await fetch("/api/updates/download", { headers: { accept: "application/json" }, cache: "no-store" });
    if (!res.ok) throw new Error(await extractError(res));
    const download = (await res.json()) as DownloadStatus;
    set({ download });
    if (download.state === "downloading") polling = setTimeout(() => void pollDownload(), POLL_MS);
  } catch (e) {
    const message = e instanceof Error ? e.message : "request failed";
    set({ download: { ...(snapshot.download ?? emptyDownload()), state: "failed", error: message } });
  }
}

function emptyDownload(): DownloadStatus {
  return { state: "idle", version: null, bytes: 0, total: 0, error: null };
}

/** Start downloading `version` (the release on offer), then follow it until it is ready or fails. */
export async function startDownload(version: string): Promise<void> {
  if (polling) clearTimeout(polling);
  set({ download: { ...emptyDownload(), state: "downloading", version } });
  try {
    const res = await fetch("/api/updates/download", {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify({ version }),
    });
    if (!res.ok) throw new Error(await extractError(res));
    set({ download: (await res.json()) as DownloadStatus });
  } catch (e) {
    set({
      download: { ...emptyDownload(), version, state: "failed", error: e instanceof Error ? e.message : "request failed" },
    });
    return;
  }
  await pollDownload();
}

/** What restarting now would interrupt. */
export async function fetchActivity(): Promise<Activity> {
  const res = await fetch("/api/activity", { headers: { accept: "application/json" }, cache: "no-store" });
  if (!res.ok) throw new Error(await extractError(res));
  return (await res.json()) as Activity;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const stale = snapshot.checkedAt === null || Date.now() - snapshot.checkedAt >= STALE_MS;
  if (!snapshot.checking && stale) void refreshVersion();
  if (timer === null) timer = setInterval(() => void refreshVersion(), REFRESH_MS);
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

const getSnapshot = () => snapshot;
const SERVER_SNAPSHOT: Snapshot = initial();
const getServerSnapshot = () => SERVER_SNAPSHOT;

export function useVersion(): Snapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** For tests: forget everything, as a fresh page load would. */
export function resetVersionForTests(): void {
  request++;
  if (timer !== null) clearInterval(timer);
  if (polling !== null) clearTimeout(polling);
  if (soon !== null) clearTimeout(soon);
  timer = null;
  polling = null;
  soon = null;
  snapshot = initial();
}

// ---------------------------------------------------------------------------
// Pure helpers, for the views and their tests
// ---------------------------------------------------------------------------

/** Whether the settings button and About's entry carry the update marker. */
export function updateAvailable(info: VersionInfo | null): boolean {
  return Boolean(info && info.checks === "on" && info.latest);
}

/** About's line on checking, when there is no update to show. */
export function checksSentence(info: VersionInfo, now: number = Date.now()): string {
  if (info.checks === "dev_build") return "Update checks are off for development builds.";
  if (info.checks === "off") return "Update checks are off.";
  if (!info.checked_at) return "Not checked yet.";
  return `VISTA is up to date. Checked ${relativeTime(info.checked_at, now)}.`;
}

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago". */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "at an unknown time";
  const minutes = Math.max(0, Math.round((now - then) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** The data-reset warning for the confirmation, exact when the release says and generic when not. */
export function dataResetSentence(dataReset: boolean | null): string | null {
  if (dataReset === true) {
    return "This update changes how VISTA stores its data: when it opens, it will ask you to back up and reset your local data.";
  }
  if (dataReset === null) {
    return "If this update changes how VISTA stores its data, it will ask you to back up and reset your local data when it opens.";
  }
  return null;
}

/** One line per thing a restart interrupts, for the confirmation's list. */
export function activityLines(activity: Activity): string[] {
  const lines: string[] = [];
  for (const run of activity.chat_runs) {
    const name = run.title ?? "A conversation";
    const where = run.project_name ? ` in ${run.project_name}` : "";
    lines.push(
      run.needs_you
        ? `${name}${where} is waiting for your answer; its response will stop.`
        : `${name}${where} is responding; its response will stop.`,
    );
  }
  if (activity.debates > 0) {
    lines.push(
      activity.debates === 1
        ? "A Hypothesis Lab debate is running; it will stop."
        : `${activity.debates} Hypothesis Lab debates are running; they will stop.`,
    );
  }
  for (const job of activity.watched_jobs) {
    const name = job.job_name ? `${job.job_name} (${job.job_id})` : `Job ${job.job_id}`;
    lines.push(
      `${name} on ${clusterTitle(job.cluster)} keeps running; VISTA watches it again once it reopens.`,
    );
  }
  return lines;
}

function clusterTitle(cluster: string): string {
  return (HPC_CLUSTER_TITLES as Record<string, string>)[cluster] ?? cluster;
}

/** "312 MB of 1.7 GB". */
export function progressText(download: DownloadStatus): string {
  const mb = (n: number) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`);
  return download.total > 0 ? `${mb(download.bytes)} of ${mb(download.total)}` : mb(download.bytes);
}
