"use client";

import { useCallback, useEffect, useState } from "react";
import { useCollaboration } from "@/lib/collaboration";
import { importAction } from "@/lib/skill-sharing";
import {
  formatSize,
  importRemoteKnowledgeBase,
  listRemoteKnowledgeBases,
  slugError,
  suggestedSlug,
  type KnowledgeBaseImported,
  type RemoteKnowledgeBase,
} from "@/lib/kb-sharing";

/**
 * The collaboration server's knowledge bases, to import onto this computer
 * with their PDFs and built index, ready to search. Without a server that
 * answers, it says why and lists nothing. One whose slug is taken here asks
 * for a new one; a copy with changes made here asks before they are replaced.
 * One indexed with another embedding model is refused by the backend, which
 * says why.
 */
export function KnowledgeBaseImportModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (result: KnowledgeBaseImported) => void;
}) {
  const collab = useCollaboration();
  const [kbs, setKbs] = useState<RemoteKnowledgeBase[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  /** Asking for a slug to import as, and why: the item's is taken, or unusable here. */
  const [renaming, setRenaming] = useState<
    { kb: RemoteKnowledgeBase; slug: string; reason: string } | null
  >(null);
  const [replacing, setReplacing] = useState<RemoteKnowledgeBase | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setKbs(await listRemoteKnowledgeBases());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The knowledge bases could not be listed.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (collab.available) void refresh();
  }, [collab.available, refresh]);

  async function runImport(
    kb: RemoteKnowledgeBase,
    opts: { renameTo?: string; replaceModified?: boolean } = {},
  ) {
    setBusyId(kb.id);
    setError(null);
    try {
      const result = await importRemoteKnowledgeBase(kb.id, opts);
      if (result.ok) {
        onImported(result);
        return;
      }
      if (result.code === "name_taken" || result.code === "name_invalid") {
        setRenaming({ kb, slug: opts.renameTo ?? suggestedSlug(kb), reason: result.detail });
        return;
      }
      if (result.code === "modified") {
        setReplacing(kb);
        return;
      }
      setError(result.detail);
    } finally {
      setBusyId(null);
    }
  }

  function onAction(kb: RemoteKnowledgeBase) {
    const action = importAction(kb);
    if (action.kind === "rename") {
      setRenaming({
        kb,
        slug: suggestedSlug(kb),
        reason: `A knowledge base with the slug ${kb.name} is already here.`,
      });
    } else if (kb.local?.modified) setReplacing(kb);
    else void runImport(kb);
  }

  const renameError = renaming ? slugError(renaming.slug.trim()) : null;

  return (
    <div className="modal-backdrop" onClick={busyId ? undefined : onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Import a knowledge base"
      >
        <div className="panel-header">
          <div className="panel-title">Import a knowledge base</div>
          <button type="button" className="button ghost" onClick={onClose} disabled={busyId !== null}>
            Close
          </button>
        </div>
        <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p style={{ margin: 0 }}>
            Knowledge bases published to the collaboration server
            {collab.status?.url ? ` at ${collab.status.url}` : ""}. Importing one
            downloads its PDFs and its index, so it can be searched at once.
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
              {loading && kbs.length === 0 && <div className="hub-count">Loading…</div>}
              {!loading && kbs.length === 0 && !error && (
                <div className="hub-empty">
                  No knowledge bases have been published yet. Publish one from its page.
                </div>
              )}
              {kbs.map((kb) => {
                const action = importAction(kb);
                const busy = busyId === kb.id;
                const manifest = kb.manifest;
                return (
                  <div key={kb.id} className="hub-card" data-testid="remote-knowledge-base">
                    <div className="hub-card-head">
                      <div className="hub-card-name">{manifest?.name ?? kb.name}</div>
                      <button
                        type="button"
                        className={`hub-card-action${action.kind === "current" ? " loaded" : ""}`}
                        disabled={busy || busyId !== null || action.kind === "current" || !manifest}
                        title={
                          manifest
                            ? undefined
                            : "This VISTA cannot read this knowledge base's newest version."
                        }
                        onClick={() => onAction(kb)}
                      >
                        {busy ? "Importing…" : action.label}
                      </button>
                    </div>
                    <div className="hub-card-desc">{manifest?.description || "No description"}</div>
                    <div className="hub-card-meta">
                      <span className="hub-card-author">
                        <code>{kb.name}</code> · Version {kb.head_version}
                        {kb.published_by_name ? ` by ${kb.published_by_name}` : ""}
                      </span>
                      {manifest && (
                        <>
                          <span className="hub-card-meta-sep">·</span>
                          <span>
                            {manifest.files} {manifest.files === 1 ? "PDF" : "PDFs"} (
                            {formatSize(manifest.size)}), {formatSize(manifest.archive_size)} to
                            download
                          </span>
                          <span className="hub-card-meta-sep">·</span>
                          {manifest.has_index ? (
                            <span title={`${manifest.embedding_model} at ${manifest.embedding_revision}`}>
                              Indexed with {manifest.embedding_model}
                            </span>
                          ) : (
                            <span>Not indexed yet</span>
                          )}
                        </>
                      )}
                      {kb.local && kb.local.name !== kb.name && (
                        <>
                          <span className="hub-card-meta-sep">·</span>
                          <span>here as {kb.local.name}</span>
                        </>
                      )}
                      {kb.local?.modified && (
                        <>
                          <span className="hub-card-meta-sep">·</span>
                          <span>changed here</span>
                        </>
                      )}
                    </div>
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
                const { kb, slug } = renaming;
                setRenaming(null);
                void runImport(kb, { renameTo: slug.trim() });
              }}
            >
              <p style={{ margin: 0 }}>{renaming.reason}</p>
              <label className="project-modal-label">
                Import it as
                <input
                  className="input"
                  value={renaming.slug}
                  onChange={(e) => setRenaming({ ...renaming, slug: e.target.value })}
                  autoFocus
                />
              </label>
              {renaming.slug.trim() && renameError && (
                <div className="error" style={{ fontSize: 12 }}>
                  {renameError}
                </div>
              )}
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button type="button" className="button ghost" onClick={() => setRenaming(null)}>
                  Cancel
                </button>
                <button type="submit" className="button" disabled={renameError !== null}>
                  Import
                </button>
              </div>
            </form>
          )}

          {replacing && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }} role="alert">
              <p style={{ margin: 0 }}>
                {replacing.local?.name ?? replacing.name} has changes made here since version{" "}
                {replacing.local?.remote_version}. Importing version {replacing.head_version}{" "}
                replaces its PDFs and index here.
              </p>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button type="button" className="button ghost" onClick={() => setReplacing(null)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    const kb = replacing;
                    setReplacing(null);
                    void runImport(kb, { replaceModified: true });
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
