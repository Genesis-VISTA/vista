import { proxyReportPost } from "../_proxy";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `POST /projects/{name}/reports/generate`.
 *
 * The client posts the active chat's `message_history` (raw PydanticAI
 * `ModelMessage` objects) plus an optional focus `hint`; the backend returns
 * `{ title, slug_suggestion, summary, body }` and persists nothing.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  const { name } = await params;
  return proxyReportPost(request, name, "/generate");
}
