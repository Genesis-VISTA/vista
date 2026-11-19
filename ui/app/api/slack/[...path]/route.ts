import { NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "../../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Proxy for the backend's `/slack/*`: the settings, take-over, and the
 * researcher's Slack links. Passed through unchanged. The Slack tokens go
 * through here only on their way to the secret store; no response carries one.
 */
async function forward(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await params;
  if (path.some((part) => !part || part === "." || part === "..")) {
    return NextResponse.json({ error: "Bad path." }, { status: 400 });
  }
  const method = request.method;
  const body = method === "GET" || method === "DELETE" ? undefined : await request.text();
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/slack/${path.map(encodeURIComponent).join("/")}`), {
      method,
      headers: await backendHeaders({
        accept: "application/json",
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
      }),
      body,
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: `Backend unreachable: ${message}` }, { status: 502 });
  }
  if (upstream.status === 204) return new NextResponse(null, { status: 204 });
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

export { forward as GET, forward as PUT, forward as POST, forward as DELETE };
