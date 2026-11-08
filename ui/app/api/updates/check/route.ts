import { relay } from "../../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /updates/check`: About's "Check now". Answers
 * as `GET /version`; does nothing while checks are off or on a dev build.
 */
export async function POST() {
  return relay("/updates/check", { method: "POST" });
}
