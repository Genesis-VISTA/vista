// Server-only helper: resolves the FastAPI backend base URL.
// Imported by server actions and route handlers; the value never reaches the
// browser. Strips trailing slashes so callers can write `${backendUrl}/path`.

import "server-only";

export const backendUrl = (
  process.env.VISTA_BACKEND_URL || "http://127.0.0.1:8001"
).replace(/\/+$/, "");
