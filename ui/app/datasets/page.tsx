"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useActiveProject } from "@/lib/projects";

type FileKind = "uploads" | "outputs";

type UploadFileInfo = {
  name: string;
  size: number;
  modifiedAt: string;
  source?: "upload" | "generated";
};

function kindForSource(source: UploadFileInfo["source"]): FileKind {
  return source === "generated" ? "outputs" : "uploads";
}

/** Encode a (possibly nested) relative path per segment, keeping `/` literal. */
function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

type UploadResponse = {
  ok: boolean;
  saved?: string[];
  error?: string;
};

const PLACEHOLDER_DATASETS = [
  "allenai/scientific_papers",
  "OpenDFM/ScienceQA",
  "bigbio/pubmed_qa",
];

function formatTimestampUtc(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
}

export default function DatasetsPage() {
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const activeProject = useActiveProject();
  const projectName = activeProject?.name ?? null;
  const [uploads, setUploads] = useState<UploadFileInfo[]>([]);
  const [isLoadingUploads, setIsLoadingUploads] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadMessage, setUploadMessage] = useState("");
  const [isDragOverUploads, setIsDragOverUploads] = useState(false);
  const [deletingUploadName, setDeletingUploadName] = useState("");
  const [dataModel, setDataModel] = useState(PLACEHOLDER_DATASETS[0]);
  const projectQuery = projectName
    ? `?project_name=${encodeURIComponent(projectName)}`
    : "";

  const uploadedFiles = useMemo(
    () => uploads.filter((file) => file.source !== "generated"),
    [uploads]
  );
  const tritiumResultFiles = useMemo(
    () => uploads.filter((file) => file.source === "generated"),
    [uploads]
  );

  const loadUploads = useCallback(async () => {
    if (!projectName) {
      setUploads([]);
      return;
    }
    setIsLoadingUploads(true);
    try {
      const [uploadsRes, outputsRes] = await Promise.all([
        fetch(`/api/files/uploads${projectQuery}`),
        fetch(`/api/files/outputs${projectQuery}`),
      ]);
      const [uploadsData, outputsData] = await Promise.all([
        uploadsRes.json(),
        outputsRes.json(),
      ]);
      setUploads([
        ...(Array.isArray(uploadsData) ? (uploadsData as UploadFileInfo[]) : []),
        ...(Array.isArray(outputsData) ? (outputsData as UploadFileInfo[]) : []),
      ]);
    } catch {
      setUploads([]);
    } finally {
      setIsLoadingUploads(false);
    }
  }, [projectName, projectQuery]);

  useEffect(() => {
    void loadUploads();
  }, [loadUploads]);

  async function refreshUploads() {
    await loadUploads();
    setUploadMessage((prev) => (prev.startsWith("Deleted ") ? "" : prev));
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
      await loadUploads();
    } catch {
      setUploadError("Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  async function deleteUpload(name: string, kind: FileKind) {
    if (!projectName) {
      setUploadError("Select a project before deleting uploads.");
      return;
    }
    setDeletingUploadName(`${kind}:${name}`);
    setUploadError("");
    setUploadMessage("");
    try {
      const response = await fetch(
        `/api/files/${kind}/${encodePath(name)}${projectQuery}`,
        { method: "DELETE" }
      );
      const data = (await response.json()) as UploadResponse;
      if (!response.ok || !data.ok) {
        setUploadError(data.error || "Delete failed.");
        return;
      }
      setUploadMessage(`Deleted ${name}.`);
      await loadUploads();
    } catch {
      setUploadError("Delete failed.");
    } finally {
      setDeletingUploadName("");
    }
  }

  return (
    <div className="standalone-page">
      <section className="panel" style={{ height: "100%" }}>
        <div className="panel-header">
          <div className="panel-title">Data</div>
          <span className="tag">/mnt/data/uploads</span>
        </div>
        <div className="panel-body">
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
            <button
              className="button button-sm"
              onClick={() => uploadInputRef.current?.click()}
              disabled={isUploading}
            >
              {isUploading ? "Uploading..." : "Upload +"}
            </button>
            <button
              className="button ghost button-sm"
              onClick={() => void refreshUploads()}
              disabled={isLoadingUploads}
            >
              {isLoadingUploads ? "Refreshing..." : "Refresh"}
            </button>
          </div>
          <div className="catalog-row">
            <label className="model-label catalog-label" htmlFor="data-model-select">
              AmSC Data Catalog
            </label>
            <select
              id="data-model-select"
              className="input model-select model-select-full"
              value={dataModel}
              onChange={(event) => setDataModel(event.target.value)}
            >
              {PLACEHOLDER_DATASETS.map((dataset) => (
                <option key={dataset} value={dataset}>
                  {dataset}
                </option>
              ))}
            </select>
          </div>
          <div
            className={`dropzone ${isDragOverUploads ? "active" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragOverUploads(true);
            }}
            onDragEnter={(event) => {
              event.preventDefault();
              setIsDragOverUploads(true);
            }}
            onDragLeave={(event) => {
              event.preventDefault();
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setIsDragOverUploads(false);
              }
            }}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragOverUploads(false);
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
            Drag and drop files here, or click to browse.
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

          <div className="upload-list">
            {uploads.length === 0 && (
              <div className="chat-bubble">
                {isLoadingUploads ? "Loading uploads..." : "No uploaded files yet."}
              </div>
            )}
            {uploads.length > 0 && (
              <>
                <div className="upload-section">
                  <div className="upload-section-title">Uploads</div>
                  {uploadedFiles.length === 0 && (
                    <div className="chat-bubble">No uploaded files yet.</div>
                  )}
                  {uploadedFiles.map((file) => (
                    <div key={file.name} className="upload-item">
                      <div className="upload-name">{file.name}</div>
                      <div className="upload-meta">
                        {(file.size / 1024).toFixed(1)} KB - {formatTimestampUtc(file.modifiedAt)}
                      </div>
                      <div className="upload-actions">
                        <a
                          className="button ghost button-xs upload-action-btn"
                          href={`/api/files/${kindForSource(file.source)}/${encodePath(file.name)}${projectQuery}`}
                          download={file.name}
                        >
                          Download
                        </a>
                        <button
                          className="button ghost button-xs upload-action-btn"
                          onClick={() => void deleteUpload(file.name, kindForSource(file.source))}
                          disabled={deletingUploadName === `${kindForSource(file.source)}:${file.name}`}
                        >
                          {deletingUploadName === `${kindForSource(file.source)}:${file.name}` ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="upload-section">
                  <div className="upload-section-title">Tritium Breeding Results</div>
                  {tritiumResultFiles.length === 0 && (
                    <div className="chat-bubble">No tritium breeding results yet.</div>
                  )}
                  {tritiumResultFiles.map((file) => (
                    <div key={file.name} className="upload-item">
                      <div className="upload-name">{file.name}</div>
                      <div className="upload-meta">
                        {(file.size / 1024).toFixed(1)} KB - {formatTimestampUtc(file.modifiedAt)}
                      </div>
                      <div className="upload-actions">
                        <a
                          className="button ghost button-xs upload-action-btn"
                          href={`/api/files/${kindForSource(file.source)}/${encodePath(file.name)}${projectQuery}`}
                          download={file.name}
                        >
                          Download
                        </a>
                        <button
                          className="button ghost button-xs upload-action-btn"
                          onClick={() => void deleteUpload(file.name, kindForSource(file.source))}
                          disabled={deletingUploadName === `${kindForSource(file.source)}:${file.name}`}
                        >
                          {deletingUploadName === `${kindForSource(file.source)}:${file.name}` ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
