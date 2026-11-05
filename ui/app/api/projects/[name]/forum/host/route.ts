import { relay } from "../../../../_relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxy for the backend's `POST /projects/{name}/forum/host`: move the
 * project's forum onto the collaboration server. It answers when the move is
 * done (every thread is pushed first), so it waits as long as that takes. A
 * success is the project; a refusal passes through with its status and `detail`.
 */
export async function POST(
  _request: Request,
  { params }: { params: Promise<{ name: string }> },
) {
  const { name } = await params;
  return relay(`/projects/${encodeURIComponent(name)}/forum/host`, {
    method: "POST",
    longWait: true,
  });
}
