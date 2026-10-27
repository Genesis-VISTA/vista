/**
 * Skills on the collaboration server: the Skill Hub's listing, publishing a
 * skill and importing one, through the `/api/skills/{slug}/publish` and
 * `/api/collaboration/skills*` proxies.
 *
 * A publish or import that needs the researcher's choice first comes back as a
 * `SharingRefusal` whose `code` says which: the server's head has moved on
 * (`version_conflict`, with `headVersion`), the skill is gone from the server
 * (`remote_missing`), the copy here has changes of its own (`modified`), or
 * its name is another skill's (`name_taken`). Controls read `useCollaboration()`
 * first, so these are only reached with a server that answered.
 */

import { refreshCollaborationStatus } from "@/lib/collaboration";
import type { SkillSummary } from "@/lib/types";

/** What a version says about the skill in it (backend `SkillManifest`). */
export type SkillManifest = {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  allowed_tools?: string | null;
  metadata?: Record<string, string | string[]> | null;
  author?: string | null;
  hpc_jobs: string[];
  files: number;
  size: number;
  repo_url?: string | null;
};

/** A skill on the collaboration server (backend `RemoteSkill`). */
export type RemoteSkill = {
  id: string;
  name: string;
  head_version: number;
  created_by_name: string | null;
  created_at: string;
  /** Who published the newest version; null when it was not fetched. */
  published_by_name: string | null;
  published_at: string | null;
  /** Null when it was not fetched, or is not a manifest this VISTA reads. */
  manifest: SkillManifest | null;
  /** This installation's copy of it. */
  local: { name: string; remote_version: number | null; modified: boolean } | null;
  /** Another skill here has its name: importing it needs a new one. */
  name_taken: boolean;
};

export type SharingCode =
  | "version_conflict"
  | "remote_missing"
  | "modified"
  | "name_taken"
  | "name_invalid"
  | "skill_conflict"
  | "knowledge_base_conflict"
  | "collaboration_unavailable"
  | "collaboration_sign_in_required"
  | "collaboration_refused"
  | "collaboration_error";

export type SharingRefusal = {
  ok: false;
  status: number;
  detail: string;
  code: SharingCode | null;
  /** With `version_conflict`: the server's newest version. */
  headVersion: number | null;
  /** With a project's `skill_conflict`: the skill that needs a choice first. */
  skill?: string | null;
  /** With a project's `knowledge_base_conflict`: the knowledge base, by slug. */
  knowledgeBase?: string | null;
};

export type Published = { ok: true; skill: SkillSummary };
export type Imported = {
  ok: true;
  status: "imported" | "updated" | "unchanged";
  skill: SkillSummary;
};

/** A refused publish or import, read from the backend's `{detail, code, …}`. */
export async function refusal(res: Response): Promise<SharingRefusal> {
  let body: Record<string, unknown> = {};
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    // not JSON; fall back to the status
  }
  const detail = body.detail ?? body.error;
  // The token expired or was refused since the last check: every Publish and
  // Import control follows, rather than each failing the same way in turn.
  if (body.code === "collaboration_sign_in_required") void refreshCollaborationStatus();
  return {
    ok: false,
    status: res.status,
    detail: typeof detail === "string" && detail ? detail : `Request failed (${res.status}).`,
    code: typeof body.code === "string" ? (body.code as SharingCode) : null,
    headVersion: typeof body.head_version === "number" ? body.head_version : null,
    ...(typeof body.skill === "string" ? { skill: body.skill } : {}),
    ...(typeof body.knowledge_base === "string" ? { knowledgeBase: body.knowledge_base } : {}),
  };
}

/** POST a JSON body; a request that cannot be sent is a refusal with status 0. */
export async function post(url: string, body: unknown): Promise<Response | SharingRefusal> {
  try {
    return await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return {
      ok: false,
      status: 0,
      detail: e instanceof Error ? e.message : "Request failed.",
      code: null,
      headVersion: null,
    };
  }
}

/**
 * The collaboration server's skills. Throws with the reason when they cannot
 * be listed. `manifests: false` asks for the head versions only, one request
 * to the server rather than one per skill.
 */
export async function listRemoteSkills(
  { manifests = true }: { manifests?: boolean } = {},
): Promise<RemoteSkill[]> {
  const query = manifests ? "" : "?manifests=false";
  const res = await fetch(`/api/collaboration/skills${query}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await refusal(res)).detail);
  const data = (await res.json()) as unknown;
  return Array.isArray(data) ? (data as RemoteSkill[]) : [];
}

/**
 * Publish a skill: its next version on the server, or a new skill there.
 * `onTopOf` publishes over a head a refusal reported; `asNew` as a new skill.
 */
export async function publishSkill(
  slug: string,
  { onTopOf, asNew }: { onTopOf?: number; asNew?: boolean } = {},
): Promise<Published | SharingRefusal> {
  const res = await post(`/api/skills/${encodeURIComponent(slug)}/publish`, {
    on_top_of: onTopOf ?? null,
    as_new: asNew ?? false,
  });
  if (!(res instanceof Response)) return res;
  if (!res.ok) return refusal(res);
  return { ok: true, skill: (await res.json()) as SkillSummary };
}

/**
 * Import a skill from the server, `version` or the newest, loading it into
 * `project` if given.
 */
export async function importRemoteSkill(
  id: string,
  opts: {
    version?: number;
    renameTo?: string;
    replaceModified?: boolean;
    project?: string | null;
  } = {},
): Promise<Imported | SharingRefusal> {
  const res = await post(`/api/collaboration/skills/${encodeURIComponent(id)}/import`, {
    version: opts.version ?? null,
    rename_to: opts.renameTo ?? null,
    replace_modified: opts.replaceModified ?? false,
    project: opts.project ?? null,
  });
  if (!(res instanceof Response)) return res;
  if (!res.ok) return refusal(res);
  const body = (await res.json()) as { status: Imported["status"]; skill: SkillSummary };
  return { ok: true, status: body.status, skill: body.skill };
}

/** Whether a skill here is a copy of an item on the server `serverUrl`. */
export function isOnServer(skill: SkillSummary, serverUrl: string | null): boolean {
  return Boolean(skill.remoteId) && serverUrl !== null && skill.originServer === serverUrl;
}

/**
 * One line on where a skill here stands with the collaboration server at
 * `serverUrl`, given that server's newest version of it (`head`) if known.
 */
export function sharingLabel(
  skill: SkillSummary,
  serverUrl: string | null,
  head?: number | null,
): string {
  if (skill.bundled) return "Bundled with VISTA";
  if (!skill.remoteId) return "Not published";
  if (serverUrl !== null && skill.originServer !== serverUrl) {
    return "Published on another collaboration server";
  }
  const version = skill.remoteVersion ?? 0;
  const changed = skill.modified ? ", changed here" : "";
  if (head != null && head > version) {
    return `Version ${head} available (this is version ${version}${changed})`;
  }
  return skill.modified ? `Changed here since version ${version}` : `Version ${version}`;
}

/** What importing this skill would do here, for its button. */
export function importAction(
  skill: Pick<RemoteSkill, "local" | "name_taken" | "head_version">,
): {
  label: string;
  kind: "import" | "rename" | "update" | "revert" | "current";
} {
  const local = skill.local;
  if (!local) {
    return skill.name_taken
      ? { label: "Import as…", kind: "rename" }
      : { label: "Import", kind: "import" };
  }
  if ((local.remote_version ?? 0) < skill.head_version) {
    return { label: `Update to version ${skill.head_version}`, kind: "update" };
  }
  if (local.modified) {
    return { label: `Revert to version ${skill.head_version}`, kind: "revert" };
  }
  return { label: "Imported ✓", kind: "current" };
}
