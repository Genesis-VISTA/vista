import { relay } from "../../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /collaboration/status`: whether publishing and
 * importing can work, and why not when they cannot. The backend asks the
 * collaboration server each time, within a few seconds.
 */
export async function GET() {
  return relay("/collaboration/status");
}
