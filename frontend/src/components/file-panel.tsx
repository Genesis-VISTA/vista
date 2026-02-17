import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, File } from "lucide-react";
import { Button } from "@/components/ui/button";

export function FilePanel() {
  const [files, setFiles] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchFiles = useCallback(async () => {
    try {
      const res = await fetch("http://localhost:8000/uploads");
      if (res.ok) {
        const data = await res.json();
        setFiles(data.files);
      }
    } catch {
      // silently ignore fetch errors
    }
  }, []);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  const uploadFile = useCallback(
    async (file: globalThis.File) => {
      const formData = new FormData();
      formData.append("file", file);
      try {
        await fetch("http://localhost:8000/upload", {
          method: "POST",
          body: formData,
        });
        await fetchFiles();
      } catch {
        // silently ignore upload errors
      }
    },
    [fetchFiles],
  );

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (!dragging) setDragging(true);
    },
    [dragging],
  );

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const droppedFiles = Array.from(e.dataTransfer.files);
      for (const file of droppedFiles) {
        uploadFile(file);
      }
    },
    [uploadFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = e.target.files;
      if (selected) {
        for (const file of Array.from(selected)) {
          uploadFile(file);
        }
      }
      e.target.value = "";
    },
    [uploadFile],
  );

  return (
    <div
      className={`flex h-full w-64 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground border-sidebar-border ${dragging ? "ring-2 ring-inset ring-sidebar-ring" : ""}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="flex items-center justify-between border-b border-sidebar-border px-4 py-3">
        <h2 className="text-sm font-semibold">Files</h2>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload className="size-4" />
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={handleInputChange}
          multiple
        />
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {files.length === 0 ? (
          <p className="px-2 py-4 text-center text-xs text-sidebar-foreground/50">
            Drop files here or click upload
          </p>
        ) : (
          <ul className="space-y-0.5">
            {files.map((name) => (
              <li
                key={name}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              >
                <File className="size-4 shrink-0 opacity-50" />
                <span className="truncate">{name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
