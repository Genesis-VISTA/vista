import type { ShownIn } from "./files-tree";
import { extractError } from "./settings";

/**
 * A project's linked folders (openspec change linked-folders): folders on this
 * computer the agent reads, read-only, at `/mnt/linked/<name>`.
 */

/** Shown wherever a folder is linked. */
export const LINK_WARNING =
  "The agent can read every file in this folder. If any files contain credentials, it may see them, be careful!";

export type LinkedFolder = {
  name: string;
  host_path: string;
  sandbox_path: string;
  available: boolean;
  /** Why a sandbox starting now could not mount it. */
  reason: "missing" | "not a folder" | "unreadable" | null;
};

/** One entry of a linked folder's directory. */
export type LinkedEntry = {
  name: string;
  /** Within the link: what the next listing, and the file's `linked` path, use. */
  path: string;
  type: "file" | "folder";
  size: number | null;
  modified: string;
  shown_in?: ShownIn | null;
};

/** A refusal from the backend: the sentence to show, and the `code` it gave. */
export class LinkError extends Error {
  constructor(
    message: string,
    readonly code: string | null,
  ) {
    super(message);
  }
}

function base(project: string): string {
  return `/api/projects/${encodeURIComponent(project)}/linked-folders`;
}

async function refused(res: Response): Promise<LinkError> {
  const copy = res.clone();
  let code: string | null = null;
  try {
    const body = (await copy.json()) as { code?: unknown };
    code = typeof body.code === "string" ? body.code : null;
  } catch {
    // not JSON
  }
  return new LinkError(await extractError(res), code);
}

export async function listLinkedFolders(project: string): Promise<LinkedFolder[]> {
  const res = await fetch(base(project), { cache: "no-store" });
  if (!res.ok) throw await refused(res);
  const body: unknown = await res.json();
  return Array.isArray(body) ? (body as LinkedFolder[]) : [];
}

export async function linkFolder(project: string, path: string): Promise<LinkedFolder> {
  const res = await fetch(base(project), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw await refused(res);
  return (await res.json()) as LinkedFolder;
}

export async function renameLinkedFolder(
  project: string,
  link: string,
  name: string,
): Promise<LinkedFolder> {
  const res = await fetch(`${base(project)}/${encodeURIComponent(link)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await refused(res);
  return (await res.json()) as LinkedFolder;
}

export async function unlinkFolder(project: string, link: string): Promise<void> {
  const res = await fetch(`${base(project)}/${encodeURIComponent(link)}`, { method: "DELETE" });
  if (!res.ok) throw await refused(res);
}

export async function listLinkedEntries(
  project: string,
  link: string,
  dir: string,
): Promise<LinkedEntry[]> {
  const query = dir ? `?${new URLSearchParams({ dir }).toString()}` : "";
  const res = await fetch(`${base(project)}/${encodeURIComponent(link)}/entries${query}`, {
    cache: "no-store",
  });
  if (!res.ok) throw await refused(res);
  const body: unknown = await res.json();
  return Array.isArray(body) ? (body as LinkedEntry[]) : [];
}

/** The folder picker in the VISTA window, or null in a browser or an older window. */
export function folderPicker(): (() => Promise<string | null>) | null {
  if (typeof window === "undefined") return null;
  const bridge = window.vistaApp;
  return bridge?.isVistaApp === true && typeof bridge.pickFolder === "function"
    ? () => bridge.pickFolder!()
    : null;
}
