import { describe, expect, it } from "vitest";
import { delimiterFor, parseDelimited } from "@/lib/csv-preview";

describe("parseDelimited", () => {
  it("parses plain rows with LF and CRLF", () => {
    expect(parseDelimited("a,b\n1,2\n").rows).toEqual([["a", "b"], ["1", "2"]]);
    expect(parseDelimited("a,b\r\n1,2\r\n3,4").rows).toEqual([["a", "b"], ["1", "2"], ["3", "4"]]);
  });
  it("handles quoted commas, newlines and doubled quotes", () => {
    const { rows } = parseDelimited('name,note\n"Li, F","line1\nline2"\n"say ""hi""",x\n');
    expect(rows).toEqual([["name", "note"], ["Li, F", "line1\nline2"], ['say "hi"', "x"]]);
  });
  it("keeps empty fields and parses TSV", () => {
    expect(parseDelimited("a,,c\n").rows).toEqual([["a", "", "c"]]);
    expect(parseDelimited("a\tb,c\t\n", "\t").rows).toEqual([["a", "b,c", ""]]);
    expect(delimiterFor("x.TSV")).toBe("\t");
    expect(delimiterFor("x.csv")).toBe(",");
  });
  it("returns the first 500 rows and counts the rest", () => {
    const text = Array.from({ length: 1200 }, (_, i) => `${i},v`).join("\n") + "\n";
    const out = parseDelimited(text);
    expect(out.rows).toHaveLength(500);
    expect(out.omitted).toBe(700);
  });
  it("reports nothing omitted for small input and empty input", () => {
    expect(parseDelimited("a\nb").omitted).toBe(0);
    expect(parseDelimited("")).toEqual({ rows: [], omitted: 0 });
  });
});
