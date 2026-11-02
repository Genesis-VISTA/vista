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

const REPORT = `# Hello report

The same hello job ran on Odo, Frontier and Lux. This is an example report: the agent writes
reports like it to the project's outputs.

## The three clusters

| Cluster | Node | GPUs seen | ROCm module | Runtime | Checksum match |
| --- | --- | --- | --- | --- | --- |
| Odo | odo03 | 8 (MI250X GCDs) | rocm/6.2.4 | under 1 s | yes |
| Frontier | frontier08454 | 8 (MI250X GCDs) | rocm/6.2.4 | 1 s | yes |
| Lux | lux005 | 1 (MI355X) | rocm/7.2.4 | 1 s | yes |

## Next steps

- Ask the agent to adapt a hello skill to your own job.
- Ask it to cite the OLCF docs behind the queue and module choices.
`;

const CLUSTERS_CSV = `cluster,scheduler,gpu
odo,slurm,MI250X
frontier,slurm,MI250X
lux,slurm,MI355X
`;

/** A small bar chart. Named colours on a white plate, so it reads in both themes. */
const CHART_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="200" viewBox="0 0 360 200" font-family="sans-serif" font-size="12">
<rect width="360" height="200" fill="white"/>
<text x="20" y="24" font-size="14" font-weight="bold" fill="black">Example: GPUs seen per cluster</text>
<g fill="black"><text x="20" y="70">Odo</text><text x="20" y="110">Frontier</text><text x="20" y="150">Lux</text></g>
<rect x="90" y="56" width="200" height="20" fill="steelblue"/>
<rect x="90" y="96" width="200" height="20" fill="steelblue"/>
<rect x="90" y="136" width="25" height="20" fill="steelblue"/>
<g fill="black"><text x="296" y="70">8</text><text x="296" y="110">8</text><text x="121" y="150">1</text></g>
<line x1="90" y1="44" x2="90" y2="170" stroke="dimgray"/>
<text x="90" y="188" fill="dimgray">GPUs visible to the job</text>
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
  example("outputs", "hello-report.md", "text/markdown", REPORT, "example-md-opened"),
  example("outputs", "gpus-per-cluster.svg", "image/svg+xml", CHART_SVG, "example-image-opened"),
  example("uploads", "clusters.csv", "text/csv", CLUSTERS_CSV),
];

/** The example at `kind` and `path`, if there is one. */
export function exampleAt(kind: FileKind, path: string): ExampleFile | undefined {
  return EXAMPLE_FILES.find((e) => e.kind === kind && e.file.name === path);
}
