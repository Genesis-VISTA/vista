import { useState } from "react";
import { LINK_WARNING, folderPicker, linkFolder, type LinkedFolder } from "@/lib/linkedFolders";

/**
 * Link a folder to a project (openspec change linked-folders): the system folder
 * picker in the VISTA window, a typed path everywhere, and the warning about what
 * linking grants. Used by the project's settings and the Files page alike.
 */
export function LinkFolderControl({
  projectName,
  onLinked,
}: {
  projectName: string;
  onLinked: (link: LinkedFolder) => void | Promise<void>;
}) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pick = folderPicker();

  async function link(path: string) {
    if (!path.trim()) return;
    setBusy(true);
    setError("");
    try {
      const made = await linkFolder(projectName, path.trim());
      setTyped("");
      await onLinked(made);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The folder was not linked.");
    } finally {
      setBusy(false);
    }
  }

  async function choose() {
    if (!pick) return;
    const path = await pick();
    if (path) await link(path);
  }

  return (
    <div className="link-folder-control">
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
      <span role="note" style={{ display: "block", fontSize: 11, color: "var(--muted)", fontWeight: 400 }}>
        {LINK_WARNING}
      </span>
      {error && (
        <span className="error" role="alert" style={{ display: "block", fontSize: 12, fontWeight: 400 }}>
          {error}
        </span>
      )}
    </div>
  );
}
