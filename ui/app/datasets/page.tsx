"use client";

// Datasets page — lists uploaded files for the current project session.

import { useEffect, useRef, useState } from "react";
import { deleteUpload } from "@/app/actions/uploads";
import type { UploadedFile } from "@/lib/types";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export default function DatasetsPage() {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  async function refresh() {
    try {
      const res = await fetch("/api/uploads", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFiles(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  useEffect(() => {
    fetch("/api/uploads", { cache: "no-store" })
      .then((res) => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.json() as Promise<UploadedFile[]>; })
      .then((data) => { setFiles(data); setError(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  async function upload(input: HTMLInputElement) {
    if (!input.files || input.files.length === 0) return;
    setUploading(true);
    try {
      const fd = new FormData();
      for (const f of Array.from(input.files)) fd.append("files", f);
      const res = await fetch("/api/uploads", { method: "POST", body: fd });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      input.value = "";
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function remove(name: string) {
    if (!window.confirm(`Delete "${name}"?`)) return;
    try {
      await deleteUpload(name);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-semibold">Datasets</h1>
          <p className="text-sm text-gray-600">Files uploaded to the agent sandbox.</p>
        </div>
        <label className="rounded-md bg-blue-600 text-white px-3 py-1.5 text-sm hover:bg-blue-700 cursor-pointer">
          {uploading ? "Uploading…" : "Upload"}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            disabled={uploading}
            onChange={(e) => upload(e.currentTarget)}
          />
        </label>
      </div>

      {error && <div className="mb-4 rounded bg-rose-50 text-rose-700 p-3 text-sm">{error}</div>}

      {files.length === 0 ? (
        <div className="text-sm text-gray-500">No files uploaded yet.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-widest text-gray-500 border-b">
              <th className="py-2">Name</th>
              <th className="py-2">Size</th>
              <th className="py-2">Modified</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.name} className="border-b last:border-b-0">
                <td className="py-2">
                  <a
                    className="text-blue-700 hover:underline"
                    href={`/api/uploads/${encodeURIComponent(f.name)}`}
                  >
                    {f.name}
                  </a>
                </td>
                <td className="py-2">{formatSize(f.size)}</td>
                <td className="py-2 text-gray-600">{new Date(f.modified).toLocaleString()}</td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    className="text-xs text-rose-700 hover:underline"
                    onClick={() => remove(f.name)}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
