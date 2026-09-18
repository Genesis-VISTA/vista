## Why

Junqi asked for a modernized UI after the installable milestone. A
three-direction design exploration (`docs/ui-modernization-brief.md`,
artboards in `design/ui-modernization/`) settled on Direction A: a two-pane
workspace with a lighter visual weight, where agent activity reads clearly to
a scientist who isn't parsing logs.

Getting there means two things land first. Project becomes a context you're
inside rather than a mode you enter from one page, so projects, an opened
project, and a new project read as one continuum. And the UI gets its own CI
coverage, so a change this wide has something behind it to catch regressions —
the testing work ships as a prerequisite, not a companion.

## What Changes

**Housekeeping (lands first, stands alone)**

- Remove Tailwind v4: dependency, `ui/postcss.config.js` entry, `tailwind.config.ts`. It is installed, configured, and used by zero utility classes.
- Delete the two permanently `disabled: true` sidebar entries (`Models`, `More`).
- Extract five pure functions from `ui/app/page.tsx` into `ui/lib/`.
- Correct the "Half-finished Tailwind migration" paragraph in `docs/ui-modernization-brief.md`, which is wrong.

**Test lane**

- Add Vitest + Testing Library + jsdom, with component tests for the five components that need no mocking, including both consent dialogs.
- Add a hermetic Playwright flow that intercepts ~10 routes (no backend, no MCP server, no model, no seeded database) and **make it a required MR job**.
- Leave the existing seeded `nightly:playwright` job as-is, schedule/manual and `allow_failure`.

**Shell**

- One shared top bar on all six routes. Only chat has one today; the other five build their own title four different ways.
- The project name in that bar becomes a switcher usable from anywhere, and switching keeps you on the current route.
- Chat redirects to the project picker when no project is selected, remembering the intended destination. Datasets and skills share one "pick a project first" panel. Knowledge bases does not redirect; its global view is legitimate.
- The sidebar's `OPENED PROJECT` group becomes always-clickable, and the sidebar opens expanded. Those four entries are inert today and claim "coming soon" on hover, which is untrue.
- Knowledge bases loses its scoped toolbar; both of its modes stay.
- The projects page gets a real empty state.

**Chat workspace**

- The right column's two stacked panes become tabs: Artifacts, Activity, Jobs. Jobs renders only when a campaign exists.
- Activity holds structured step detail plus a raw-log toggle. The thread keeps user turns and final answers.
- A single in-thread status line with a spinner updates in place during a run and disappears when the answer lands, replacing today's stacking tool bubbles. It uses readable labels ("Searching the literature") with the raw tool name as fallback, since the MCP tool list is not fixed.
- **BREAKING (user-visible)**: the "Analyze Salt" and "Predict Salt" quick-chips are removed. They bypass the agent and the code-scanning gate, interpolate unquoted input into a shell string, and one is likely broken on a default install. Their stdout must be ported into the agent result payload first, because they are the only path that populates the Prediction Summary and References panels.
- Add suggestion chips in the empty conversation state that prefill the composer.

## Capabilities

### New Capabilities

- `ui-shell`: one chrome across every route, project as ambient context rather than a mode, and the redirect / empty-state rules for routes that need a project.
- `ui-agent-activity`: how a run reads in the chat workspace, from the in-thread status line to the tabbed right column and the structured activity view.

### Modified Capabilities

- `testing-ci`: adds a hermetic UI test lane (Vitest component tests plus an intercepted-route browser flow) to the set of required MR jobs. Today the UI contributes only `ui:lint` and `ui:typecheck`, neither of which runs UI code.

## Impact

- `ui/app/page.tsx` (1,941 lines) and `ui/app/knowledge-bases/page.tsx` (~1,755 lines) both change substantially. Neither is split in this change.
- New: `ui/components/AppTopBar.tsx`, `ui/components/ProjectSwitcher.tsx`, `ui/lib/*` extractions, `ui/vitest.config.ts`, `ui/tests/`.
- Removed: `tailwindcss` / `@tailwindcss/postcss` deps, `ui/tailwind.config.ts`, the salt-button handlers and their `/api/chat/quick` path.
- `.gitlab-ci.yml` gains `ui:test` and a blocking hermetic browser job.
- Contradicts one requirement in the open `milestone-d-validation-lane` change: "Minimal Playwright smoke outside PR CI" says Playwright MUST NOT be a required job. That scoping was written for a smoke test needing a live backend and a seeded database. The hermetic flow added here needs neither. Milestone D's requirement should be narrowed to the seeded smoke before either change archives.
- Backend: one field addition so tool stdout survives into the agent result payload.
- No interaction with the `desktop` branch. It carries its own merge request; whichever lands second resolves the overlap. The two touch six UI files in common, most of them net deletions here, plus ~70 lines of inference-credential fields in `UserSettingsModal`.
- Deliberately out of scope: dark mode (AmSC has not defined one), tokenizing `globals.css`, merging the two knowledge-base modes, splitting `page.tsx`.
