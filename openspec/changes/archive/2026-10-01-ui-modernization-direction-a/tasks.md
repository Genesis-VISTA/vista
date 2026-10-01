## 1. Housekeeping (commit one, independently revertible)

- [x] 1.1 Remove Tailwind: uninstall `tailwindcss` and `@tailwindcss/postcss`, drop the plugin from `ui/postcss.config.js`, delete `ui/tailwind.config.ts`. Verify `npm run build` succeeds and no visual diff appears on any route.
- [x] 1.2 Delete the two `disabled: true` entries (`Models`, `More`) from `ui/components/NavRail.tsx`. Verify the rail renders with no inert items.
- [x] 1.3 Move `formatResultSummary`, `extractPlotPath`, `parseReferencesFromStdout`, `extractReferences`, `extractPredictionSummary`, and `intermediatePreview` from `ui/app/page.tsx` into `ui/lib/result-parsing.ts`. Verify `npm run typecheck` and `npm run lint` pass with no behavior change.
- [x] 1.4 Correct the "Half-finished Tailwind migration" paragraph in `docs/ui-modernization-brief.md`, which claims a migration that does not exist. Verify the brief no longer asserts that `skill-hub` and `projects` use utility classes.
- [x] 1.5 Commit as `chore(ui): remove Tailwind, dead nav entries, extract result parsing`. Verify the commit stands alone by checking out that SHA and running `./scripts/ci-local.sh ui lint`.

## 2. Component test lane

- [x] 2.1 Add Vitest, `@testing-library/react`, jsdom, and `vite-tsconfig-paths`; create `ui/vitest.config.ts`. Verify a trivial test importing a `@/` path and a CSS module runs green.
- [x] 2.2 Add `npm run test` to `ui/package.json` and wire `ui test` into `./scripts/ci-local.sh`. Verify `./scripts/ci-local.sh ui test` runs the suite.
- [x] 2.3 Test `ui/components/ToolApprovalModal.tsx`: render, approve, deny, and the always-allow path. Verify each callback fires with the expected arguments.
- [x] 2.4 Test `ui/components/ElicitationModal.tsx`: render each field type, submit, cancel. Verify submitted values match the entered ones.
- [x] 2.5 Test `PublishConfirmModal`, `SkillEditorModal`, and `SandboxedHtmlCard`. Verify the sandbox card's iframe carries its sandbox attributes.
- [x] 2.6 Unit-test the helpers extracted in 1.3 against real captured stdout. Verify reference parsing and prediction-summary extraction cover the empty, malformed, and populated cases.
- [x] 2.7 Add a required `ui:test` job to `.gitlab-ci.yml` with no `allow_failure`. Verify a deliberately broken assertion fails the pipeline.

## 3. Hermetic browser test (blocking)

- [x] 3.1 Add `ui/playwright.hermetic.config.ts` with a `webServer` block starting the Next dev server and no backend. Verify the config starts and stops cleanly.
- [x] 3.2 Intercept every backend route the flow touches via `page.route`, nine returning fixed JSON. Verify with `page.on('request')` that nothing escapes to a real origin.
- [x] 3.3 Script the SSE response for the chat stream route as a chunked body, since the client reads `response.body.getReader()` rather than using `EventSource`. Verify the thread renders streamed content.
- [x] 3.4 Write the flow: open app, pick a project, send a message, observe the status line, observe the final answer, open the workspace tabs. Verify it passes against the pre-change UI where behavior is unchanged, and fails when a selector is wrong.
- [x] 3.5 Add the job to `.gitlab-ci.yml` as required, separate from `nightly:playwright`. Verify `nightly:playwright` keeps `allow_failure: true` and its schedule/manual rules.
- [x] 3.6 Replace the `test.skip`-on-missing-fixture pattern in the existing nightly spec with a failure that names what was missing. Verify the nightly job fails rather than reporting success when its seeded project is absent.

## 4. Shared top bar: skills and datasets

- [x] 4.1 Build `ui/components/AppTopBar.tsx` taking a page title and optional actions, styled from the existing `.app-topbar` rules in `globals.css`. Verify it renders with the Genesis lockup on a dark-blue plate, per the AmSC logo rule.
- [x] 4.2 Adopt the bar on `ui/app/skills/page.tsx` and `ui/app/datasets/page.tsx`, deleting their hand-made headers. Verify page padding and the first content row align identically on both.
- [x] 4.3 Reconcile the page-padding differences the two pages expose. Verify the shared bar sits at the same offset on both routes at 1440px and at 768px.

## 5. Shared top bar: projects, skill hub, knowledge bases

- [x] 5.1 Adopt the bar on `ui/app/projects/page.tsx` and `ui/app/skill-hub/page.tsx`. Verify both lose their local title markup.
- [x] 5.2 Adopt the bar on `ui/app/knowledge-bases/page.tsx` and delete its page-scoped toolbar, folding Refresh and New into the bar or the page header and rehoming the "Show all" link beside the picker. Verify every control that existed before is still reachable.
- [x] 5.3 Confirm both knowledge-base modes still render and switch. Verify project-scoped and global views each load with their existing data.

## 6. Project as context

- [x] 6.1 Build `ui/components/ProjectSwitcher.tsx` into the top bar, reading and writing the `ui/lib/projects.ts` store. Verify switching from any route updates the active project without a full reload.
- [x] 6.2 Make switching preserve the current route. Verify that switching on `/datasets` stays on `/datasets` scoped to the new project.
- [x] 6.3 Redirect chat to the project picker when no project is active, storing the intended destination and returning there after selection. Verify a direct visit to `/` with no project lands on the picker and then returns to chat.
- [x] 6.4 Build one shared "pick a project first" panel and use it on datasets and skills, replacing their three current treatments. Verify selecting from the panel keeps the user on the page they opened.
- [x] 6.5 Enable the `OPENED PROJECT` group in `NavRail` unconditionally and remove the "coming soon" hover text. Verify each entry navigates with and without an active project.
- [x] 6.6 Default the rail to expanded on first load, persisting the user's collapse choice. Verify a cleared `localStorage` yields an expanded rail.
- [x] 6.7 Add an empty state to the projects page explaining what a project is and offering creation. Verify it renders when the projects list is empty.
- [x] 6.8 Add the per-project count fan-out to the project cards with a comment recording the cost. Verify counts match the underlying endpoints for a seeded project.

## 7. Chat workspace

- [x] 7.1 Add the `stdout` field to the agent result payload so tool output reaches the UI, replacing the hardcoded `stdout: ""` at `ui/app/page.tsx:838`. Verify the Prediction Summary and References panels populate from an agent run with no salt button involved.
- [x] 7.2 Replace the two stacked right-column panes with an Artifacts / Activity / Jobs tab set, keeping the vertical divider and removing the horizontal one. Verify Artifacts is selected on open.
- [x] 7.3 Render the Jobs tab only when a campaign exists, matching the existing `CampaignPanel` guard. Verify a non-campaign conversation shows two tabs, not three.
- [x] 7.4 Build the Activity view from the persisted message record rather than the ephemeral `agentLogs` state, with a toggle for the raw log text. Verify a reloaded conversation shows no less detail than it does before this change.
- [x] 7.5 Replace the stacking `intermediate: true` tool bubbles with one in-thread status line that updates in place and clears on completion. Verify a multi-tool run leaves only the user turn and the final answer in the thread.
- [x] 7.6 Add the tool label map with the raw tool name as fallback. Verify an unmapped tool name renders the raw name rather than an empty line.
- [x] 7.7 Remove the "Analyze Salt" and "Predict Salt" quick-chips and their handler path. Verify no remaining code builds a shell command from user input outside the agent's approval gate.
- [x] 7.8 Add suggestion chips to the empty conversation state that prefill the composer without sending. Verify clicking one populates the input and sends nothing.
- [x] 7.9 Re-run the hermetic browser test against the new chat layout, updating selectors. Verify it passes and still intercepts every route.

## 8. Visual language: match the Direction A artboards

The structural work in groups 4–7 landed inside the pre-change visual system,
so every route rendered new structure in the old skin. The artboards are the
approved artifact and they use a different model: **one continuous surface
divided by hairlines**, not floating rounded cards with gutters. Layout panes
carry no radius, no shadow and no gutter; only content objects (project cards
12px, artifact and activity cards 10px, the composer 12px, the user bubble)
are rounded. There is not one shadow and not one gradient in either artboard.

This group does not reopen any group 4–7 decision. In particular task 7.5
stands: the thread carries the user turn, the final answer and one status
line, and tool steps stay in the Activity tab. The artboard's in-thread steps
card is **not** adopted.

Two decisions taken while working the group. The artboards' warm near-white
neutrals replace the AmSC guide's light greys (`#D2D3D7` / `#E3E3E3` /
`#F2F2F2`) as a local choice, reversible if it ever matters — nothing is
raised with AmSC, which is out of scope for this change. And the two dark
terminal surfaces, raw agent output and the log viewer, stay dark: they read
as a console on purpose, so they get their own token group rather than being
folded into the light ramp.

- [x] 8.1 Retokenize `:root` to the artboard ramp (ground `#f4f4f2`, surfaces `#ffffff` / `#fbfbfa`, hairlines `#e2e2de` / `#f0f0ec` / `#d8d8d2`, ink `#1c1c1a` / `#3d3d39`, muted `#767671` / `#6b6b66`, brand tints, radius scale), keeping every brand, status and accent value from the AmSC guide verbatim and the legacy names as aliases. Verify no route renders an undefined custom property.
- [x] 8.2 Strip card chrome from every layout pane: `.panel` loses its 16px radius, shadow and fill; `.workspace` and `.left-split` go to `gap: 0` with the resizer as a 1px hairline; `.projects-panel` stops wrapping the grid in a card. Verify no resting `box-shadow` and no `linear-gradient` remains outside overlay elevation and the indeterminate progress bar.
- [x] 8.3 Split the body treatment by route type: list routes (projects, skill hub, skills, datasets) get a `26px 30px` padded body holding a card grid; master/detail routes (chat, knowledge bases) run edge to edge via `.app-page-body.flush`. Verify the knowledge-base panes reach the window edges and the project grid does not.
- [x] 8.4 Rebuild the chat column to the artboard: delete the `.panel-header` that duplicated the top bar's title and move its controls into `AppTopBar`'s existing `actions` slot; assistant turns become bare 14px/1.6 prose; only the user turn keeps a bubble, right-aligned with the `12px 12px 4px 12px` radius; the composer becomes a card with a 32px square navy send button; workspace tabs become a 2px underline. Verify the thread still shows exactly the user turn, the final answer and the status line, per 7.5.
- [x] 8.5 Audit and fix `skills`, `datasets`, `skill-hub` and `knowledge-bases`, which have not been reviewed since retokenizing. Verify none of them wraps a list or grid in a bordered, rounded, filled container the way `.projects-panel` did.
- [x] 8.6 Apply the artboard type ramp: 14px/1.6 prose, 13px rail entries and card descriptions, 12px UI and secondary text, 11px meta, 10px uppercase rail section labels; Figtree on titles and card names only, the mono face confined to tool names, identifiers and raw output. Verify nothing renders below the artboards' 10px floor and that `.panel-title` is no longer uppercase muted 16px.
- [x] 8.7 Fold the remaining hardcoded hex literals into the token set. Verify no hex literal survives outside `:root`.
- [x] 8.8 Update the component and hermetic browser tests against the changed markup, in particular the chat header controls that moved into the top bar and the rebuilt composer. Verify `./scripts/ci-local.sh ui` is green across lint, typecheck, the component suite and the browser suite.
- [x] 8.9 Stop the navigation rail opting every route out of prerendering. `useSearchParams` in a root-layout client component excludes the enclosing Suspense boundary from the build, so all six routes shipped HTML with an empty rail and filled it in only after hydration; the console error on every route was React reporting that, not a markup mismatch. The rail read the query string only to tell the two knowledge-base views apart, so those became two routes (`/knowledge-bases` and `/knowledge-bases/project`) and the rail now matches on path alone — which also retires the App Router's no-op on a query-only navigation, the bug group 6 had to work around. Verify the rail's entries appear in the prerendered HTML for every route, and that only `/projects` still bails, for its own unrelated `?new=1`.
- [x] 8.10 Resolve the `var(--name, #fallback)` references naming tokens that were never defined, so every one of them always rendered its fallback — and the fallbacks were dark-theme values sitting in a light UI. Verify the tool-approval modal's script body no longer paints a 25%-black slab, and that no fallback in the file names an undefined token.

## 9. Reconciliation, review, and follow-ups

- [x] 9.1 Capture before/after screenshots of all six routes plus the salt-button flow, with the "after" taken once group 8 is complete rather than mid-restyle. Both sides are driven through the hermetic stub and fixtures by `ui/playwright.shots.config.ts`, so a pair differs only by the change. Verify each pair shows the same project, the same conversation and the same file list.
- [x] 9.2 Narrow the "Minimal Playwright smoke outside PR CI" requirement in `openspec/changes/milestone-d-validation-lane/` to the seeded smoke only, so it stops contradicting the hermetic browser job task 3.5 made required. Verify `openspec validate --all` passes with both changes open.
- [ ] 9.3 Open the MR against `main` with a reviewer other than the author, noting the housekeeping commit as the clean split point. Verify `ui:lint`, `ui:typecheck`, `ui:test`, and the hermetic browser job all pass.
- [ ] 9.4 File follow-ups: per-turn step persistence, rollup counts on the projects endpoint, splitting `page.tsx`, merging the knowledge-base modes, and `CampaignPanel` polling every 5s with no campaign. Verify each has an issue with a link back to this change.
