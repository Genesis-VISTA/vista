/**
 * The one place a client call names VISTA's API (openspec change
 * `multi-user-hosted`, design D8).
 *
 * Next prefixes `<Link>` and its own assets with `basePath` but not a `fetch`,
 * an `EventSource` or a plain `href`, so every client path that starts `/api/`
 * goes through here. `basePath` is fixed at build time (`VISTA_BASE_PATH`, read
 * in `next.config.mjs`, which also exposes it as `NEXT_PUBLIC_BASE_PATH`), so
 * this is not the deployment flag, which is reported at runtime
 * (`lib/deployment.ts`). ESLint forbids a bare `/api/` literal in client code.
 * Server-side route handlers call the backend directly and do not use it.
 */
import { defaultUrlTransform } from "react-markdown";

export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** `path` (starting `/api/`) under the base path; unchanged when there is none. */
export function apiUrl(path: string): string {
  return `${BASE_PATH}${path}`;
}

/**
 * A URL from agent output or a document (a `display_file` link, a markdown link
 * or image): prefixed when it is a same-origin `/api/` path, left alone
 * otherwise (other origins, `//host`, `data:`, `#anchors`, paths outside the
 * API, and one already under the base path).
 */
export function withBasePath(url: string): string {
  if (!url.startsWith("/api/")) return url;
  return apiUrl(url);
}

/** A `ReactMarkdown` `urlTransform`: react-markdown's own sanitizing, then `withBasePath`. */
export function basePathUrlTransform(url: string): string {
  return withBasePath(defaultUrlTransform(url));
}
