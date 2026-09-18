import { describe, expect, it } from "vitest";
import {
  extractPlotPath,
  extractPredictionSummary,
  extractReferences,
  formatResultSummary,
  intermediatePreview,
  parseReferencesFromStdout,
} from "@/lib/result-parsing";
import type { ExecutionResult } from "@/lib/types";

/**
 * Shaped after what the seeded skills actually print:
 * `backend/src/vista_backend/db/skills/salt-prediction/scripts/predict_salt.py`
 * emits the training block, then SUMMARY_JSON, then the plot path;
 * `salt-analysis/scripts/analyze_salt.py` prints a `References` heading
 * underlined with `=`, then `[n] <citation>` lines.
 */
const PREDICT_STDOUT = [
  "",
  "Training summary:",
  "Samples: 412",
  "Features: 18",
  "Train size: 329",
  "Test size: 83",
  "MAE: 24.117 K",
  "RMSE: 33.902 K",
  "R2: 0.94218",
  "Mean uncertainty: 18.44 K",
  "Predicted melting point (LiF-BeF2 | 66-34): 731.204 +/- 21.887 K",
  'SUMMARY_JSON: {"samples": 412, "mae_k": 24.117, "r2": 0.94218, "formula": "LiF-BeF2", "predicted_melting_point_k": 731.204, "plot_path": "/mnt/data/output/salt-prediction-LiF-BeF2-66-34-20260914-101500.png"}',
  "Plot saved to /mnt/data/output/salt-prediction-LiF-BeF2-66-34-20260914-101500.png",
].join("\n");

const ANALYZE_STDOUT = [
  "",
  "",
  "Analyzing salt: FLiBe",
  "  density: 2.28 g/cm3 at 873 K",
  "",
  "============================================================",
  "References",
  "============================================================",
  "",
  "[1] Janz, G.J., Molten Salts Handbook, Academic Press, 1967",
  "",
  "[2] 10.1016/j.jnucmat.2019.151794",
  "",
  "[3] Romatoski & Hu, Ann. Nucl. Energy 109 (2017) 635-647",
  "Plot saved to /mnt/data/output/flibe-density.png",
].join("\n");

function result(over: Partial<ExecutionResult> = {}): ExecutionResult {
  return { ok: true, stdout: "", stderr: "", artifacts: [], meta: {}, ...over };
}

describe("extractPlotPath", () => {
  it("pulls the path a skill printed", () => {
    expect(extractPlotPath(PREDICT_STDOUT)).toBe(
      "/mnt/data/output/salt-prediction-LiF-BeF2-66-34-20260914-101500.png",
    );
  });

  it("returns null when nothing was plotted", () => {
    expect(extractPlotPath("Samples: 412\nMAE: 24.117 K")).toBeNull();
    expect(extractPlotPath("")).toBeNull();
  });

  it("takes the first path when a script plots more than once", () => {
    const two = "Plot saved to /out/a.png\nmore work\nPlot saved to /out/b.png";
    expect(extractPlotPath(two)).toBe("/out/a.png");
  });

  it("trims trailing whitespace off the path", () => {
    expect(extractPlotPath("Plot saved to /out/a.png   ")).toBe("/out/a.png");
  });
});

describe("extractPredictionSummary", () => {
  it("parses the SUMMARY_JSON line out of a full run", () => {
    const summary = extractPredictionSummary(result({ stdout: PREDICT_STDOUT }));
    expect(summary).toMatchObject({
      samples: 412,
      mae_k: 24.117,
      formula: "LiF-BeF2",
      predicted_melting_point_k: 731.204,
    });
  });

  it.each([
    ["no result at all", null],
    ["an empty stdout", result({ stdout: "" })],
    ["stdout with no summary line", result({ stdout: ANALYZE_STDOUT })],
    ["a summary marker with nothing after it", result({ stdout: "SUMMARY_JSON:" })],
  ])("returns null for %s", (_label, input) => {
    expect(extractPredictionSummary(input as ExecutionResult | null)).toBeNull();
  });

  // A truncated or malformed line must not take the panel down with it.
  it("returns null rather than throwing on malformed JSON", () => {
    expect(extractPredictionSummary(result({ stdout: 'SUMMARY_JSON: {"samples": 412' }))).toBeNull();
  });

  it("returns null when the payload is valid JSON but not an object", () => {
    expect(extractPredictionSummary(result({ stdout: "SUMMARY_JSON: 42" }))).toBeNull();
    expect(extractPredictionSummary(result({ stdout: "SUMMARY_JSON: null" }))).toBeNull();
  });
});

describe("parseReferencesFromStdout", () => {
  // Note the last entry. Once the parser enters the References section it
  // never leaves, so anything a script prints afterwards is captured as a
  // citation. This pins current behaviour, not desired behaviour; changing it
  // is a behaviour fix and belongs in its own change.
  it("reads a numbered reference block, and keeps reading past the end of it", () => {
    expect(parseReferencesFromStdout(ANALYZE_STDOUT)).toEqual([
      "Janz, G.J., Molten Salts Handbook, Academic Press, 1967",
      "10.1016/j.jnucmat.2019.151794",
      "Romatoski & Hu, Ann. Nucl. Energy 109 (2017) 635-647",
      "Plot saved to /mnt/data/output/flibe-density.png",
    ]);
  });

  it("picks up a bare DOI or URL before any reference block", () => {
    const stdout = "See 10.1016/j.jnucmat.2019.151794 for the fit\nand https://mstdb.ornl.gov/data";
    expect(parseReferencesFromStdout(stdout)).toEqual([
      "10.1016/j.jnucmat.2019.151794",
      "https://mstdb.ornl.gov/data",
    ]);
  });

  it("strips bullet markers as well as numbering", () => {
    const stdout = "References\n- Janz 1967\n* Romatoski 2017\n[3] Benes 2009";
    expect(parseReferencesFromStdout(stdout)).toEqual(["Janz 1967", "Romatoski 2017", "Benes 2009"]);
  });

  it.each([["an empty string", ""], ["output with no citations", "Samples: 412\nMAE: 24.117 K"]])(
    "returns nothing for %s",
    (_label, stdout) => {
      expect(parseReferencesFromStdout(stdout)).toEqual([]);
    },
  );

  it("handles CRLF output", () => {
    expect(parseReferencesFromStdout("References\r\n[1] Janz 1967\r\n")).toEqual(["Janz 1967"]);
  });
});

describe("extractReferences", () => {
  it("merges meta, data, and stdout references without repeating any", () => {
    const refs = extractReferences(
      result({
        stdout: "References\n[1] Janz 1967\n[2] Benes 2009",
        meta: { references: ["Janz 1967", "  Romatoski 2017  "] },
        data: { references: ["Benes 2009"] },
      }),
    );
    expect(refs).toEqual(["Janz 1967", "Romatoski 2017", "Benes 2009"]);
  });

  it("ignores non-string entries", () => {
    const refs = extractReferences(
      result({ meta: { references: ["Janz 1967", 42, null, "   "] } }),
    );
    expect(refs).toEqual(["Janz 1967"]);
  });

  it("returns nothing for a null result", () => {
    expect(extractReferences(null)).toEqual([]);
  });

  it("survives a result carrying no meta or data references", () => {
    expect(extractReferences(result({ stdout: "nothing here" }))).toEqual([]);
  });
});

describe("formatResultSummary", () => {
  it("leads with OK and the start of stdout", () => {
    expect(formatResultSummary(result({ stdout: "Samples: 412" }))).toBe("OK: Samples: 412");
  });

  it("leads with ERROR when the run failed", () => {
    expect(formatResultSummary(result({ ok: false, stdout: "Traceback" }))).toBe("ERROR: Traceback");
  });

  it("says only OK when there was no output", () => {
    expect(formatResultSummary(result({ stdout: "" }))).toBe("OK");
  });

  it("caps the preview at 240 characters", () => {
    const summary = formatResultSummary(result({ stdout: "x".repeat(500) }));
    expect(summary).toHaveLength("OK: ".length + 240);
  });
});

describe("intermediatePreview", () => {
  it("uses the first markdown heading", () => {
    expect(intermediatePreview("## Trial 3 report\n\nDensity converged.")).toBe("Trial 3 report");
  });

  it("strips bullet and quote markers", () => {
    expect(intermediatePreview("- checked 12 candidates")).toBe("checked 12 candidates");
    expect(intermediatePreview("> quoted note")).toBe("quoted note");
  });

  it("strips surrounding bold markers", () => {
    expect(intermediatePreview("**Trial 3 complete**")).toBe("Trial 3 complete");
  });

  it("skips a leading code fence", () => {
    expect(intermediatePreview("```python\nprint(1)\n```")).toBe("print(1)");
  });

  it.each([["empty content", ""], ["whitespace only", "   \n\n  "], ["only a fence", "```"]])(
    "falls back to a generic label for %s",
    (_label, content) => {
      expect(intermediatePreview(content)).toBe("Agent update");
    },
  );
});
