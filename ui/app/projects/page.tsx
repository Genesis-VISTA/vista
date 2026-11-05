"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import type { KnowledgeBaseSummary, SkillSummary } from "@/lib/types";
import { AppTopBar } from "@/components/AppTopBar";
import { relativeTime, useProjectStats } from "@/lib/project-stats";
import { DEFAULT_DESTINATION, takePendingDestination } from "@/lib/pending-destination";
import { forumCredential, type SettingsTokens } from "@/lib/forum-credential";
import { fetchSettings, type LocalSettings } from "@/lib/settings";
import { hostedForumServer } from "@/lib/debates";
import { publishAudience, usableInProject, useCollaboration, useTeams } from "@/lib/collaboration";
import { TeamBadge } from "@/components/TeamSwitcher";
import {
  importRemoteProject,
  importSummary,
  isProjectOnServer,
  listRemoteProjects,
  projectSharingLabel,
  publishedFirst,
  publishProject,
  type ProjectImported,
} from "@/lib/project-sharing";
import type { SharingRefusal } from "@/lib/skill-sharing";
import { PublishConfirmModal } from "@/components/PublishConfirmModal";
import { CopyToTeamModal, copyTargets } from "@/components/CopyToTeamModal";
import { PublishConflictModal } from "@/components/PublishConflictModal";
import { ProjectImportModal } from "@/components/ProjectImportModal";
import { HELLO_OLCF_PROJECT_NAME } from "@/lib/default-project";
import {
  type ForumChoice,
  type Project,
  type ProjectFields,
  activateProject,
  forumRequest,
  hostForum,
  projectNameError,
  createProject,
  deleteProject,
  notifyActiveProjectChanged,
  toCreate,
  updateProject,
  useActiveProject,
  useProjects,
  writeActiveProjectName,
} from "@/lib/projects";

/** Project draft used by the create/edit modal (no `id` until persisted). */
type ProjectDraft = ProjectFields;

/** What the dialog asks of a create beyond the fields: whether a hosted forum is wanted. */
type SaveOptions = { hostedForumWanted: boolean };

type ModalState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; project: Project };

export default function ProjectsPage() {
  // useSearchParams forces this page out of static prerender; wrap in
  // Suspense so the rest of the tree can still stream in.
  return (
    <Suspense fallback={<div className="standalone-page" />}>
      <ProjectsPageContent />
    </Suspense>
  );
}

function ProjectsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { projects, loading, error, refresh } = useProjects();
  const activeProject = useActiveProject();
  const activeName = activeProject?.name ?? null;
  // The tour's project: the seeded hello-olcf one, else whichever comes first.
  const tourProjectName =
    (projects.find((p) => p.name === HELLO_OLCF_PROJECT_NAME) ?? projects[0])?.name ?? null;
  const [skillCatalog, setSkillCatalog] = useState<SkillSummary[]>([]);
  const [kbCatalog, setKbCatalog] = useState<KnowledgeBaseSummary[]>([]);
  // The NavRail's "+ New project" link routes here with `?new=1` to auto-open
  // the create modal. Read it during initial state setup so we don't need a
  // setState-in-effect to flip the modal open after mount.
  const shouldOpenNew = searchParams?.get("new") === "1";
  const [modal, setModal] = useState<ModalState>(
    shouldOpenNew ? { mode: "create" } : { mode: "closed" }
  );
  const collab = useCollaboration();
  const teams = useTeams();
  const serverUrl = collab.status?.reachable ? collab.status.url : null;
  // Only the selected team's projects (Private's: those on this computer only); the rest are a team switch away.
  const shownProjects = projects.filter((p) => teams.show(p.teamId));
  const hiddenCount = projects.length - shownProjects.length;
  const [notice, setNotice] = useState<{
    text: string;
    skill?: string;
    knowledgeBase?: string;
  } | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [pendingPublish, setPendingPublish] = useState<Project | null>(null);
  const [conflict, setConflict] = useState<{ project: Project; refusal: SharingRefusal } | null>(
    null
  );
  const [showImport, setShowImport] = useState(false);
  const [copying, setCopying] = useState<Project | null>(null);
  /** The server's newest version of each item, by id, for "version N available". */
  const [heads, setHeads] = useState<Record<string, number>>({});

  const refreshHeads = useCallback(() => {
    listRemoteProjects({ manifests: false })
      .then((remote) => setHeads(Object.fromEntries(remote.map((r) => [r.id, r.head_version]))))
      .catch(() => setHeads({}));
  }, []);

  useEffect(() => {
    if (collab.available) refreshHeads();
  }, [collab.available, refreshHeads]);

  const refreshSkillCatalog = useCallback(() => {
    fetch("/api/skills")
      .then((res) => res.json())
      .then((data) => setSkillCatalog(Array.isArray(data) ? data : []))
      .catch(() => setSkillCatalog([]));
  }, []);

  const refreshKbCatalog = useCallback(() => {
    fetch("/api/knowledge-bases")
      .then((res) => res.json())
      .then((data) => setKbCatalog(Array.isArray(data) ? data : []))
      .catch(() => setKbCatalog([]));
  }, []);

  useEffect(() => {
    refreshSkillCatalog();
    refreshKbCatalog();
  }, [refreshSkillCatalog, refreshKbCatalog]);

  // Strip `?new=1` from the URL after we've used it so refreshes don't
  // re-open the modal and the URL stays clean.
  useEffect(() => {
    if (shouldOpenNew) {
      router.replace("/projects");
    }
  }, [shouldOpenNew, router]);

  function open(project: Project) {
    activateProject(project);
    // Back to whatever sent them here, or to chat if they started on this page.
    router.push(takePendingDestination() ?? DEFAULT_DESTINATION);
  }

  async function saveProject(draft: ProjectDraft, options: SaveOptions, editing?: Project) {
    if (editing) {
      await updateProject(editing.name, toCreate(draft));
      // The active-project pointer stores the name; if we just renamed the
      // active project, repoint it so it doesn't dangle.
      if (editing.name !== draft.name && activeName === editing.name) {
        writeActiveProjectName(draft.name);
        notifyActiveProjectChanged();
      }
    } else {
      const project = await createProject(toCreate(draft), options);
      if (project.forumNotice) {
        // The project is made, without the hosted forum it was to get. Stay,
        // so the reason can be read, rather than carry it into chat unseen.
        activateProject(project);
        setNotice({ text: `Created ${project.name}. ${project.forumNotice}` });
      } else {
        open(project);
      }
    }
    setModal({ mode: "closed" });
  }

  async function publish(project: Project, opts: { onTopOf?: number; asNew?: boolean } = {}) {
    setBusyName(project.name);
    setNotice(null);
    try {
      const result = await publishProject(project.name, opts);
      if (result.ok) {
        const skills = publishedFirst(result);
        setNotice({
          text:
            result.status === "unchanged"
              ? `Nothing new to publish: ${project.name} is version ${result.project.remoteVersion} on the server.`
              : `Published ${project.name} as version ${result.project.remoteVersion}${skills}.`,
        });
        await refresh();
        refreshHeads();
        return;
      }
      if (result.code === "version_conflict" || result.code === "remote_missing") {
        setConflict({ project, refusal: result });
        return;
      }
      setNotice({
        text: result.detail,
        skill: result.skill ?? undefined,
        knowledgeBase: result.knowledgeBase ?? undefined,
      });
    } finally {
      setBusyName(null);
    }
  }

  /** Replace the project here with the server's version `version`, the one the researcher was shown. */
  async function takeServers(project: Project, version?: number) {
    if (!project.remoteId) return;
    setBusyName(project.name);
    setNotice(null);
    try {
      const result = await importRemoteProject(project.remoteId, {
        version,
        replaceModified: true,
      });
      if (result.ok) imported(result);
      else setNotice({ text: result.detail });
    } finally {
      setBusyName(null);
    }
  }

  function imported(result: ProjectImported) {
    setShowImport(false);
    const what =
      result.status === "unchanged"
        ? `${result.project.name} is already here at version ${result.project.remoteVersion}.`
        : `Imported ${result.project.name} version ${result.project.remoteVersion}.`;
    const more = importSummary(result);
    setNotice({ text: more ? `${what} ${more}` : what });
    // The import may have brought skills and knowledge bases, which the
    // project dialog lists.
    refreshSkillCatalog();
    refreshKbCatalog();
    void refresh();
    refreshHeads();
  }

  async function removeProject(project: Project) {
    const ok = window.confirm(
      `Delete project "${project.name}"? This cannot be undone.`
    );
    if (!ok) return;
    await deleteProject(project.name);
    if (activeName === project.name) {
      writeActiveProjectName(null);
      notifyActiveProjectChanged();
    }
  }

  return (
    <div className="app-page">
      <AppTopBar title="Projects" showProject={false} />
      <div className="app-page-body">
      <p className="page-lede">
        Pick a project to scope the chat session with its system prompt, skills, and tools.{" "}
        <button
          type="button"
          className="button ghost button-sm"
          disabled={!collab.available}
          title={collab.available ? "Import a project from the collaboration server" : (collab.reason ?? undefined)}
          onClick={() => setShowImport(true)}
        >
          Import from the collaboration server
        </button>
      </p>

      {notice && (
        <div className="hub-count" role="status" style={{ marginBottom: 12 }}>
          {notice.text}
          {notice.skill && (
            <>
              {" "}
              <Link href="/skills">Open the Skills page</Link>
            </>
          )}
          {notice.knowledgeBase && (
            <>
              {" "}
              <Link href="/knowledge-bases">Open the Knowledge Bases page</Link>
            </>
          )}
        </div>
      )}

      {error && (
        <div className="error" style={{ marginBottom: 12, fontSize: 13 }}>
          {error}{" "}
          <button type="button" className="button ghost button-xs" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      )}

      {hiddenCount > 0 && (
        <div className="hub-count" style={{ marginBottom: 12 }}>
          {hiddenCount === 1 ? "1 project is" : `${hiddenCount} projects are`} in another
          team. Switch team in the sidebar to see {hiddenCount === 1 ? "it" : "them"}.
        </div>
      )}

      <div className="projects-panel">
        {!loading && projects.length === 0 ? (
          <div className="projects-empty">
            <h2 className="projects-empty-title">No projects yet</h2>
            <p className="projects-empty-body">
              A project is a working context: its own system prompt, the skills and
              tools the agent may use, and the conversations and files that
              belong to it. Chat starts here, so this is the first thing to make.
            </p>
            <button
              type="button"
              className="button"
              onClick={() => setModal({ mode: "create" })}
            >
              Create a project
            </button>
          </div>
        ) : (
        <div className="projects-grid">
          {loading && projects.length === 0 && (
            <div className="chat-bubble">Loading projects…</div>
          )}
          {shownProjects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              tourTarget={project.name === tourProjectName}
              active={activeName === project.name}
              sharing={projectSharingLabel(
                project,
                serverUrl,
                project.remoteId ? heads[project.remoteId] : undefined
              )}
              onServer={isProjectOnServer(project, serverUrl)}
              publishing={busyName === project.name}
              publishDisabledReason={collab.available ? null : collab.reason}
              copyable={teams.shown && copyTargets(teams.teams, project.teamId).length > 0}
              onCopy={() => setCopying(project)}
              onOpen={() => open(project)}
              onEdit={() => setModal({ mode: "edit", project })}
              onDelete={() => void removeProject(project)}
              onPublish={() => {
                if (isProjectOnServer(project, serverUrl)) void publish(project);
                else setPendingPublish(project);
              }}
            />
          ))}
          <button
            type="button"
            className="project-new"
            data-tour="project:new"
            onClick={() => setModal({ mode: "create" })}
          >
            <div className="project-new-plus" aria-hidden="true">+</div>
            <div className="project-new-label">New project</div>
          </button>
        </div>
        )}
      </div>
      </div>

      {(modal.mode === "create" || modal.mode === "edit") && (
        <ProjectModal
          mode={modal.mode}
          initial={modal.mode === "edit" ? modal.project : undefined}
          skillCatalog={skillCatalog}
          kbCatalog={kbCatalog}
          existingNames={new Set(projects.map((p) => p.name))}
          onCancel={() => setModal({ mode: "closed" })}
          onSave={(draft, options) =>
            saveProject(draft, options, modal.mode === "edit" ? modal.project : undefined)
          }
        />
      )}

      <PublishConfirmModal
        open={pendingPublish !== null}
        title="Publish project"
        message={pendingPublish
          ? `Publish "${pendingPublish.name}" to the collaboration server at ${serverUrl ?? "?"}?\n\n${publishAudience(pendingPublish.teamId, teams)}\n\nIts skills and knowledge bases that are not on the server, or have changed, are published to the same team first: a knowledge base with its PDFs and index, which can take a while. A published version stays there; you can publish newer ones.`
          : ""}
        confirmLabel="Publish"
        onCancel={() => setPendingPublish(null)}
        onConfirm={() => {
          if (!pendingPublish) return;
          const project = pendingPublish;
          setPendingPublish(null);
          void publish(project);
        }}
      />

      {conflict && (
        <PublishConflictModal
          name={conflict.project.name}
          noun="project"
          refusal={conflict.refusal}
          onCancel={() => setConflict(null)}
          onTakeServers={() => {
            setConflict(null);
            void takeServers(conflict.project, conflict.refusal.headVersion ?? undefined);
          }}
          onPublishOnTop={(head) => {
            setConflict(null);
            void publish(conflict.project, { onTopOf: head });
          }}
          onPublishAsNew={() => {
            setConflict(null);
            void publish(conflict.project, { asNew: true });
          }}
        />
      )}

      {copying && (
        <CopyToTeamModal
          kind="project"
          name={copying.name}
          teamId={copying.teamId}
          onClose={() => setCopying(null)}
          onCopied={() => {
            // The project and what it uses joined the team, or were imported
            // beside the originals: every list here may have changed.
            refreshSkillCatalog();
            refreshKbCatalog();
            void refresh();
            refreshHeads();
          }}
        />
      )}

      {showImport && (
        <ProjectImportModal onClose={() => setShowImport(false)} onImported={imported} />
      )}

    </div>
  );
}

function ProjectCard({
  project,
  tourTarget,
  active,
  sharing,
  onServer,
  publishing,
  publishDisabledReason,
  copyable,
  onCopy,
  onOpen,
  onEdit,
  onDelete,
  onPublish,
}: {
  project: Project;
  /** The card the first-boot tour points at. */
  tourTarget: boolean;
  active: boolean;
  /** Where it stands with the collaboration server. */
  sharing: string;
  onServer: boolean;
  publishing: boolean;
  /** Why Publish is off; null when it can be used. */
  publishDisabledReason: string | null;
  /** A team to copy it to exists, and the server answers. */
  copyable: boolean;
  onCopy: () => void;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onPublish: () => void;
}) {
  const stats = useProjectStats(project.name);

  return (
    <div
      className={`project-card${active ? " active" : ""}`}
      data-tour={tourTarget ? "project:card" : undefined}
    >
      <div className="project-card-head">
        <div>
          <div className="project-card-title">
            {project.name}
            <TeamBadge teamId={project.teamId} />
          </div>
          {active && <span className="project-card-badge active">Active</span>}
        </div>
      </div>
      {stats && (
        <div className="project-card-stats">
          <span>
            {stats.conversations} {stats.conversations === 1 ? "conversation" : "conversations"}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            {stats.files} {stats.files === 1 ? "file" : "files"}
          </span>
          {stats.lastActive && (
            <>
              <span aria-hidden="true">·</span>
              <span>active {relativeTime(stats.lastActive)}</span>
            </>
          )}
        </div>
      )}
      <div className="project-card-desc">{project.description || "No description."}</div>
      <div className="project-card-sharing">{sharing}</div>

      <ChipRow label="Skills" items={project.skills} />
      <ChipRow label="Knowledge Bases" items={project.knowledgeBases} />
      <ChipRow label="Tools" items={project.tools} />

      <div className="project-card-actions">
        <button
          type="button"
          className="button"
          data-tour={tourTarget ? "project:open" : undefined}
          onClick={onOpen}
        >
          {active ? "Reopen" : "Open"}
        </button>
        <button type="button" className="button ghost button-sm" onClick={onEdit}>
          Edit
        </button>
        {/* A private project is shared by copying it to a team; one in a team
            publishes new versions to it. */}
        {project.teamId && (
          <button
            type="button"
            className="button ghost button-sm"
            disabled={publishing || publishDisabledReason !== null}
            title={publishDisabledReason ?? "Publish this project and its skills to its team"}
            onClick={onPublish}
          >
            {publishing
              ? "Publishing…"
              : !onServer
                ? "Publish"
                : project.modified
                  ? "Publish changes"
                  : "Republish"}
          </button>
        )}
        {copyable && (
          <button
            type="button"
            className="button ghost button-sm"
            disabled={publishing}
            title="Copy this project, with its skills and knowledge bases, to a team"
            onClick={onCopy}
          >
            Copy to team…
          </button>
        )}
        <button type="button" className="button ghost button-sm" onClick={onDelete}>
          Delete
        </button>
      </div>
    </div>
  );
}

function ChipRow({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="project-card-row">
      <div className="project-card-row-label">{label}</div>
      <div className="project-card-chips">
        {items.length === 0 ? (
          <span className="project-card-chip empty">none</span>
        ) : (
          items.map((item) => (
            <span key={item} className="project-card-chip">
              {item}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

/** Under a skill or knowledge base a project has but cannot use, which the backend refuses on save. */
function ForeignNote() {
  return (
    <div className="error" style={{ fontSize: 11 }}>
      In another team: this project cannot use it. Uncheck it to save.
    </div>
  );
}

/** Which credential the forum publishes with, said under its URL. */
/** A new hosted forum is its creator's alone until the project is published. */
const PRIVATE_FORUM_NOTE =
  "Only you can read it until you publish the project; then everyone in its team can.";

const CREDENTIAL_NOTES = {
  collaboration: "Publishes with your collaboration sign-in.",
  "settings-github": "Publishes with your GitHub token from Settings → Repositories.",
  "settings-gitlab": "Publishes with your gitlab.com token from Settings → Repositories.",
  "system-git":
    "Publishes with your system git’s credentials: an SSH key or a credential helper. For an https:// forum on github.com or gitlab.com, you can add a token in Settings → Repositories instead.",
} as const;

function ProjectModal({
  mode,
  initial,
  skillCatalog,
  kbCatalog,
  existingNames,
  onCancel,
  onSave,
}: {
  mode: "create" | "edit";
  initial?: Project;
  skillCatalog: SkillSummary[];
  kbCatalog: KnowledgeBaseSummary[];
  existingNames: Set<string>;
  onCancel: () => void;
  onSave: (draft: ProjectFields, options: SaveOptions) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.systemPrompt ?? "");
  const [skills, setSkills] = useState<Set<string>>(new Set(initial?.skills ?? []));
  const [kbs, setKbs] = useState<Set<string>>(new Set(initial?.knowledgeBases ?? []));
  const [forumRepoUrl, setForumRepoUrl] = useState(initial?.forumRepoUrl ?? "");
  // The forum as the project has it saved, which moving it onto the server changes.
  const [savedForumUrl, setSavedForumUrl] = useState(initial?.forumRepoUrl ?? "");
  const [forumChoice, setForumChoice] = useState<ForumChoice>("hosted");
  const [hosting, setHosting] = useState<{ state: "idle" | "moving" } | { state: "failed"; message: string }>({
    state: "idle",
  });
  const collab = useCollaboration();
  // The server the project's forum would be on, and whether this install may use it.
  const collabServer = collab.status?.url ?? null;
  const canHost = collab.available;
  const hosted = hostedForumServer(savedForumUrl, collabServer) !== null;
  // Published, the project's team can read and post; before that only its creator.
  const teamCanPost = initial ? isProjectOnServer(initial, collabServer) : false;
  // Which Settings tokens exist, to say which credential the forum publishes with.
  const [settingsTokens, setSettingsTokens] = useState<
    (SettingsTokens & Pick<LocalSettings, "collaboration_team_id">) | null
  >(null);
  useEffect(() => {
    let live = true;
    fetchSettings()
      .then((u) => live && setSettingsTokens(u))
      .catch(() => live && setSettingsTokens(null));
    return () => {
      live = false;
    };
  }, []);
  // Unknown until Settings has loaded: saying "system git" meanwhile would be
  // wrong for anyone with a token saved.
  const chooser = mode === "create" && canHost;
  const credential =
    chooser && forumChoice === "hosted"
      ? "collaboration"
      : settingsTokens
        ? forumCredential(forumRepoUrl, settingsTokens, collabServer)
        : null;
  // The project's team, which decides what it may use: its own when editing,
  // else the one selected in Settings, which a new project joins.
  const projectTeam =
    mode === "edit" ? (initial?.teamId ?? null) : (settingsTokens?.collaboration_team_id ?? null);
  // Only what the project can use is offered, the rule the backend enforces on
  // save. One it already has from another team stays listed, flagged, so it
  // can be removed.
  const offeredSkills = skillCatalog.filter(
    (s) => usableInProject(s.teamId, projectTeam) || skills.has(s.slug),
  );
  const offeredKbs = kbCatalog.filter(
    (kb) => usableInProject(kb.team_id, projectTeam) || kbs.has(kb.slug),
  );
  const [toolsRaw, setToolsRaw] = useState((initial?.tools ?? []).join(", "));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function toggleSkill(slug: string) {
    setSkills((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function toggleKb(slug: string) {
    setKbs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  async function moveToServer() {
    if (!initial) return;
    setHosting({ state: "moving" });
    try {
      const moved = await hostForum(initial.name);
      setSavedForumUrl(moved.forumRepoUrl);
      setForumRepoUrl(moved.forumRepoUrl);
      setHosting({ state: "idle" });
    } catch (e) {
      // The project stays on the forum it had; the server's sentence says why.
      setHosting({ state: "failed", message: e instanceof Error ? e.message : "The forum was not moved." });
    }
  }

  async function commit() {
    const trimmedName = name.trim();
    const trimmedDesc = description.trim();
    const nameError = projectNameError(trimmedName);
    if (nameError) {
      setError(nameError);
      return;
    }
    if (mode === "create" && existingNames.has(trimmedName)) {
      setError(`A project named "${trimmedName}" already exists.`);
      return;
    }
    const splitTags = (raw: string) =>
      raw
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);

    // Signed in, the dialog's choice decides; otherwise a blank URL is no forum, as ever.
    const forum = chooser
      ? forumRequest(forumChoice, forumRepoUrl)
      : { forumRepoUrl: forumRepoUrl.trim(), hostedForumWanted: true };
    if (chooser && forumChoice === "url" && !forum.forumRepoUrl) {
      setError("Enter the repository's URL, or choose another forum.");
      return;
    }

    setSaving(true);
    setError("");
    try {
      await onSave({
        name: trimmedName,
        description: trimmedDesc,
        systemPrompt: systemPrompt.trim(),
        skills: Array.from(skills),
        knowledgeBases: Array.from(kbs),
        forumRepoUrl: forum.forumRepoUrl,
        // New projects inherit every available MCP tool. Keep an existing
        // project's saved patterns intact when editing it.
        tools: mode === "create" ? [] : splitTags(toolsRaw),
        // PUT is a full overwrite on the backend — echo the existing usage
        // limits so they aren't reset on edit.
        usageLimits: initial?.usageLimits ?? {},
      }, { hostedForumWanted: forum.hostedForumWanted });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save project.");
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal project-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={mode === "create" ? "Create project" : "Edit project"}
      >
        <div className="panel-header">
          <div className="panel-title">{mode === "create" ? "New project" : "Edit project"}</div>
          <button type="button" className="button ghost" onClick={onCancel}>
            Close
          </button>
        </div>
        <div className="modal-body">
          <label className="project-modal-label">
            Name
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              maxLength={80}
            />
          </label>

          <label className="project-modal-label">
            Description
            <textarea
              className="input"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={400}
            />
          </label>

          <label className="project-modal-label">
            System prompt
            <textarea
              className="input"
              rows={4}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="Extra instructions appended to the base agent prompt."
            />
          </label>

          <div className="project-modal-label">
            <div>Skills</div>
            <div className="project-modal-skill-list">
              {skillCatalog.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--muted)" }}>Loading skills...</div>
              ) : offeredSkills.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--muted)" }}>
                  No skills this project&rsquo;s team can use.
                </div>
              ) : (
                offeredSkills.map((s) => (
                  <label key={s.slug} className="project-modal-skill-item">
                    <input
                      type="checkbox"
                      checked={skills.has(s.slug)}
                      onChange={() => toggleSkill(s.slug)}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>
                        {s.name}
                        <TeamBadge teamId={s.teamId} />
                      </div>
                      {!usableInProject(s.teamId, projectTeam) && <ForeignNote />}
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>
                        {s.description.slice(0, 120)}
                        {s.description.length > 120 ? "..." : ""}
                      </div>
                    </div>
                  </label>
                ))
              )}
            </div>
          </div>

          <div className="project-modal-label">
            <div>Knowledge Bases</div>
            <div className="project-modal-skill-list">
              {offeredKbs.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--muted)" }}>
                  {kbCatalog.length === 0
                    ? "No knowledge bases yet. Create one from the Knowledge Bases page."
                    : "No knowledge bases this project\u2019s team can use."}
                </div>
              ) : (
                offeredKbs.map((kb) => (
                  <label key={kb.slug} className="project-modal-skill-item">
                    <input
                      type="checkbox"
                      checked={kbs.has(kb.slug)}
                      onChange={() => toggleKb(kb.slug)}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>
                        {kb.name}
                        <TeamBadge teamId={kb.team_id} />
                      </div>
                      {!usableInProject(kb.team_id, projectTeam) && <ForeignNote />}
                      <div style={{ fontSize: 11, color: "var(--muted)" }}>
                        {(kb.description ?? "").slice(0, 120)}
                        {(kb.description ?? "").length > 120 ? "..." : ""}
                      </div>
                    </div>
                  </label>
                ))
              )}
            </div>
          </div>

          {hosted ? (
            <div className="project-modal-label" data-testid="forum-hosted">
              Hypothesis Lab
              <span style={{ fontWeight: 400 }}>Hosted on the collaboration server.</span>
              <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
                {teamCanPost
                  ? "Everyone in the project’s team can post to its debates."
                  : PRIVATE_FORUM_NOTE}{" "}
                {CREDENTIAL_NOTES.collaboration}
              </span>
            </div>
          ) : (
            <>
              {chooser && (
                <fieldset className="project-modal-label" style={{ border: 0, padding: 0, margin: 0 }}>
                  <legend style={{ padding: 0 }}>Hypothesis Lab forum</legend>
                  {(
                    [
                      ["hosted", "Hosted on the collaboration server"],
                      ["url", "A repository URL"],
                      ["none", "No forum"],
                    ] as const
                  ).map(([value, label]) => (
                    <label key={value} style={{ fontWeight: 400, display: "flex", gap: 6 }}>
                      <input
                        type="radio"
                        name="forum-choice"
                        checked={forumChoice === value}
                        onChange={() => setForumChoice(value)}
                      />
                      {label}
                    </label>
                  ))}
                </fieldset>
              )}
              {chooser && forumChoice === "hosted" && (
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  {PRIVATE_FORUM_NOTE}{" "}
                  {CREDENTIAL_NOTES.collaboration}
                </span>
              )}
              {(!chooser || forumChoice === "url") && (
                <label className="project-modal-label">
                  {chooser ? (
                    "Repository URL"
                  ) : (
                    <>
                      Hypothesis Lab{" "}
                      <span style={{ color: "var(--muted)", fontWeight: 400 }}>
                        (git repository the debates publish to; leave blank to turn it off)
                      </span>
                    </>
                  )}
                  <input
                    className="input"
                    value={forumRepoUrl}
                    onChange={(e) => setForumRepoUrl(e.target.value)}
                    placeholder="https://github.com/your-org/your-forum.git"
                  />
                  <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
                    Everyone with push access to this repository can post to the
                    project&rsquo;s debates, so it is the guest list for the room. The
                    repository must already exist; saving checks it can be reached and
                    pulls in any threads it already holds.
                    {credential && <> {CREDENTIAL_NOTES[credential]}</>}
                  </span>
                </label>
              )}
            </>
          )}

          {mode === "edit" && canHost && !hosted && (
            <div className="project-modal-label">
              <div>
                <button
                  type="button"
                  className="button ghost"
                  onClick={() => void moveToServer()}
                  disabled={hosting.state === "moving" || forumRepoUrl.trim() !== savedForumUrl}
                >
                  {hosting.state === "moving" ? "Moving the forum…" : "Host on the collaboration server"}
                </button>
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
                Threads are copied to the server; the old repository is left unchanged.
                {forumRepoUrl.trim() !== savedForumUrl && " Save the repository change first."}
              </span>
              {hosting.state === "failed" && (
                <span className="error" role="alert" style={{ fontSize: 12, fontWeight: 400 }}>
                  {hosting.message}
                </span>
              )}
            </div>
          )}

          {mode === "edit" && (
            <label className="project-modal-label">
              Tools{" "}
              <span style={{ color: "var(--muted)", fontWeight: 400 }}>
                (comma-separated fnmatch patterns; `!` prefix denies)
              </span>
              <input
                className="input"
                value={toolsRaw}
                onChange={(e) => setToolsRaw(e.target.value)}
                placeholder="e.g. *, !agenthpc_*"
              />
            </label>
          )}

          {error && (
            <div className="error" style={{ fontSize: 12 }}>
              {error}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <button type="button" className="button ghost" onClick={onCancel} disabled={saving}>
              Cancel
            </button>
            <button type="button" className="button" onClick={() => void commit()} disabled={saving || hosting.state === "moving"}>
              {saving ? "Saving…" : mode === "create" ? "Create" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
