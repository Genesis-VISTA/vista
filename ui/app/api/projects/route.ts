import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET /projects` (list) and `POST /projects` (create).
 *
 * The backend owns the project DB; this route is a thin pass-through. On a list
 * failure we degrade gracefully with `[]` + 200 (matching `skills/route.ts`) so
 * the projects page still renders.
 */
export async function GET() {
  try {
    const upstream = await fetch(backendUrl("/projects"), {
      headers: await backendHeaders({ accept: "application/json" }),
    });
    if (!upstream.ok) {
      return NextResponse.json([], { status: 200 });
    }
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/projects"), {
      method: "POST",
      headers: await backendHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }

  // Pass the upstream body (created project, or a 422 validation error) and
  // status straight through.
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
