import { describe, expect, it } from "vitest";
import {
  formatBadge,
  isAcceptedKbFile,
  rowCountLabel,
  unsupportedFileMessage,
} from "@/lib/kb-formats";

describe("kb formats", () => {
  it("accepts the five formats case-insensitively and rejects others", () => {
    for (const n of ["a.pdf", "a.CSV", "a.tsv", "a.txt", "a.MD"]) expect(isAcceptedKbFile(n)).toBe(true);
    for (const n of ["a.xlsx", "a.docx", "pdf"]) expect(isAcceptedKbFile(n)).toBe(false);
  });
  it("lists accepted formats in the rejection message", () => {
    expect(unsupportedFileMessage("a.xlsx")).toBe(
      "File 'a.xlsx' is not a supported format. Accepted formats: PDF, CSV, TSV, TXT, MD.",
    );
  });
  it("badges only non-PDF kinds", () => {
    expect(formatBadge({ kind: "markdown" })).toBe("Markdown");
    expect(formatBadge({ kind: "pdf" })).toBeNull();
    expect(formatBadge({})).toBeNull();
  });
  it("labels row counts for csv/tsv only", () => {
    expect(rowCountLabel({ kind: "csv", row_count: 1240 })).toBe("1,240 rows");
    expect(rowCountLabel({ kind: "tsv", row_count: 1 })).toBe("1 row");
    expect(rowCountLabel({ kind: "csv", row_count: null })).toBeNull();
    expect(rowCountLabel({ kind: "text", row_count: 5 })).toBeNull();
  });
});
