/**
 * Knowledge bases on the collaboration server: the import list, publishing one
 * (its PDFs and built index) and importing one, through the
 * `/api/knowledge-bases/{slug}/publish` and `/api/collaboration/knowledge-bases*`
 * proxies.
 *
 * Refusals are the skills' `SharingRefusal`. Two have no `code`, as they are
 * not a choice: a knowledge base still indexing, and an index built with an
 * embedding model or chromadb this VISTA cannot search with; their `detail`
 * says so. Controls read `useCollaboration()` first.
 */

import { post, refusal, type SharingRefusal } from "@/lib/skill-sharing";
import type { KnowledgeBase, Publication } from "@/lib/types";

/** What a version of a knowledge base is (backend `KnowledgeBaseManifest`). */
export type KnowledgeBaseManifest = {
  format: number;
  slug: string;
  name: string;
  description: string | null;
  publications: Publication[];
  files: number;
  size: number;
  archive_size: number;
  /** False for one nothing was indexed into: its archive holds no index. */
  has_index: boolean;
  embedding_model: string;
  embedding_revision: string;
  chromadb: string;
};

/** A knowledge base on the collaboration server (backend `RemoteKnowledgeBase`). */
export type RemoteKnowledgeBase = {
  id: string;
  /** The publisher's slug. */
  name: string;
  head_version: number;
  created_by_name: string | null;
  created_at: string;
  published_by_name: string | null;
  published_at: string | null;
  /** Null when it was not fetched, or is not a manifest this VISTA reads. */
  manifest: KnowledgeBaseManifest | null;
  /** This installation's copy of it; `name` is its slug here. */
  local: { name: string; remote_version: number | null; modified: boolean } | null;
  /** Another knowledge base here has its slug: importing it needs a new one. */
  name_taken: boolean;
};

export type KnowledgeBasePublished = {
  ok: true;
  status: "published" | "unchanged";
  knowledgeBase: KnowledgeBase;
};

export type KnowledgeBaseImported = {
  ok: true;
  status: "imported" | "updated" | "unchanged";
  knowledgeBase: KnowledgeBase;
};

/** Backend `db.schemas.SLUG_PATTERN`. */
export const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$/;

/** Why `slug` cannot name a knowledge base, or null when it can (backend `is_valid_slug`). */
export function slugError(slug: string): string | null {
  if (SLUG.test(slug)) return null;
  return "A slug is 1 to 80 lowercase letters, digits and dashes, starting and ending with a letter or digit.";
}

/** A slug to offer for importing `kb` under: its own, plus `-2`, kept within the rule. */
export function suggestedSlug(kb: RemoteKnowledgeBase): string {
  const usable = kb.name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 78);
  return `${usable || "knowledge-base"}-2`;
}

/** The collaboration server's knowledge bases. Throws with the reason when they cannot be listed. */
export async function listRemoteKnowledgeBases(
  { manifests = true }: { manifests?: boolean } = {},
): Promise<RemoteKnowledgeBase[]> {
  const query = manifests ? "" : "?manifests=false";
  const res = await fetch(`/api/collaboration/knowledge-bases${query}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await refusal(res)).detail);
  const data = (await res.json()) as unknown;
  return Array.isArray(data) ? (data as RemoteKnowledgeBase[]) : [];
}

/**
 * Publish a knowledge base's PDFs and index. `onTopOf` publishes over a head a
 * refusal reported; `asNew` as a new knowledge base.
 */
export async function publishKnowledgeBase(
  slug: string,
  { onTopOf, asNew }: { onTopOf?: number; asNew?: boolean } = {},
): Promise<KnowledgeBasePublished | SharingRefusal> {
  const res = await post(`/api/knowledge-bases/${encodeURIComponent(slug)}/publish`, {
    on_top_of: onTopOf ?? null,
    as_new: asNew ?? false,
  });
  if (!(res instanceof Response)) return res;
  if (!res.ok) return refusal(res);
  const body = (await res.json()) as {
    status: KnowledgeBasePublished["status"];
    knowledge_base: KnowledgeBase;
  };
  return { ok: true, status: body.status, knowledgeBase: body.knowledge_base };
}

/** Import a knowledge base from the server: `version`, or the newest. */
export async function importRemoteKnowledgeBase(
  id: string,
  opts: { version?: number; renameTo?: string; replaceModified?: boolean } = {},
): Promise<KnowledgeBaseImported | SharingRefusal> {
  const res = await post(`/api/collaboration/knowledge-bases/${encodeURIComponent(id)}/import`, {
    version: opts.version ?? null,
    rename_to: opts.renameTo ?? null,
    replace_modified: opts.replaceModified ?? false,
  });
  if (!(res instanceof Response)) return res;
  if (!res.ok) return refusal(res);
  const body = (await res.json()) as {
    status: KnowledgeBaseImported["status"];
    knowledge_base: KnowledgeBase;
  };
  return { ok: true, status: body.status, knowledgeBase: body.knowledge_base };
}

/** Whether a knowledge base here is a copy of an item on the server `serverUrl`. */
export function isKnowledgeBaseOnServer(kb: KnowledgeBase, serverUrl: string | null): boolean {
  return Boolean(kb.remote_id) && serverUrl !== null && kb.origin_server === serverUrl;
}

/**
 * One line on where a knowledge base here stands with the collaboration server
 * at `serverUrl`, given that server's newest version of it (`head`) if known.
 */
export function kbSharingLabel(
  kb: KnowledgeBase,
  serverUrl: string | null,
  head?: number | null,
): string {
  if (!kb.remote_id) return "Not published";
  if (serverUrl !== null && kb.origin_server !== serverUrl) {
    return "Published on another collaboration server";
  }
  const version = kb.remote_version ?? 0;
  const changed = kb.modified ? ", changed here" : "";
  if (head != null && head > version) {
    return `Version ${head} available (this is version ${version}${changed})`;
  }
  return kb.modified ? `Changed here since version ${version}` : `Version ${version}`;
}

/** Bytes, for a person: "12.3 MB". */
export function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
