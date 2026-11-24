import { Agent, fetch } from "undici";
import { backendUrl, identityHeaders } from "../_backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The catch-all proxy: `/api/<path>` is the backend's `/<path>`, with method,
 * query, body and the researcher's identity (`Authorization: Bearer`, from
 * `X-Access-Token` or an incoming `Authorization`) forwarded, and the answer
 * streamed back as it arrives, so SSE is never buffered. Routes with logic of
 * their own (validation, reshaping, another backend path) are specific route
 * files, which Next.js matches ahead of this one, and use `backendHeaders` for
 * the same identity.
 */

/** Request headers worth passing on; the rest (cookies, host, …) stay here. */
const PASSED = ["content-type", "accept", "last-event-id", "range", "if-none-match"];

/** Never drop a slow answer or an idle event stream: only the backend ends them. */
const PATIENT = new Agent({ headersTimeout: 0, bodyTimeout: 0 });

/** Response headers undici has already decoded or that describe the hop, not the body. */
const DROPPED = new Set(["connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length"]);

function bad(detail: string, status: number) {
  return Response.json({ detail }, { status });
}

async function forward(request: Request, { params }: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await params;
  if (path.some((part) => !part || part === "." || part === "..")) return bad("Bad path.", 400);

  const headers: Record<string, string> = identityHeaders(request.headers);
  for (const name of PASSED) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }

  const hasBody = request.method !== "GET" && request.method !== "HEAD" && request.body !== null;
  let upstream;
  try {
    upstream = await fetch(backendUrl(`/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`), {
      method: request.method,
      headers,
      ...(hasBody ? { body: request.body as never, duplex: "half" as const } : {}),
      redirect: "manual",
      dispatcher: PATIENT,
      signal: request.signal,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return bad(`Backend unreachable: ${message}`, 502);
  }

  const out = new Headers();
  upstream.headers.forEach((value, name) => {
    if (!DROPPED.has(name)) out.append(name, value);
  });
  if ((upstream.headers.get("content-type") ?? "").includes("text/event-stream")) {
    out.set("cache-control", "no-cache");
    out.set("x-accel-buffering", "no");
  }
  const noBody = upstream.status === 204 || upstream.status === 205 || upstream.status === 304;
  return new Response(noBody || request.method === "HEAD" ? null : (upstream.body as ReadableStream | null), {
    status: upstream.status,
    headers: out,
  });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE, forward as HEAD };
