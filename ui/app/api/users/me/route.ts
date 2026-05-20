import { NextResponse } from "next/server";
import { backendUrl } from "../../_backend";

export const runtime = "nodejs";

/**
 * Proxy for the backend's `GET/PUT /users/me`.
 *
 * GET returns the authenticated user (currently resolved by a dev SSO
 * placeholder in the backend). Pass `?config=true` to include the per-user
 * config + decrypted tokens — used by the settings modal; the nav rail
 * fetches the light view so secrets aren't decrypted on every page load.
 *
 * PUT accepts a `UserSelfUpdate` body and returns the updated
 * `UserPublicWithConfig` so the modal can update its draft without a
 * follow-up GET.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const config = searchParams.get("config") === "true";
  const upstreamPath = config ? "/users/me?config=true" : "/users/me";

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(upstreamPath), {
      headers: { accept: "application/json" },
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
    upstream = await fetch(backendUrl("/users/me"), {
      method: "PUT",
      headers: { "content-type": "application/json" },
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
