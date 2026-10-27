"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import SandboxedHtmlCard from "@/components/SandboxedHtmlCard";
import ImageLightbox from "@/components/ImageLightbox";
import { readCapped } from "@/lib/capped-read";
import { delimiterFor, parseDelimited } from "@/lib/csv-preview";
import { fileLinkProps } from "@/lib/file-links";

type Props = {
  kind: "uploads" | "outputs";
  /** Path inside the kind, as listed (may be nested). */
  path: string;
  name: string;
  /** Listed size in bytes, for the truncation note; omitted where it isn't known. */
  size?: number;
  projectName: string | null;
  /**
   * Which version of the file this is (its modified time, or the display that
   * showed it). It goes into the preview URL, because a page reuses an image it
   * has already loaded from the same URL even after the file has changed.
   */
  version?: string;
  /**
   * Where the content is, when it is not a project file: preview and download
   * use it in place of the project file's URL. The tour's example files are
   * `data:` URLs.
   */
  src?: string;
};

type Renderer = "image" | "text" | "csv" | "markdown" | "html" | "pdf" | "none";

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg"]);
const TEXT_EXT = new Set([
  "txt", "log", "out", "err", "py", "sh", "bash", "zsh", "js", "jsx", "ts", "tsx", "json", "jsonl",
  "yaml", "yml", "toml", "ini", "cfg", "conf", "xml", "c", "h", "cpp", "hpp", "f90", "r", "sql", "css",
  "in", "dat", "lammps", "inp",
]);

export function rendererFor(name: string): Renderer {
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  if (IMAGE_EXT.has(ext)) return "image";
  if (ext === "csv" || ext === "tsv") return "csv";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "html" || ext === "htm") return "html";
  if (ext === "pdf") return "pdf";
  if (TEXT_EXT.has(ext)) return "text";
  return "none";
}

function fileUrl({ kind, path, projectName, version }: Props, preview: boolean): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  const params: string[] = [];
  if (projectName) params.push(`project_name=${encodeURIComponent(projectName)}`);
  if (preview) params.push("preview=1");
  if (preview && version) params.push(`v=${encodeURIComponent(version)}`);
  return `/api/files/${kind}/${encoded}${params.length ? `?${params.join("&")}` : ""}`;
}

type Loaded = { text: string; bytesRead: number; truncated: boolean };

/** Capped text of the file, or an error message. */
function useCappedText(url: string, enabled: boolean): { data: Loaded | null; error: string | null } {
  const [state, setState] = useState<{ url: string; data: Loaded | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Could not load the file (${res.status}).`);
        const data = await readCapped(res);
        if (!cancelled) setState({ url, data, error: null });
      } catch (e) {
        if (!cancelled) setState({ url, data: null, error: e instanceof Error ? e.message : "Could not load the file." });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url, enabled]);
  const current = state && state.url === url ? state : null;
  return { data: current?.data ?? null, error: current?.error ?? null };
}

function TruncationNote({ loaded, size }: { loaded: Loaded; size?: number }) {
  if (!loaded.truncated) return null;
  const kib = (n: number) => `${Math.round(n / 1024).toLocaleString("en-US")} KiB`;
  // Only a listed size larger than what was read gives a total worth stating.
  const total = size !== undefined && size > loaded.bytesRead ? ` of ${kib(size)}` : "";
  return (
    <p className="muted" role="note">
      Showing the first {kib(loaded.bytesRead)}{total}. Download the file for the rest.
    </p>
  );
}

function prettyJson(text: string, truncated: boolean): string {
  if (truncated) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function CsvTable({ name, loaded, size }: { name: string; loaded: Loaded; size?: number }) {
  const parsed = parseDelimited(loaded.text, delimiterFor(name));
  return (
    <>
      <div style={{ overflow: "auto" }}>
        <table className="file-preview-table">
          <tbody>
            {parsed.rows.map((row, i) => (
              <tr key={i}>{row.map((cell, j) => (i === 0 ? <th key={j}>{cell}</th> : <td key={j}>{cell}</td>))}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {parsed.omitted > 0 && (
        <p className="muted" role="note">
          {parsed.omitted.toLocaleString("en-US")} more rows not shown. Download the file for all of it.
        </p>
      )}
      <TruncationNote loaded={loaded} size={size} />
    </>
  );
}

/**
 * A project file, shown by its type. Everything is fetched with `?preview=1`
 * and nothing here runs the file: images are `<img>`, markdown has no raw
 * HTML and no images, HTML goes through the sandboxed card, PDFs open in a
 * window of their own.
 */
export default function FilePreview(props: Props) {
  const { name, size } = props;
  const renderer = rendererFor(name);
  const previewUrl = props.src ?? fileUrl(props, true);
  const downloadUrl = props.src ?? fileUrl(props, false);
  // A `src` URL carries no file name of its own, so the download is named here.
  const downloadProps = props.src ? { download: name } : fileLinkProps(downloadUrl);
  const [zoom, setZoom] = useState(false);
  const needsText = renderer === "text" || renderer === "csv" || renderer === "markdown" || renderer === "html";
  const { data, error } = useCappedText(previewUrl, needsText);

  let body: React.ReactNode = null;
  if (renderer === "image") {
    body = (
      <>
        <button type="button" className="artifact-zoom" onClick={() => setZoom(true)} title="Show this figure full size">
          <img src={previewUrl} alt={name} />
        </button>
        {zoom && <ImageLightbox src={previewUrl} alt={name} onClose={() => setZoom(false)} />}
      </>
    );
  } else if (renderer === "pdf") {
    body = (
      <button type="button" className="button" onClick={() => window.open(previewUrl, "_blank")}>
        Open PDF
      </button>
    );
  } else if (renderer === "none") {
    // Download is all there is to do, so it is the main action, not the bar's.
    body = (
      <div className="file-preview-empty">
        <p className="muted">No preview for this type.</p>
        <a className="button" href={downloadUrl} {...downloadProps}>
          Download
        </a>
      </div>
    );
  } else if (error) {
    body = <p className="error">{error}</p>;
  } else if (!data) {
    body = <p className="muted">Loading…</p>;
  } else if (renderer === "text") {
    body = (
      <>
        <pre className="file-preview-text">
          {name.toLowerCase().endsWith(".json") ? prettyJson(data.text, data.truncated) : data.text}
        </pre>
        <TruncationNote loaded={data} size={size} />
      </>
    );
  } else if (renderer === "csv") {
    body = <CsvTable name={name} loaded={data} size={size} />;
  } else if (renderer === "markdown") {
    body = (
      <>
        <div className="file-preview-markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // A project file must not make VISTA fetch a remote URL by being
              // looked at, so an image is only a link to where it would be.
              img: ({ src, alt }) => {
                const href = typeof src === "string" ? src : "";
                return (
                  <a href={href} target="_blank" rel="noreferrer">
                    {alt || href}
                  </a>
                );
              },
            }}
          >
            {data.text}
          </ReactMarkdown>
        </div>
        <TruncationNote loaded={data} size={size} />
      </>
    );
  } else if (renderer === "html") {
    body = (
      <>
        <SandboxedHtmlCard html={data.text} />
        <TruncationNote loaded={data} size={size} />
      </>
    );
  }

  return (
    <div className="file-preview">
      <div className="file-preview-bar">
        <span className="file-preview-name">{name}</span>
        {renderer !== "none" && (
          <a className="button ghost button-sm" href={downloadUrl} {...downloadProps}>
            Download
          </a>
        )}
      </div>
      {body}
    </div>
  );
}
