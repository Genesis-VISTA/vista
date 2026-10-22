import { NextResponse } from "next/server";
import { backendUrl, backendHeaders } from "../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET/PUT /settings`: the installation's settings,
 * including decrypted credentials.
 *
 * PUT accepts a `LocalSettingsUpdate` body and returns the updated
 * `LocalSettingsPublic` so the modal can update its draft without a
 * follow-up GET.
 */
export async function GET() {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/settings"), {
      headers: await backendHeaders({ accept: "application/json" }),
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { error: `Backend unreachable: ${message}` },
      { status: 502 }
    );
  }
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}

export async function PUT(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl("/settings"), {
      method: "PUT",
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

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
