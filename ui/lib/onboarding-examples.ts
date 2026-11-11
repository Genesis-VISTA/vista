/**
 * The tour's examples: what a step shows in the real components and nowhere
 * else. The files step's example files, and the collaboration step's example
 * teams and skills.
 *
 * The example files the Files page shows while the tour's files step is up:
 * a markdown report and a chart among the outputs, a CSV among the uploads.
 * They exist only here. Nothing is written to the project or sent to the
 * backend, so the agent never sees them, and each carries its content as a
 * `data:` URL for `FilePreview`'s `src`.
 */

import { useTour } from "@/components/TourProvider";
import type { CollaborationTeam } from "./collaboration";
import type { TourEvent, TourPosition } from "./onboarding";
import type { FileKind, ListedFile } from "./files-tree";
import type { RemoteSkill } from "./skill-sharing";
import type { SkillSummary } from "./types";

export type ExampleFile = {
  kind: FileKind;
  file: ListedFile;
  /** The content as a `data:` URL, which the previewer shows in place of a project file. */
  src: string;
  /** What opening it tells the tour. */
  opened?: TourEvent;
};

const REPORT = `# Sweep report

This is an example. The agent writes reports like it to the project's outputs, here a
made-up parameter sweep.

## Runs

| Run | Input value | Result | Runtime |
| --- | --- | --- | --- |
| 1 | 0.1 | 2.0 | 10 s |
| 2 | 0.2 | 3.5 | 11 s |
| 3 | 0.3 | 4.1 | 10 s |
| 4 | 0.4 | 3.2 | 12 s |
| 5 | 0.5 | 1.8 | 11 s |

## Next steps

- Ask the agent to rerun the sweep with a finer step.
- Ask it to explain which input value gave the best result.
`;

const SWEEP_CSV = `run,input_value
1,0.1
2,0.2
3,0.3
4,0.4
5,0.5
`;

/** A small bar chart. Named colours on a white plate, so it reads in both themes. */
const CHART_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="200" viewBox="0 0 360 200" font-family="sans-serif" font-size="12">
<rect width="360" height="200" fill="white"/>
<text x="20" y="24" font-size="14" font-weight="bold" fill="black">Example: result per run</text>
<line x1="40" y1="160" x2="340" y2="160" stroke="dimgray"/>
<rect x="55" y="96" width="36" height="64" fill="steelblue"/>
<rect x="115" y="48" width="36" height="112" fill="steelblue"/>
<rect x="175" y="29" width="36" height="131" fill="steelblue"/>
<rect x="235" y="58" width="36" height="102" fill="steelblue"/>
<rect x="295" y="102" width="36" height="58" fill="steelblue"/>
<g fill="black" text-anchor="middle"><text x="73" y="178">1</text><text x="133" y="178">2</text><text x="193" y="178">3</text><text x="253" y="178">4</text><text x="313" y="178">5</text></g>
<text x="40" y="194" fill="dimgray">Run</text>
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
  example("outputs", "sweep-report.md", "text/markdown", REPORT, "example-md-opened"),
  example("outputs", "sweep-results.svg", "image/svg+xml", CHART_SVG, "example-image-opened"),
  example("uploads", "sweep-inputs.csv", "text/csv", SWEEP_CSV),
];

/** The example at `kind` and `path`, if there is one. */
export function exampleAt(kind: FileKind, path: string): ExampleFile | undefined {
  return EXAMPLE_FILES.find((e) => e.kind === kind && e.file.name === path);
}

/* ------------------------------------------------------------------ */
/* The collaboration step's examples                                   */
/* ------------------------------------------------------------------ */

/** The examples' teams' ids, never a real team's. */
export const EXAMPLE_PUBLIC_TEAM_ID = "example:public";
export const EXAMPLE_TEAM_ID = "example:team";
export const EXAMPLE_TEAM_NAME = "Example team";

/** Something the collaboration step shows that exists only in render. */
export type Example = { example: true };

/** Whether `item` is one of the tour's examples rather than a real one. */
export function isExample(item: object | null | undefined): boolean {
  return (item as Partial<Example> | null | undefined)?.example === true;
}

export type ExampleTeam = CollaborationTeam & Example;

/** What the Skill Hub's team filter lists during the step. Private is no team. */
export const EXAMPLE_TEAMS: readonly ExampleTeam[] = [
  {
    id: EXAMPLE_PUBLIC_TEAM_ID,
    name: "Public",
    public: true,
    created_by: "example",
    created_by_name: null,
    created_at: MODIFIED,
    example: true,
  },
  {
    id: EXAMPLE_TEAM_ID,
    name: EXAMPLE_TEAM_NAME,
    public: false,
    created_by: "example",
    created_by_name: null,
    created_at: MODIFIED,
    example: true,
  },
];

/**
 * A Skill Hub card's skill. It carries its team's name, which `TeamBadge`
 * shows: the real teams it would look the id up in don't hold the examples'.
 */
export type ExampleHubSkill = RemoteSkill & Example & { team_name: string };

function hubSkill(id: string, name: string, teamId: string, teamName: string, version: number, description: string) {
  return {
    id,
    team_id: teamId,
    team_name: teamName,
    name,
    head_version: version,
    created_by_name: null,
    created_at: MODIFIED,
    published_by_name: null,
    published_at: MODIFIED,
    manifest: { name, description, hpc_jobs: [], files: 1, size: 1024 },
    local: null,
    name_taken: false,
    example: true,
  } satisfies ExampleHubSkill;
}

/** The Skill Hub's cards during the step, in place of its own content. */
export const EXAMPLE_HUB_SKILLS: readonly ExampleHubSkill[] = [
  hubSkill("example:plot-results", "plot-results", EXAMPLE_PUBLIC_TEAM_ID, "Public", 1, "Plots a results table as labelled figures."),
  hubSkill(
    "example:batch-job-submit",
    "batch-job-submit",
    EXAMPLE_TEAM_ID,
    EXAMPLE_TEAM_NAME,
    3,
    "Submits a parameter sweep as Slurm jobs on Frontier.",
  ),
];

/**
 * A Skills page card's skill: its team's name (null for a private one), and
 * the sharing line it shows in place of `sharingLabel`'s.
 */
export type ExampleLocalSkill = SkillSummary & Example & { teamName: string | null; sharing: string };

/** Added to the Skills page's own list during the step. */
export const EXAMPLE_LOCAL_SKILLS: readonly ExampleLocalSkill[] = [
  {
    slug: "csv-cleanup",
    name: "csv-cleanup",
    description: "Tidies a CSV file's columns and units before analysis.",
    path: "",
    hpcJobs: [],
    bundled: false,
    remoteId: null,
    remoteVersion: null,
    modified: true,
    teamId: null,
    teamName: null,
    sharing: "Not published · on this computer only",
    example: true,
  },
  {
    slug: "run-summary",
    name: "run-summary",
    description: "Summarises a finished run's outputs in a short report.",
    path: "",
    hpcJobs: [],
    bundled: false,
    remoteId: "example:run-summary",
    remoteVersion: 2,
    modified: true,
    teamId: EXAMPLE_TEAM_ID,
    teamName: EXAMPLE_TEAM_NAME,
    sharing: "Changed here since version 2",
    example: true,
  },
];

/** Whether the collaboration step's examples show at `position`. Pure, for testing. */
export function showsTourExamples(position: TourPosition | null): boolean {
  return position?.step === "collaboration";
}

/**
 * Whether the collaboration step's examples show now: while the step does,
 * whatever the researcher's collaboration state, and never otherwise.
 */
export function useTourExamples(): boolean {
  return showsTourExamples(useTour().position);
}
