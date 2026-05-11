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

export function getBackendBaseUrl(): string {
  return (process.env.VISTA_BACKEND_URL || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

export function getMcpBaseUrl(): string {
  return process.env.VISTA_MCP_URL || process.env.MCP_BASE_URL || "http://127.0.0.1:8000/mcp";
}

export function backendUrl(path: string): string {
  return `${getBackendBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
}
