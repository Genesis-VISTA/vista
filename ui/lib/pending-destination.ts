/**
 * Where the user was headed when they were sent to pick a project.
 *
 * Chat is the only route that redirects: it has nothing to show without a
 * project. Sending someone to the picker and then dropping them somewhere else
 * loses their intent, so the route they asked for is parked here and read back
 * once they choose.
 *
 * sessionStorage, not localStorage: this is one interrupted journey, and it
 * should not survive into a new tab or tomorrow's session.
 */
const KEY = "vista.pendingDestination.v1";

/** Where to land when nothing was parked — the project picker sends you to chat. */
export const DEFAULT_DESTINATION = "/";

export function writePendingDestination(path: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(KEY, path);
  } catch {
    // Private mode or blocked storage: the default destination still applies.
  }
}

/** Read and clear in one step, so a stale destination cannot fire twice. */
export function takePendingDestination(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.sessionStorage.getItem(KEY);
    window.sessionStorage.removeItem(KEY);
    // Only same-origin paths. Anything else is either corrupt or hostile.
    return value && value.startsWith("/") && !value.startsWith("//") ? value : null;
  } catch {
    return null;
  }
}
