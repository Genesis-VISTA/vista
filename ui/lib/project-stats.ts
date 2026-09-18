"use client";

import { useEffect, useState } from "react";

export type ProjectStats = {
  conversations: number;
  datasets: number;
  /** ISO timestamp of the most recent conversation update, or null. */
  lastActive: string | null;
};

/**
 * Counts for a project card.
 *
 * Deliberately a fan-out: three requests per card, because `ProjectPublic`
 * carries none of these and every one of them is already derivable from a
 * route the app calls elsewhere. That is fine at a handful of projects and bad
 * at fifty, and it degrades suddenly rather than gradually — the page stays
 * responsive until the browser's connection limit, then everything queues.
 * The fix is rollup counts on the projects endpoint; until then this is one
 * request per count per card, and it is worth knowing that.
 */
export function useProjectStats(projectName: string): ProjectStats | null {
  const [stats, setStats] = useState<ProjectStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    const query = `?project_name=${encodeURIComponent(projectName)}`;

    async function countOf(path: string): Promise<unknown[]> {
      const res = await fetch(path, { headers: { accept: "application/json" } });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data) ? data : [];
    }

    void (async () => {
      try {
        const [sessions, uploads, outputs] = await Promise.all([
          countOf(`/api/chat/sessions${query}`),
          countOf(`/api/files/uploads${query}`),
          countOf(`/api/files/outputs${query}`),
        ]);
        if (cancelled) return;
        const updatedAt = sessions
          .map((s) => (s as { updated_at?: unknown }).updated_at)
          .filter((value): value is string => typeof value === "string")
          .sort();
        setStats({
          conversations: sessions.length,
          datasets: uploads.length + outputs.length,
          lastActive: updatedAt.length > 0 ? updatedAt[updatedAt.length - 1] : null,
        });
      } catch {
        // A card without counts is better than a card that fails to render.
        if (!cancelled) setStats(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [projectName]);

  return stats;
}

/** "4m ago", "3d ago". Coarse on purpose; the exact minute is not the point. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.round(months / 12)}y ago`;
}
