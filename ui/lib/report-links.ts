import { defaultUrlTransform } from "react-markdown";

/**
 * Sandbox paths a report may reference, and the file kind each is served as.
 * A saved report keeps these paths so a later agent can open the files; only
 * the browser needs them rewritten.
 */
const SANDBOX_PREFIXES: ReadonlyArray<[prefix: string, kind: "outputs" | "uploads"]> = [
  ["/mnt/data/output/", "outputs"],
  ["/mnt/data/uploads/", "uploads"],
];

/**
 * The UI file route for a sandbox path such as `/mnt/data/output/plots/a.png`
 * (also accepted as a `file://` URL), or `null` if `url` is not one. Each path
 * segment is encoded; a `.` or `..` segment is refused rather than resolved.
 */
export function sandboxFileUrl(url: string, projectName: string): string | null {
  const path = url.startsWith("file://") ? url.slice("file://".length) : url;
  for (const [prefix, kind] of SANDBOX_PREFIXES) {
    if (!path.startsWith(prefix)) continue;
    const segments = path.slice(prefix.length).split("/");
    if (segments.some((s) => !s || s === "." || s === "..")) return null;
    const encoded = segments.map((s) => encodeURIComponent(safeDecode(s))).join("/");
    return `/api/files/${kind}/${encoded}?project_name=${encodeURIComponent(projectName)}`;
  }
  return null;
}

/**
 * A `ReactMarkdown` `urlTransform` that serves sandbox paths through the
 * project's file routes and leaves every other URL to react-markdown's
 * default sanitizer.
 */
export function reportUrlTransform(projectName: string): (url: string) => string {
  return (url) => sandboxFileUrl(url, projectName) ?? defaultUrlTransform(url);
}

/** Markdown may already percent-encode a path (spaces as %20); don't double-encode. */
function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
