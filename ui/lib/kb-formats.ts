import type { Publication, PublicationKind } from "@/lib/types";

/** File extensions a knowledge base indexes; mirrors the backend's list. */
export const KB_ACCEPTED_EXTENSIONS = [".pdf", ".csv", ".tsv", ".txt", ".md"] as const;

/** Value for an `<input type="file" accept>`: extensions plus MIME types. */
export const KB_ACCEPT_ATTR = [
  "application/pdf",
  "text/csv",
  "text/tab-separated-values",
  "text/plain",
  "text/markdown",
  ...KB_ACCEPTED_EXTENSIONS,
].join(",");

const ACCEPTED_TEXT = KB_ACCEPTED_EXTENSIONS.map((e) => e.slice(1).toUpperCase()).join(", ");

export function isAcceptedKbFile(name: string): boolean {
  const lower = name.toLowerCase();
  return KB_ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** Message for a file the knowledge base would reject; same wording as the backend. */
export function unsupportedFileMessage(name: string): string {
  return `File '${name}' is not a supported format. Accepted formats: ${ACCEPTED_TEXT}.`;
}

const KIND_LABELS: Record<PublicationKind, string> = {
  pdf: "PDF",
  csv: "CSV",
  tsv: "TSV",
  text: "Text",
  markdown: "Markdown",
};

/** Badge label for a non-PDF publication; null for PDFs and rows without a kind. */
export function formatBadge(p: Pick<Publication, "kind">): string | null {
  return p.kind && p.kind !== "pdf" ? KIND_LABELS[p.kind] : null;
}

/** "1,240 rows" for CSV/TSV with a known count; null otherwise. */
export function rowCountLabel(p: Pick<Publication, "kind" | "row_count">): string | null {
  if ((p.kind !== "csv" && p.kind !== "tsv") || p.row_count == null) return null;
  return `${p.row_count.toLocaleString("en-US")} ${p.row_count === 1 ? "row" : "rows"}`;
}
