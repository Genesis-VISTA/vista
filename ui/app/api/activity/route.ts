import { relay } from "../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /activity`: agent responses, debates and
 * watched HPC jobs a restart would interrupt, for the update's confirmation.
 */
export async function GET() {
  return relay("/activity");
}
