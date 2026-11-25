"use client";

import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api-url";

/** Who is signed in, as their sign-in names them (`GET /api/me`). */
export type Me = { displayName: string | null; email: string | null };

/**
 * The signed-in user, asked for only when `enabled` (a hosted deployment:
 * desktop's one user has no sign-in to name). `null` until it answers, or if
 * it cannot be read.
 */
export function useMe(enabled: boolean): Me | null {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    fetch(apiUrl("/api/me"), { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { display_name: string | null; email: string | null } | null) => {
        if (live && body) setMe({ displayName: body.display_name, email: body.email });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [enabled]);
  return enabled ? me : null;
}
