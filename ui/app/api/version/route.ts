import { relay } from "../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /version`: the installed version and commit,
 * whether this install checks for updates, and what the last check found.
 * Never asks GitHub itself; the backend checks on its own schedule.
 */
export async function GET() {
  return relay("/version");
}
