"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useActiveProject } from "@/lib/projects";
import { AppTopBar } from "@/components/AppTopBar";
import { ProjectRequired } from "@/components/ProjectRequired";
import FilePreview from "@/components/FilePreview";
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
  type TreeNode,
} from "@/lib/files-tree";
import { useOnChatRunFinished } from "@/lib/use-chat-run-finished";

type UploadResponse = {
  ok: boolean;
  saved?: string[];
  error?: string;
};

type Listing = Record<FileKind, ListedFile[]>;

const EMPTY: Listing = { uploads: [], outputs: [] };

/** The trees, top to bottom, with the sandbox path the agent sees each one at. */
const SECTIONS: { kind: FileKind; title: string; sandboxPath: string; empty: string }[] = [
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
  return value === "uploads" || value === "outputs";
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

  const loadListing = useCallback(async () => {
    if (!projectName) {
      setListing(EMPTY);
      return;
    }
    setIsLoading(true);
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
  }, [projectName, projectQuery]);

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

  const selectedFile = selected
    ? (shown[selected.kind].find((f) => f.name === selected.path) ?? null)
    : null;
  /** The example `file` is, as opposed to a real file of the same name. */
  function exampleFor(kind: FileKind, file: ListedFile) {
    const example = showExamples ? exampleAt(kind, file.name) : undefined;
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
    if (!selectedKind || !selectedPath) return;
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

  async function deleteFile(name: string, kind: FileKind) {
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

  function renderNode(kind: FileKind, node: TreeNode, depth: number) {
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
                  onClick={() => {
                    setUploadMessage((prev) => (prev.startsWith("Deleted ") ? "" : prev));
                    void loadListing();
                  }}
                  disabled={isLoading}
                >
                  {isLoading ? "Refreshing..." : "Refresh"}
                </button>
              </div>
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
                  ) : selected && loaded && !isLoading ? (
                    <div className="chat-bubble">
                      {selected.path} is not in this project&apos;s {selected.kind}.
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
