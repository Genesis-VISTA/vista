"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type UploadFileInfo = {
  name: string;
  size: number;
  modifiedAt: string;
  source?: "upload" | "generated";
};

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
  const [uploads, setUploads] = useState<UploadFileInfo[]>([]);
  const [isLoadingUploads, setIsLoadingUploads] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadMessage, setUploadMessage] = useState("");
  const [isDragOverUploads, setIsDragOverUploads] = useState(false);
  const [deletingUploadName, setDeletingUploadName] = useState("");
  const [dataModel, setDataModel] = useState(PLACEHOLDER_DATASETS[0]);

  const uploadedFiles = useMemo(
    () => uploads.filter((file) => file.source !== "generated"),
    [uploads]
  );
  const tritiumResultFiles = useMemo(
    () => uploads.filter((file) => file.source === "generated"),
    [uploads]
  );

  const loadUploads = useCallback(async () => {
    setIsLoadingUploads(true);
    try {
      const response = await fetch("/api/uploads");
      const data = (await response.json()) as UploadFileInfo[];
      setUploads(Array.isArray(data) ? data : []);
    } catch {
      setUploads([]);
    } finally {
      setIsLoadingUploads(false);
    }
  }, []);

  useEffect(() => {
    void loadUploads();
  }, [loadUploads]);

  async function refreshUploads() {
    await loadUploads();
    setUploadMessage((prev) => (prev.startsWith("Deleted ") ? "" : prev));
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setIsUploading(true);
    setUploadError("");
    setUploadMessage("");
    try {
      const form = new FormData();
      for (const file of Array.from(files)) form.append("files", file);
      const response = await fetch("/api/uploads", { method: "POST", body: form });
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

  async function deleteUpload(name: string) {
    setDeletingUploadName(name);
    setUploadError("");
    setUploadMessage("");
    try {
      const response = await fetch(`/api/uploads/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
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
                          href={`/api/uploads/${encodeURIComponent(file.name)}`}
                          download={file.name}
                        >
                          Download
                        </a>
                        <button
                          className="button ghost button-xs upload-action-btn"
                          onClick={() => void deleteUpload(file.name)}
                          disabled={deletingUploadName === file.name}
                        >
                          {deletingUploadName === file.name ? "Deleting..." : "Delete"}
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
                          href={`/api/uploads/${encodeURIComponent(file.name)}`}
                          download={file.name}
                        >
                          Download
                        </a>
                        <button
                          className="button ghost button-xs upload-action-btn"
                          onClick={() => void deleteUpload(file.name)}
                          disabled={deletingUploadName === file.name}
                        >
                          {deletingUploadName === file.name ? "Deleting..." : "Delete"}
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
