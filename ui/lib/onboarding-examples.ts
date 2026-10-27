/**
 * The example files the Files page shows while the tour's files step is up:
 * a markdown report and a chart among the outputs, a CSV among the uploads.
 * They exist only here. Nothing is written to the project or sent to the
 * backend, so the agent never sees them, and each carries its content as a
 * `data:` URL for `FilePreview`'s `src`.
 */

import type { TourEvent } from "./onboarding";
import type { FileKind, ListedFile } from "./files-tree";

export type ExampleFile = {
  kind: FileKind;
  file: ListedFile;
  /** The content as a `data:` URL, which the previewer shows in place of a project file. */
  src: string;
  /** What opening it tells the tour. */
  opened?: TourEvent;
};

const REPORT = `# Risk summary

Autonomous labs and LLM agents share a short list of risks. This is an example report: the
agent writes reports like it to the project's outputs.

## Shared risks

| Risk | Where it shows up | Proposed mitigation |
| --- | --- | --- |
| Prompt injection | Documents and tool output the agent reads | Treat retrieved text as data |
| Excess agency | Tools that act on instruments or clusters | Approval before irreversible actions |
| Data leakage | Credentials and unpublished results in prompts | Scoped tokens, no secrets in context |

## Next steps

- Rank the risks for your own workflow.
- Ask the agent to cite the papers behind each mitigation.
`;

const RISK_CSV = `risk,likelihood,impact
prompt injection,0.7,0.6
excess agency,0.4,0.9
data leakage,0.5,0.8
`;

/** A small bar chart. Named colours on a white plate, so it reads in both themes. */
const CHART_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="200" viewBox="0 0 360 200" font-family="sans-serif" font-size="12">
<rect width="360" height="200" fill="white"/>
<text x="20" y="24" font-size="14" font-weight="bold" fill="black">Example: risk scores</text>
<g fill="black"><text x="20" y="70">Prompt injection</text><text x="20" y="110">Excess agency</text><text x="20" y="150">Data leakage</text></g>
<rect x="130" y="56" width="168" height="20" fill="steelblue"/>
<rect x="130" y="96" width="198" height="20" fill="firebrick"/>
<rect x="130" y="136" width="154" height="20" fill="darkorange"/>
<line x1="130" y1="44" x2="130" y2="170" stroke="dimgray"/>
<text x="130" y="188" fill="dimgray">Score, 0 to 1</text>
</svg>`;

function dataUrl(mime: string, text: string): string {
  return `data:${mime};charset=utf-8,${encodeURIComponent(text)}`;
}

const MODIFIED = "2026-01-01T00:00:00Z";

function example(kind: FileKind, name: string, mime: string, text: string, opened?: TourEvent): ExampleFile {
  return {
    kind,
    file: { name, size: new TextEncoder().encode(text).length, modifiedAt: MODIFIED },
    src: dataUrl(mime, text),
    opened,
  };
}

export const EXAMPLE_FILES: readonly ExampleFile[] = [
  example("outputs", "summary.md", "text/markdown", REPORT, "example-md-opened"),
  example("outputs", "risk-scores.svg", "image/svg+xml", CHART_SVG, "example-image-opened"),
  example("uploads", "risks.csv", "text/csv", RISK_CSV),
];

/** The example at `kind` and `path`, if there is one. */
export function exampleAt(kind: FileKind, path: string): ExampleFile | undefined {
  return EXAMPLE_FILES.find((e) => e.kind === kind && e.file.name === path);
}
