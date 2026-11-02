import { NextResponse } from "next/server";
import { Agent, fetch } from "undici";
import { backendUrl, backendHeaders } from "./_backend";

/**
 * The one way these proxy routes send a request to the backend: `relay`
 * passes its answer through as it is, and `postBackend`
 * (`skills/import/_imported.ts`) reads it for a route that reshapes it.
 */

/** A team id as the backend takes it; anything else never reaches it. */
export const TEAM_ID = /^[0-9a-fA-F-]{36}$/;

/**
 * For a request that answers only when its work is done (a knowledge base's
 * publish, import or copy moves all of it first): undici's default header
 * timeout would give up on it.
 */
const LONG_WAIT = new Agent({ headersTimeout: 60 * 60 * 1000 });

export type Sent =
  | { ok: true; status: number; contentType: string | null; text: string }
  | { ok: false; reason: string };

/** Send one request to the backend: its status and body, or why it could not be reached. */
export async function sendBackend(
  path: string,
  { method = "GET", body, longWait = false }: { method?: string; body?: string; longWait?: boolean } = {},
): Promise<Sent> {
  try {
    const upstream = await fetch(backendUrl(path), {
      method,
      body,
      ...(longWait ? { dispatcher: LONG_WAIT } : {}),
      headers: await backendHeaders({
        accept: "application/json",
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
      }),
    });
    return {
      ok: true,
      status: upstream.status,
      contentType: upstream.headers.get("content-type"),
      text: await upstream.text(),
    };
  } catch (error) {
    return { ok: false, reason: error instanceof Error ? error.message : "Unknown error" };
  }
}

/**
 * Send one request to the backend and pass its answer through: status, body
 * and all, so a refusal keeps its `detail` and `code`. A 204 is passed on
 * without a body, which a 204 cannot carry.
 */
export async function relay(
  path: string,
  init: { method?: string; body?: string; longWait?: boolean } = {},
): Promise<NextResponse> {
  const sent = await sendBackend(path, init);
  if (!sent.ok) {
    return NextResponse.json({ detail: `Backend unreachable: ${sent.reason}` }, { status: 502 });
  }
  if (sent.status === 204) return new NextResponse(null, { status: 204 });
  return new NextResponse(sent.text, {
    status: sent.status,
    headers: { "content-type": sent.contentType ?? "application/json" },
  });
}

/** The request's JSON body, or a 400 to answer with. */
export async function jsonBody(request: Request): Promise<string | NextResponse> {
  try {
    return JSON.stringify(await request.json());
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
}
