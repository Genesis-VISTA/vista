/**
 * Shared helper for the Next.js Backend-for-Frontend proxy routes.
 *
 * The Python backend (vista-backend, FastAPI + PydanticAI) owns the real
 * API surface; these Next.js routes are thin wrappers that:
 *   - resolve the backend base URL,
 *   - forward requests,
 *   - reshape responses to match what the unmodified frontend expects.
 */

import { headers } from "next/headers";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8001";

/**
 * Headers the ALB injects after authenticating the user via OIDC. In the
 * Backend-for-Frontend pattern the browser never talks to the Python backend
 * directly — it hits these Next.js routes, which must relay the ALB's signed
 * identity headers so the backend can cryptographically verify the user
 * (see `backend/.../api/auth.py`). `x-amzn-oidc-data` is the signed claims JWT.
 */
const FORWARDED_AUTH_HEADERS = [
  "x-amzn-oidc-data",
  "x-amzn-oidc-accesstoken",
  "x-amzn-oidc-identity",
];

/**
 * Build the headers for an upstream backend request: the ALB auth headers from
 * the current request, merged with any per-call extras (e.g. `content-type`).
 * Reads the incoming request via `next/headers`, so it works in every route
 * handler without threading the `Request` through.
 */
export async function backendHeaders(
  extra: Record<string, string> = {}
): Promise<Record<string, string>> {
  const incoming = await headers();
  const forwarded: Record<string, string> = {};
  for (const name of FORWARDED_AUTH_HEADERS) {
    const value = incoming.get(name);
    if (value) forwarded[name] = value;
  }
  return { ...forwarded, ...extra };
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
