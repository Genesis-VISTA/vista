import { relay } from "../../_relay";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /collaboration/users?q=`: everyone who has
 * signed in to the collaboration server whose name or email contains `q`, who
 * can be added to a team. Only `q` is passed on.
 */
export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q");
  return relay(`/collaboration/users${q ? `?q=${encodeURIComponent(q)}` : ""}`);
}
