"use client";

import { useCallback, useEffect, useState } from "react";
import { useCollaboration } from "@/lib/collaboration";
import { projectNameError } from "@/lib/projects";
import {
  importRemoteProject,
  listRemoteProjects,
  projectImportAction,
  type ProjectImported,
  type RemoteProject,
} from "@/lib/project-sharing";

/**
 * The collaboration server's projects, to import onto this computer with the
 * skill versions each loads. Without a server that answers, it says why and
 * lists nothing. A project whose name is taken here asks for a new one; a copy
 * with changes made here asks before they are replaced.
 */
/** A name to offer for importing `project` under: its own, made usable here, plus `-2`. */
function suggestedName(project: RemoteProject): string {
  const usable = project.name.replace(/[^\w ._-]/g, "-").slice(0, 78);
  return `${usable}-2`;
}

export function ProjectImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (result: ProjectImported) => void;
}) {
  const collab = useCollaboration();
  const [projects, setProjects] = useState<RemoteProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  /** Asking for a name to import as, and why (`reason`): the item's is taken, or unusable here. */
  const [renaming, setRenaming] = useState<
    { project: RemoteProject; name: string; reason: string } | null
  >(null);
  const [replacing, setReplacing] = useState<RemoteProject | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setProjects(await listRemoteProjects());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The projects could not be listed.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (collab.available) void refresh();
  }, [collab.available, refresh]);

  async function runImport(
    project: RemoteProject,
    opts: { renameTo?: string; replaceModified?: boolean } = {},
  ) {
    setBusyId(project.id);
    setError(null);
    try {
      const result = await importRemoteProject(project.id, opts);
      if (result.ok) {
        onImported(result);
        return;
      }
      if (result.code === "name_taken" || result.code === "name_invalid") {
        // Keep what the researcher typed, if this was a rename.
        setRenaming({ project, name: opts.renameTo ?? suggestedName(project), reason: result.detail });
        return;
      }
      if (result.code === "modified") {
        setReplacing(project);
        return;
      }
      setError(result.detail);
    } finally {
      setBusyId(null);
    }
  }

  function onAction(project: RemoteProject) {
    const action = projectImportAction(project);
    if (action.kind === "rename") {
      setRenaming({
        project,
        name: suggestedName(project),
        reason: `A project named ${project.name} is already here.`,
      });
    }
    else if (project.local?.modified) setReplacing(project);
    else void runImport(project);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Import a project"
      >
        <div className="panel-header">
          <div className="panel-title">Import a project</div>
          <button type="button" className="button ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p style={{ margin: 0 }}>
            Projects published to the collaboration server
            {collab.status?.url ? ` at ${collab.status.url}` : ""}. Importing one
            imports the skills it loads too: it is your decision to trust them.
          </p>
          {!collab.available ? (
            <div className="hub-unavailable" role="status">
              {collab.reason}
            </div>
          ) : (
            <>
              {error && (
                <div className="error" role="alert" style={{ fontSize: 13 }}>
                  {error}
                </div>
              )}
              {loading && projects.length === 0 && <div className="hub-count">Loading…</div>}
              {!loading && projects.length === 0 && !error && (
                <div className="hub-empty">
                  No projects have been published yet. Publish one from its card on this page.
                </div>
              )}
              {projects.map((project) => {
                const action = projectImportAction(project);
                const busy = busyId === project.id;
                const manifest = project.manifest;
                return (
                  <div key={project.id} className="hub-card" data-testid="remote-project">
                    <div className="hub-card-head">
                      <div className="hub-card-name">{project.name}</div>
                      <button
                        type="button"
                        className={`hub-card-action${action.kind === "current" ? " loaded" : ""}`}
                        disabled={busy || busyId !== null || action.kind === "current" || !manifest}
                        title={manifest ? undefined : "This VISTA cannot read this project's newest version."}
                        onClick={() => onAction(project)}
                      >
                        {busy ? "Importing…" : action.label}
                      </button>
                    </div>
                    <div className="hub-card-desc">
                      {manifest?.description || "No description"}
                    </div>
                    <div className="hub-card-meta">
                      <span className="hub-card-author">
                        Version {project.head_version}
                        {project.published_by_name ? ` by ${project.published_by_name}` : ""}
                      </span>
                      {manifest && (
                        <>
                          <span className="hub-card-meta-sep">·</span>
                          <span>
                            {manifest.skills.length}{" "}
                            {manifest.skills.length === 1 ? "skill" : "skills"}
                          </span>
                        </>
                      )}
                      {project.local && project.local.name !== project.name && (
                        <>
                          <span className="hub-card-meta-sep">·</span>
                          <span>here as {project.local.name}</span>
                        </>
                      )}
                      {project.local?.modified && (
                        <>
                          <span className="hub-card-meta-sep">·</span>
                          <span>differs here</span>
                        </>
                      )}
                    </div>
                    {manifest && manifest.skills.length > 0 && (
                      <div className="hub-card-tags">
                        {manifest.skills.map((skill) => (
                          <span key={skill.name} className="hub-card-tag">
                            {skill.name}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </>
          )}

          {renaming && (
            <form
              style={{ display: "flex", flexDirection: "column", gap: 8 }}
              onSubmit={(e) => {
                e.preventDefault();
                const { project, name } = renaming;
                setRenaming(null);
                void runImport(project, { renameTo: name.trim() });
              }}
            >
              <p style={{ margin: 0 }}>{renaming.reason}</p>
              <label className="project-modal-label">
                Import it as
                <input
                  className="input"
                  value={renaming.name}
                  onChange={(e) => setRenaming({ ...renaming, name: e.target.value })}
                  autoFocus
                />
              </label>
              {renaming.name.trim() && projectNameError(renaming.name.trim()) && (
                <div className="error" style={{ fontSize: 12 }}>
                  {projectNameError(renaming.name.trim())}
                </div>
              )}
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button type="button" className="button ghost" onClick={() => setRenaming(null)}>
                  Cancel
                </button>
                <button
                  type="submit"
                  className="button"
                  disabled={projectNameError(renaming.name.trim()) !== null}
                >
                  Import
                </button>
              </div>
            </form>
          )}

          {replacing && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }} role="alert">
              <p style={{ margin: 0 }}>
                {replacing.local?.name ?? replacing.name} differs here from version{" "}
                {replacing.local?.remote_version}: it was changed here, or does not load a skill
                that version names. Importing version {replacing.head_version}{" "}
                replaces the project here.
              </p>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button type="button" className="button ghost" onClick={() => setReplacing(null)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    const project = replacing;
                    setReplacing(null);
                    void runImport(project, { replaceModified: true });
                  }}
                >
                  Replace it
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
