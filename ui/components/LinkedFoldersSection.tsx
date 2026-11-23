"use client";

import { useCallback, useEffect, useState } from "react";
import {
  LINK_WARNING,
  folderPicker,
  linkFolder,
  listLinkedFolders,
  renameLinkedFolder,
  unlinkFolder,
  type LinkedFolder,
} from "@/lib/linkedFolders";

/**
 * A project's linked folders in its settings (openspec change linked-folders): link
 * one (the system picker in the VISTA window, a typed path everywhere), rename it,
 * unlink it. Each change is saved at once, apart from the project's Save, and
 * reaches each conversation from its next message.
 */
export function LinkedFoldersSection({ projectName }: { projectName: string }) {
  const [links, setLinks] = useState<LinkedFolder[] | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState<{ link: string; name: string } | null>(null);
  const pick = folderPicker();

  const load = useCallback(async () => {
    try {
      setLinks(await listLinkedFolders(projectName));
    } catch (e) {
      setLinks([]);
      setError(e instanceof Error ? e.message : "Could not load the linked folders.");
    }
  }, [projectName]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(work: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setError("");
    try {
      await work();
      await load();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function link(path: string) {
    if (!path.trim()) return;
    if (await act(() => linkFolder(projectName, path.trim()))) setTyped("");
  }

  async function choose() {
    if (!pick) return;
    const path = await pick();
    if (path) await link(path);
  }

  async function rename() {
    if (!renaming) return;
    const { link: from, name } = renaming;
    if (!name.trim() || name.trim() === from) {
      setRenaming(null);
      return;
    }
    if (await act(() => renameLinkedFolder(projectName, from, name.trim()))) setRenaming(null);
  }

  return (
    <div className="project-modal-label" data-testid="linked-folders">
      <div>Linked folders</div>
      <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
        Folders on this computer the agent can read, read-only, at /mnt/linked/&lt;name&gt;.
        Changes apply to each conversation from its next message.
      </span>
      {links === null ? (
        <div style={{ fontSize: 12, color: "var(--muted)", fontWeight: 400 }}>Loading…</div>
      ) : links.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--muted)", fontWeight: 400 }}>No linked folders.</div>
      ) : (
        <ul className="linked-folders-list" aria-label="Linked folders">
          {links.map((l) => (
            <li key={l.name} className="linked-folder-row">
              <div style={{ minWidth: 0, flex: 1 }}>
                {renaming?.link === l.name ? (
                  <input
                    className="input"
                    aria-label={`New name for ${l.name}`}
                    value={renaming.name}
                    autoFocus
                    onChange={(e) => setRenaming({ link: l.name, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void rename();
                      if (e.key === "Escape") setRenaming(null);
                    }}
                  />
                ) : (
                  <div style={{ fontWeight: 600 }}>
                    {l.name} <span className="tag">{l.sandbox_path}</span>
                    {!l.available && (
                      <span className="tag" style={{ marginLeft: 4 }} title="Not mounted until it is back">
                        Unavailable: {l.reason}
                      </span>
                    )}
                  </div>
                )}
                <div
                  style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400, wordBreak: "break-all" }}
                >
                  {l.host_path}
                </div>
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                {renaming?.link === l.name ? (
                  <>
                    <button type="button" className="button button-xs" onClick={() => void rename()} disabled={busy}>
                      Save name
                    </button>
                    <button type="button" className="button ghost button-xs" onClick={() => setRenaming(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      className="button ghost button-xs"
                      onClick={() => setRenaming({ link: l.name, name: l.name })}
                      disabled={busy}
                      aria-label={`Rename ${l.name}`}
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      className="button ghost button-xs"
                      onClick={() => void act(() => unlinkFolder(projectName, l.name))}
                      disabled={busy}
                      aria-label={`Unlink ${l.name}`}
                    >
                      Unlink
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        {pick && (
          <button type="button" className="button ghost button-sm" onClick={() => void choose()} disabled={busy}>
            Link folder…
          </button>
        )}
        <input
          className="input"
          style={{ flex: 1, minWidth: 200 }}
          aria-label="Folder path to link"
          placeholder={pick ? "or type a folder's path" : "Folder path, e.g. /Users/you/Research/salt-data"}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void link(typed);
            }
          }}
        />
        <button
          type="button"
          className="button button-sm"
          onClick={() => void link(typed)}
          disabled={busy || !typed.trim()}
        >
          Link
        </button>
      </div>
      <span role="note" style={{ fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
        {LINK_WARNING}
      </span>
      {error && (
        <span className="error" role="alert" style={{ fontSize: 12, fontWeight: 400 }}>
          {error}
        </span>
      )}
    </div>
  );
}
