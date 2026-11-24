/**
 * Shared helper for the Next.js Backend-for-Frontend proxy routes.
 *
 * The Python backend (vista-backend, FastAPI + PydanticAI) owns the real
 * API surface; these Next.js routes are thin wrappers that:
 *   - resolve the backend base URL,
 *   - forward requests,
 *   - reshape responses to match what the unmodified frontend expects.
 */

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8001";

import { headers } from "next/headers";

/**
 * The researcher's access token on a request: `X-Access-Token` (what Obsidian's
 * gateway sets after signing them in), else an incoming `Authorization: Bearer`.
 * Null on the desktop, where nothing signs anyone in. Never log the result.
 */
export function accessToken(incoming: Headers): string | null {
  const direct = incoming.get("x-access-token")?.trim();
  if (direct) return direct;
  const match = /^Bearer\s+(\S+)\s*$/i.exec(incoming.get("authorization") ?? "");
  return match ? match[1] : null;
}

/** `Authorization: Bearer <token>` for the backend, or nothing without a token. */
export function identityHeaders(incoming: Headers): Record<string, string> {
  const token = accessToken(incoming);
  return token ? { authorization: `Bearer ${token}` } : {};
}

/**
 * The headers for an upstream backend request: the researcher's identity from
 * the current request, merged with any per-call extras (e.g. `content-type`).
 * Reads the incoming request via `next/headers`, so it works in every route
 * handler without threading the `Request` through.
 */
export async function backendHeaders(
  extra: Record<string, string> = {}
): Promise<Record<string, string>> {
  return { ...identityHeaders(await headers()), ...extra };
}

export function getBackendBaseUrl(): string {
  return (process.env.VISTA_BACKEND_URL || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

export function getMcpBaseUrl(): string {
  return process.env.VISTA_MCP_URL || process.env.MCP_BASE_URL || "http://127.0.0.1:8000/mcp";
}

export function backendUrl(path: string): string {
  return `${getBackendBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
}
