"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useActiveProject } from "@/lib/projects";
import { AppTopBar } from "@/components/AppTopBar";
import { ProjectRequired } from "@/components/ProjectRequired";
import FilePreview from "@/components/FilePreview";
import { LinkFolderControl } from "@/components/LinkFolderControl";
import { useTour } from "@/components/TourProvider";
import { EXAMPLE_FILES, exampleAt } from "@/lib/onboarding-examples";
import type { TourEvent } from "@/lib/onboarding";
import {
  ancestorFolders,
  buildTree,
  filesHref,
  readOpenFolders,
  shownInHref,
  writeOpenFolders,
  type FileKind,
  type ListedFile,
  type TreeKind,
  type TreeNode,
} from "@/lib/files-tree";
import {
  listLinkedEntries,
  listLinkedFolders,
  renameLinkedFolder,
  unlinkFolder,
  type LinkedEntry,
  type LinkedFolder,
} from "@/lib/linkedFolders";
import { useOnChatRunFinished } from "@/lib/use-chat-run-finished";

type UploadResponse = {
  ok: boolean;
  saved?: string[];
  error?: string;
};

type Listing = Record<TreeKind, ListedFile[]>;

/** A linked folder's directory as loaded, keyed `<link>/<dir>` (`<link>/` for its top). */
type LinkedDirs = Record<string, LinkedEntry[] | { error: string }>;

/** `<link>/<path>` split into the link and the path within it. */
function splitLinked(path: string): { link: string; rel: string } {
  const at = path.indexOf("/");
  return at < 0 ? { link: path, rel: "" } : { link: path.slice(0, at), rel: path.slice(at + 1) };
}

function dirKey(link: string, dir: string): string {
  return `${link}/${dir}`;
}

function parentDir(rel: string): string {
  const at = rel.lastIndexOf("/");
  return at < 0 ? "" : rel.slice(0, at);
}

const EMPTY: Listing = { uploads: [], outputs: [] };

/** The trees, top to bottom, with the sandbox path the agent sees each one at. */
const SECTIONS: { kind: TreeKind; title: string; sandboxPath: string; empty: string }[] = [
  {
    kind: "outputs",
    title: "Outputs",
    sandboxPath: "/mnt/data/output",
    empty: "No outputs yet. Files the agent writes to /mnt/data/output appear here.",
  },
  {
    kind: "uploads",
    title: "Uploads",
    sandboxPath: "/mnt/data/uploads",
    empty: "No uploaded files yet.",
  },
];

/** The tour's anchor on an example's row: the one its opening completes. */
function exampleAnchor(opened: TourEvent | undefined): string | undefined {
  if (opened === "example-image-opened") return "files:open-chart";
  if (opened === "example-md-opened") return "files:open-report";
  return undefined;
}

/** Encode a (possibly nested) relative path per segment, keeping `/` literal. */
function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function isKind(value: string | null): value is FileKind {
  return value === "uploads" || value === "outputs" || value === "linked";
}

function formatTimestamp(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
}

export default function FilesPage() {
  // useSearchParams opts the page out of static prerendering up to the nearest
  // Suspense boundary, so the part that reads the URL sits inside one.
  return (
    <Suspense fallback={<div className="app-page" />}>
      <FilesPageInner />
    </Suspense>
  );
}

function FilesPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;
  const [listing, setListing] = useState<Listing>(EMPTY);
  const [links, setLinks] = useState<LinkedFolder[]>([]);
  /** Whether Link folder's panel, under the toolbar, is open. */
  const [linking, setLinking] = useState(false);
  /** The link being renamed, and the name typed so far. */
  const [renaming, setRenaming] = useState<{ link: string; name: string } | null>(null);
  const [linkBusy, setLinkBusy] = useState("");
  const [linkError, setLinkError] = useState("");
  const [linkedDirs, setLinkedDirs] = useState<LinkedDirs>({});
  /** Directories being fetched, so each is asked for once. */
  const loadingDirs = useRef<Set<string>>(new Set());
  const [loaded, setLoaded] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadMessage, setUploadMessage] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [deleting, setDeleting] = useState("");
  /** Open folders, as `kind:path`. Clicks flip one; a selection opens the folders above it. */
  const [openFolders, setOpenFolders] = useState<ReadonlySet<string>>(new Set());
  /** The selection whose folders were last opened, so each selection opens them once. */
  const [openedFor, setOpenedFor] = useState("");
  /** The project whose remembered folders `openFolders` was loaded from. */
  const [foldersFor, setFoldersFor] = useState<string | null>(null);
  const projectQuery = projectName
    ? `?project_name=${encodeURIComponent(projectName)}`
    : "";

  const kindParam = searchParams.get("kind");
  const pathParam = searchParams.get("path");
  const selected =
    isKind(kindParam) && pathParam ? { kind: kindParam, path: pathParam } : null;

  const loadLinks = useCallback(async () => {
    if (!projectName) return;
    try {
      setLinks(await listLinkedFolders(projectName));
    } catch {
      setLinks([]);
    }
  }, [projectName]);

  const loadListing = useCallback(async () => {
    if (!projectName) {
      setListing(EMPTY);
      return;
    }
    setIsLoading(true);
    // Linked folders are listed a directory at a time: forgetting the loaded ones
    // makes the open ones load again.
    loadingDirs.current = new Set();
    setLinkedDirs({});
    void loadLinks();
    try {
      const [uploadsRes, outputsRes] = await Promise.all([
        fetch(`/api/files/uploads${projectQuery}`),
        fetch(`/api/files/outputs${projectQuery}`),
      ]);
      const [uploadsData, outputsData] = await Promise.all([
        uploadsRes.json(),
        outputsRes.json(),
      ]);
      setListing({
        uploads: Array.isArray(uploadsData) ? (uploadsData as ListedFile[]) : [],
        outputs: Array.isArray(outputsData) ? (outputsData as ListedFile[]) : [],
      });
    } catch {
      setListing(EMPTY);
    } finally {
      setIsLoading(false);
      setLoaded(true);
    }
  }, [projectName, projectQuery, loadLinks]);

  useEffect(() => {
    void loadListing();
  }, [loadListing]);

  // A turn that just finished may have written or displayed files.
  useOnChatRunFinished(projectName, () => void loadListing());

  // While the tour's files step is up, its example files join the listing.
  // They are made here and never sent anywhere; a real file of the same name wins.
  const tour = useTour();
  const showExamples = tour.position?.step === "files";
  const reportTour = tour.report;
  const shown = useMemo<Listing>(() => {
    // After the real listing, so a real file of the same name is known by then.
    if (!showExamples || !loaded) return listing;
    const withExamples: Listing = { uploads: [...listing.uploads], outputs: [...listing.outputs] };
    for (const { kind, file } of EXAMPLE_FILES) {
      if (!listing[kind].some((f) => f.name === file.name)) withExamples[kind].push(file);
    }
    return withExamples;
  }, [listing, showExamples, loaded]);

  const trees = useMemo(
    () => ({
      uploads: buildTree(shown.uploads),
      outputs: buildTree(shown.outputs),
    }),
    [shown],
  );

  /** A linked file as the preview wants it, from its directory's listing once loaded. */
  function linkedFile(path: string): ListedFile | null | undefined {
    const { link, rel } = splitLinked(path);
    const dir = linkedDirs[dirKey(link, parentDir(rel))];
    if (dir === undefined) return undefined;
    if (!Array.isArray(dir)) return null;
    const entry = dir.find((e) => e.path === rel && e.type === "file");
    return entry
      ? { name: path, size: entry.size ?? 0, modifiedAt: entry.modified, shown_in: entry.shown_in ?? null }
      : null;
  }
  const selectedLinked = selected?.kind === "linked" ? linkedFile(selected.path) : undefined;
  const selectedFile = selected
    ? selected.kind === "linked"
      ? (selectedLinked ?? null)
      : (shown[selected.kind].find((f) => f.name === selected.path) ?? null)
    : null;
  /** The example `file` is, as opposed to a real file of the same name. */
  function exampleFor(kind: FileKind, file: ListedFile) {
    const example = showExamples && kind !== "linked" ? exampleAt(kind, file.name) : undefined;
    return example?.file === file ? example : undefined;
  }
  const selectedExample = selected && selectedFile ? exampleFor(selected.kind, selectedFile) : undefined;
  const selectedExampleEvent = selectedExample?.opened;
  useEffect(() => {
    if (selectedExampleEvent) reportTour(selectedExampleEvent);
  }, [selectedExampleEvent, reportTour]);
  // The examples go when their step does. One still selected would leave the
  // preview saying a file is missing, so the selection goes with it.
  const selectedKind = selected?.kind;
  const selectedPath = selected?.path;
  const examplesWereShown = useRef(false);
  useEffect(() => {
    if (showExamples) {
      examplesWereShown.current = true;
      return;
    }
    if (!examplesWereShown.current) return;
    examplesWereShown.current = false;
    if (!selectedKind || !selectedPath || selectedKind === "linked") return;
    const isReal = listing[selectedKind].some((f) => f.name === selectedPath);
    if (!isReal && exampleAt(selectedKind, selectedPath)) router.replace("/files");
  }, [showExamples, selectedKind, selectedPath, listing, router]);
  // A new selection (a click or the URL) opens the folders above it and closes
  // none: folders the researcher opened stay open. Adjusted while rendering
  // rather than in an effect, so the tree never paints with them shut.
  const ancestorsOfSelection = selected
    ? ancestorFolders(selected.path).map((p) => `${selected.kind}:${p}`)
    : [];
  // Coming back to Files (or switching project) reopens the folders that were open
  // last time this session, plus any above the selection.
  if (projectName && projectName !== foldersFor) {
    setFoldersFor(projectName);
    setOpenFolders(new Set([...readOpenFolders(projectName), ...ancestorsOfSelection]));
  }
  const selectionKey = selected ? `${selected.kind}:${selected.path}` : "";
  if (selectionKey !== openedFor) {
    setOpenedFor(selectionKey);
    if (selected) {
      setOpenFolders((prev) => new Set([...prev, ...ancestorsOfSelection]));
    }
  }

  useEffect(() => {
    if (projectName && foldersFor === projectName) writeOpenFolders(projectName, openFolders);
  }, [projectName, foldersFor, openFolders]);

  // Each open linked folder's directory is fetched once, when it is first open.
  useEffect(() => {
    if (!projectName) return;
    const available = new Set(links.filter((l) => l.available).map((l) => l.name));
    for (const key of openFolders) {
      if (!key.startsWith("linked:")) continue;
      const { link, rel } = splitLinked(key.slice("linked:".length));
      const id = dirKey(link, rel);
      if (!available.has(link) || id in linkedDirs || loadingDirs.current.has(id)) continue;
      loadingDirs.current.add(id);
      listLinkedEntries(projectName, link, rel)
        .then((entries) => setLinkedDirs((prev) => ({ ...prev, [id]: entries })))
        .catch((e: unknown) =>
          setLinkedDirs((prev) => ({
            ...prev,
            [id]: { error: e instanceof Error ? e.message : "Could not list this folder." },
          })),
        );
    }
  }, [projectName, links, openFolders, linkedDirs]);

  function isOpen(kind: FileKind, path: string): boolean {
    return openFolders.has(`${kind}:${path}`);
  }

  function toggleFolder(kind: FileKind, path: string) {
    const key = `${kind}:${path}`;
    setOpenFolders((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function select(kind: FileKind, path: string) {
    // Replace, not push: stepping through files should not fill the back button.
    router.replace(filesHref(kind, path));
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    if (!projectName) {
      setUploadError("Select a project before uploading.");
      return;
    }
    setIsUploading(true);
    setUploadError("");
    setUploadMessage("");
    try {
      const form = new FormData();
      for (const file of Array.from(files)) {
        form.append("files", file, file.webkitRelativePath || file.name);
      }
      const response = await fetch(`/api/files/uploads${projectQuery}`, { method: "POST", body: form });
      const data = (await response.json()) as UploadResponse;
      if (!response.ok || !data.ok) {
        setUploadError(data.error || "Upload failed.");
        return;
      }
      const savedCount = Array.isArray(data.saved) ? data.saved.length : 0;
      setUploadMessage(savedCount > 0 ? `${savedCount} file(s) uploaded.` : "Upload complete.");
      await loadListing();
    } catch {
      setUploadError("Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  async function deleteFile(name: string, kind: TreeKind) {
    if (!projectName) {
      setUploadError("Select a project before deleting files.");
      return;
    }
    setDeleting(`${kind}:${name}`);
    setUploadError("");
    setUploadMessage("");
    try {
      const response = await fetch(
        `/api/files/${kind}/${encodePath(name)}${projectQuery}`,
        { method: "DELETE" },
      );
      const data = (await response.json()) as UploadResponse;
      if (!response.ok || !data.ok) {
        setUploadError(data.error || "Delete failed.");
        return;
      }
      setUploadMessage(`Deleted ${name}.`);
      if (selected && selected.kind === kind && selected.path === name) {
        router.replace("/files");
      }
      await loadListing();
    } catch {
      setUploadError("Delete failed.");
    } finally {
      setDeleting("");
    }
  }

  /** A linked folder's directory, a level at a time: read-only, no delete or rename. */
  function renderLinkedDir(link: string, dir: string, depth: number) {
    const loaded = linkedDirs[dirKey(link, dir)];
    const indent = { paddingLeft: 8 + depth * 16 };
    if (loaded === undefined) {
      return (
        <li className="muted" style={{ ...indent, fontSize: 12 }}>
          Loading…
        </li>
      );
    }
    if (!Array.isArray(loaded)) {
      return (
        <li className="error" style={{ ...indent, fontSize: 12 }}>
          {loaded.error}
        </li>
      );
    }
    if (loaded.length === 0) {
      return (
        <li className="muted" style={{ ...indent, fontSize: 12 }}>
          Empty folder.
        </li>
      );
    }
    return loaded.map((entry) => {
      const path = `${link}/${entry.path}`;
      if (entry.type === "folder") {
        const open = isOpen("linked", path);
        return (
          <li key={`d:${path}`}>
            <button
              type="button"
              className="files-folder"
              style={indent}
              aria-expanded={open}
              onClick={() => toggleFolder("linked", path)}
            >
              <span className="files-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
              {entry.name}
            </button>
            {open && <ul className="files-children">{renderLinkedDir(link, entry.path, depth + 1)}</ul>}
          </li>
        );
      }
      const isSelected = selected?.kind === "linked" && selected.path === path;
      return (
        <li key={`f:${path}`}>
          <div className={`files-row ${isSelected ? "selected" : ""}`}>
            <button
              type="button"
              className="files-file"
              style={indent}
              aria-current={isSelected ? "true" : undefined}
              onClick={() => select("linked", path)}
            >
              <span className="files-file-name">{entry.name}</span>
              <span className="files-file-meta">
                {((entry.size ?? 0) / 1024).toFixed(1)} KB · {formatTimestamp(entry.modified)}
              </span>
            </button>
            <div className="files-row-actions">
              {entry.shown_in && (
                <Link
                  className="button ghost button-xs"
                  href={shownInHref(entry.shown_in)}
                  title={`Shown in ${entry.shown_in.chat_title}`}
                >
                  Show in chat
                </Link>
              )}
              <a
                className="button ghost button-xs"
                href={`/api/files/linked/${encodePath(path)}${projectQuery}`}
                download={entry.name}
                aria-label={`Download ${path}`}
              >
                Download
              </a>
            </div>
          </div>
        </li>
      );
    });
  }

  async function renameLink() {
    if (!projectName || !renaming) return;
    const { link, name } = renaming;
    if (!name.trim() || name.trim() === link) {
      setRenaming(null);
      return;
    }
    setLinkBusy(link);
    setLinkError("");
    try {
      await renameLinkedFolder(projectName, link, name.trim());
      setRenaming(null);
      if (selected?.kind === "linked" && selected.path.startsWith(`${link}/`)) router.replace("/files");
      await loadLinks();
    } catch (e) {
      setLinkError(e instanceof Error ? e.message : "The folder was not renamed.");
    } finally {
      setLinkBusy("");
    }
  }

  async function removeLink(link: string) {
    if (!projectName) return;
    setLinkBusy(link);
    setLinkError("");
    try {
      await unlinkFolder(projectName, link);
      if (selected?.kind === "linked" && selected.path.startsWith(`${link}/`)) router.replace("/files");
      await loadLinks();
    } catch (e) {
      setLinkError(e instanceof Error ? e.message : "The folder was not unlinked.");
    } finally {
      setLinkBusy("");
    }
  }

  function renderNode(kind: TreeKind, node: TreeNode, depth: number) {
    const indent = { paddingLeft: 8 + depth * 16 };
    if (node.type === "folder") {
      const open = isOpen(kind, node.path);
      return (
        <li key={`d:${node.path}`}>
          <button
            type="button"
            className="files-folder"
            style={indent}
            aria-expanded={open}
            onClick={() => toggleFolder(kind, node.path)}
          >
            <span className="files-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
            {node.name}
          </button>
          {open && (
            <ul className="files-children">
              {node.children.map((child) => renderNode(kind, child, depth + 1))}
            </ul>
          )}
        </li>
      );
    }
    const file = node.file;
    const isSelected = selected?.kind === kind && selected.path === node.path;
    const busy = deleting === `${kind}:${node.path}`;
    const downloadHref = `/api/files/${kind}/${encodePath(node.path)}${projectQuery}`;
    if (exampleFor(kind, file)) {
      // An example is not in the project: nothing to download, delete or show.
      return (
        <li key={`f:${node.path}`}>
          <div
            className={`files-row ${isSelected ? "selected" : ""}`}
            data-tour={exampleAnchor(exampleFor(kind, file)?.opened)}
          >
            <button
              type="button"
              className="files-file"
              style={indent}
              aria-current={isSelected ? "true" : undefined}
              onClick={() => select(kind, node.path)}
            >
              <span className="files-file-name">{node.name}</span>
              <span className="files-file-meta">{(file.size / 1024).toFixed(1)} KB</span>
            </button>
            <div className="files-row-actions">
              <span className="tag">Example</span>
            </div>
          </div>
        </li>
      );
    }
    return (
      <li key={`f:${node.path}`}>
        <div className={`files-row ${isSelected ? "selected" : ""}`}>
          <button
            type="button"
            className="files-file"
            style={indent}
            aria-current={isSelected ? "true" : undefined}
            onClick={() => select(kind, node.path)}
          >
            <span className="files-file-name">{node.name}</span>
            <span className="files-file-meta">
              {(file.size / 1024).toFixed(1)} KB · {formatTimestamp(file.modifiedAt)}
            </span>
          </button>
          <div className="files-row-actions">
            {/* Chat titles don't tell conversations apart well yet, so this names none. */}
            {file.shown_in && (
              <Link
                className="button ghost button-xs"
                href={shownInHref(file.shown_in)}
                title={`Shown in ${file.shown_in.chat_title}`}
              >
                Show in chat
              </Link>
            )}
            <a
              className="button ghost button-xs"
              href={downloadHref}
              download={node.name}
              aria-label={`Download ${node.path}`}
            >
              Download
            </a>
            <button
              type="button"
              className="button ghost button-xs"
              onClick={() => void deleteFile(node.path, kind)}
              disabled={busy}
              aria-label={`Delete ${node.path}`}
            >
              {busy ? "Deleting..." : "Delete"}
            </button>
          </div>
        </div>
      </li>
    );
  }

  return (
    <div className="app-page">
      <AppTopBar title="Files" />
      <div className="app-page-body">
        {!projectName ? (
          <ProjectRequired what="Files" />
        ) : (
          <section className="panel files-page">
            <div className="panel-body">
              <div className="files-toolbar">
                <button
                  className="button button-sm"
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={isUploading}
                >
                  {isUploading ? "Uploading..." : "Upload +"}
                </button>
                <button
                  className="button ghost button-sm"
                  onClick={() => setLinking((open) => !open)}
                  aria-expanded={linking}
                >
                  Link folder…
                </button>
                <button
                  className="button ghost button-sm"
                  onClick={() => {
                    setUploadMessage((prev) => (prev.startsWith("Deleted ") ? "" : prev));
                    void loadListing();
                  }}
                  disabled={isLoading}
                >
                  {isLoading ? "Refreshing..." : "Refresh"}
                </button>
              </div>
              {linking && (
                <div className="files-link-panel" data-testid="files-link-panel">
                  <LinkFolderControl
                    projectName={projectName}
                    onLinked={async (made) => {
                      await loadLinks();
                      setLinking(false);
                      if (made.available) toggleFolder("linked", made.name);
                    }}
                  />
                </div>
              )}
              <div
                className={`dropzone files-dropzone ${isDragOver ? "active" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDragOver(true);
                }}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setIsDragOver(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setIsDragOver(false);
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsDragOver(false);
                  void uploadFiles(event.dataTransfer.files);
                }}
                onClick={() => uploadInputRef.current?.click()}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    uploadInputRef.current?.click();
                  }
                }}
              >
                Drag and drop files here to upload them, or click to browse.
              </div>
              <input
                ref={uploadInputRef}
                type="file"
                multiple
                style={{ display: "none" }}
                onChange={(event) => {
                  void uploadFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />

              {uploadMessage && (
                <div className="chat-bubble tool" style={{ marginBottom: 8 }}>
                  {uploadMessage}
                </div>
              )}
              {uploadError && (
                <div className="chat-bubble" style={{ marginBottom: 8 }}>
                  <div className="error">{uploadError}</div>
                </div>
              )}

              <div className="files-layout">
                <div className="files-trees">
                  {SECTIONS.map(({ kind, title, sandboxPath, empty }) => (
                    <div key={kind} className="files-section" data-tour={`files:${kind}`}>
                      <div className="files-section-head">
                        <h2 className="files-section-title">{title}</h2>
                        <span className="tag">{sandboxPath}</span>
                      </div>
                      {trees[kind].length === 0 ? (
                        <div className="chat-bubble">{isLoading && !loaded ? "Loading files..." : empty}</div>
                      ) : (
                        <ul className="files-tree" aria-label={title}>
                          {trees[kind].map((node) => renderNode(kind, node, 0))}
                        </ul>
                      )}
                    </div>
                  ))}
                  <div className="files-section" data-testid="files-linked">
                    <div className="files-section-head">
                      <h2 className="files-section-title">Linked</h2>
                      <span className="tag">/mnt/linked</span>
                    </div>
                    <span className="muted" style={{ fontSize: 11 }}>
                      Folders on this computer the agent reads, read-only. Linking, renaming and unlinking
                      apply to each conversation from its next message; unlinking leaves the folder as it is.
                    </span>
                    {linkError && (
                      <div className="error" role="alert" style={{ fontSize: 12 }}>
                        {linkError}
                      </div>
                    )}
                    {links.length === 0 ? (
                      <div className="chat-bubble">
                        No linked folders yet. Link folder&hellip;, above, links one from this computer.
                      </div>
                    ) : (
                      <ul className="files-tree" aria-label="Linked">
                        {links.map((l) => {
                          const open = l.available && isOpen("linked", l.name);
                          return (
                            <li key={`l:${l.name}`}>
                              <div className="files-row">
                                {renaming?.link === l.name ? (
                                  <input
                                    className="input"
                                    style={{ flex: 1, marginLeft: 8 }}
                                    aria-label={`New name for ${l.name}`}
                                    value={renaming.name}
                                    autoFocus
                                    onChange={(e) => setRenaming({ link: l.name, name: e.target.value })}
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter") void renameLink();
                                      if (e.key === "Escape") setRenaming(null);
                                    }}
                                  />
                                ) : (
                                  <button
                                    type="button"
                                    className="files-folder"
                                    style={{ paddingLeft: 8 }}
                                    aria-expanded={l.available ? open : undefined}
                                    disabled={!l.available}
                                    onClick={() => toggleFolder("linked", l.name)}
                                    title={l.host_path}
                                  >
                                    <span className="files-caret" aria-hidden="true">
                                      {l.available ? (open ? "▾" : "▸") : "·"}
                                    </span>
                                    {l.name} <span className="tag">{l.sandbox_path}</span>
                                    {!l.available && <span className="tag">Unavailable: {l.reason}</span>}
                                  </button>
                                )}
                                <div className="files-row-actions">
                                  {renaming?.link === l.name ? (
                                    <>
                                      <button
                                        type="button"
                                        className="button button-xs"
                                        onClick={() => void renameLink()}
                                        disabled={linkBusy === l.name}
                                      >
                                        Save name
                                      </button>
                                      <button
                                        type="button"
                                        className="button ghost button-xs"
                                        onClick={() => setRenaming(null)}
                                      >
                                        Cancel
                                      </button>
                                    </>
                                  ) : (
                                    <>
                                      <button
                                        type="button"
                                        className="button ghost button-xs"
                                        onClick={() => setRenaming({ link: l.name, name: l.name })}
                                        disabled={linkBusy === l.name}
                                        aria-label={`Rename ${l.name}`}
                                      >
                                        Rename
                                      </button>
                                      <button
                                        type="button"
                                        className="button ghost button-xs"
                                        onClick={() => void removeLink(l.name)}
                                        disabled={linkBusy === l.name}
                                        aria-label={`Unlink ${l.name}`}
                                        title="Stop the agent reading this folder; its files stay as they are"
                                      >
                                        {linkBusy === l.name ? "Unlinking…" : "Unlink"}
                                      </button>
                                    </>
                                  )}
                                </div>
                              </div>
                              <div className="muted" style={{ fontSize: 11, paddingLeft: 24, wordBreak: "break-all" }}>
                                {l.host_path}
                              </div>
                              {open && <ul className="files-children">{renderLinkedDir(l.name, "", 1)}</ul>}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                </div>
                <div className="files-preview-pane" data-tour="files:preview">
                  {selected && selectedFile ? (
                    <FilePreview
                      key={`${selected.kind}:${selected.path}:${selectedFile.modifiedAt}`}
                      kind={selected.kind}
                      path={selected.path}
                      name={selected.path.split("/").pop() ?? selected.path}
                      size={selectedFile.size}
                      projectName={projectName}
                      version={selectedFile.modifiedAt}
                      src={selectedExample?.src}
                    />
                  ) : selected && loaded && !isLoading && (selected.kind !== "linked" || selectedLinked === null) ? (
                    <div className="chat-bubble">
                      {selected.kind === "linked"
                        ? `${selected.path} is not in this project's linked folders.`
                        : `${selected.path} is not in this project's ${selected.kind}.`}
                    </div>
                  ) : (
                    <div className="files-preview-empty muted">
                      {selected ? "Loading..." : "Select a file to preview it."}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
