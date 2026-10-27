## Context

See proposal.md for why. The UI has no tour, coachmark or component library: popovers such
as `ModelMenu` and `ProjectSwitcher` are hand-rolled, and all colors go through theme tokens
checked by `scripts/check-colors.mjs`. The parts the tour drives today:

- **Settings** opens only from the rail. The open state is local to `NavRail`
  (`NavRail.tsx:166`), and the modal's only deep link is `initialCluster`
  (`UserSettingsModal.tsx:252-266`). Its default section is `"profile"`.
- **Inference state** comes from `GET /settings/inference` through `useAgentSettings`
  (`ui/lib/agent-settings.ts`). It already reports what the tour needs: `providers[].default_model`,
  `has_credential`, `model` and `model_is_default`.
- **The active project** is a localStorage pointer, `vista.activeProject.v1` (`ui/lib/projects.ts:73`).
- **`FilePreview`** builds its URL itself (`fileUrl()`, `FilePreview.tsx:49-56`), so it can
  only preview files the backend serves.
- **The chat composer** can already be prefilled: the empty-conversation openers call
  `setInput` (`ui/app/page.tsx:78`, `:1633`).
- **Anchors:** the UI has two `data-testid`s. The rail entries are `a.nav-rail-entry[aria-label]`,
  and two of them are both labelled "Knowledge Bases".
- **Schema:** schema changes must be additive (`db/schema_check.py`). A nullable column is
  added at startup by `_add_missing_columns`.

## Goals / Non-Goals

**Goals:**
- Every rule about which step is current lives in one pure function, testable without a DOM.
- The tour drives the real UI. Its only fake is the example files.
- The tour adds no product behaviour beyond what the specs describe. When it is off or
  finished, every page behaves as it does today.

**Non-Goals:**
- A general-purpose tour framework, or steps defined in configuration.
- Per-step documentation links (see the proposal's Non-goals).

## Decisions

### 1. Step logic is a pure function over observed state

`ui/lib/onboarding.ts` exports `nextTourStep(state): TourStep | null`. Its input is:
- `stored`: the persisted `{status, step}`;
- `inference`: `{hasCredential, model, modelIsDefault, providerDefault}`;
- `activeProject`;
- `events`: a set of tour-relevant actions seen during this session, for example
  `kb-opened`, `skill-opened`, `example-image-opened`, `example-md-opened`, `hpc-card-opened`
  and `turn-succeeded`;
- `skipped` and `acknowledged`: the steps and sub-steps passed with Skip or Next.

It returns the step to show, and its sub-step for multi-coachmark steps, or `null` when the
tour is closed.

The steps that depend on app state (project, credential, model) read it, so they complete
at once when already done, as the "Already configured" scenarios require. The other steps
read `events`. "Model chosen" is `model !== null && !modelIsDefault`, so a provider default
does not count, matching "walk the researcher through picking a model".

*Alternative:* a step machine that advances by imperative callbacks. It's simpler to start,
but state-derived completion then has to be re-checked by hand on every change, and that is
where a resumed or replayed tour goes wrong.

### 2. A `TourProvider` on the app shell, with components reporting events

`TourProvider` wraps `.app-shell` in `ui/app/layout.tsx`. It holds the session's events,
reads the inference state and the active project, computes `nextTourStep`, persists `step`
whenever it changes, and renders the coachmark. Pages report events through
`useTourEvent()`, a no-op when no tour is running. For example, the knowledge-base explorer
calls `report("kb-opened")` when a knowledge base's detail view opens.

Navigation the step needs, such as `/projects` for the project step and `/files` for the
files step, is done by the provider through `router.push` when the step starts. The
researcher's clicks then do the rest.

A replay is `/settings` changing, while the provider is mounted, to
`{status: "in_progress", step: "welcome"}` from anything else, whoever wrote it. It clears
the session's events, skips and acknowledgements, so the tour really starts over. The off
switch (§4) only stops the tour opening by itself: with it set, a never-started tour or one
left in progress by an earlier session stays closed and nothing is written, but a replay in
this session still opens the tour.

### 3. driver.js for drawing, not for flow

The dimmed backdrop, the cut-out, placement, and re-placement on scroll and resize use
`driver.js`, pinned exactly. 1.9.0 is the latest release at least two weeks old at the time of
writing; check again at implementation. The provider calls `driver.highlight()` for the current
step only, and does not use driver.js's own step list or its Next and Back buttons. Flow belongs
to `nextTourStep`, and the popover's buttons (Next, Skip this step, Exit tutorial) are VISTA's,
rendered into driver.js's popover via `onPopoverRender`. Next appears only on a coachmark that
Next completes, which `completesOnNext(step, subStep)` in `ui/lib/onboarding.ts` says. A
coachmark that waits for an action offers only Skip and Exit. driver.js hides its footer when
it has no buttons of its own (`showButtons: []`), so `onPopoverRender` shows it again for
VISTA's.

Each coachmark's title, text and optional link come from `coachmarkFor(step, subStep, ...)` in
`ui/lib/onboarding-copy.ts`, kept apart from the step logic so wording can change without
touching it.

Its CSS is overridden in `globals.css` with theme tokens, so light and dark mode both work and
`check-colors.mjs` passes. Escape maps to Exit through the provider's own `keydown` listener on
the document. driver.js's keyboard control is off, because in `highlight()` mode its arrow keys
would end the highlight and exit the tour. Targets are found by `data-tour` attributes, and a
missing target waits for it to mount, through a `MutationObserver` with a short timeout. Pages
render asynchronously, and a missing anchor must not stall the tour.

While a coachmark is up, driver.js makes everything except the highlighted element
unclickable. A step that needs the researcher to use more than one control, such as the
provider choice and key field in Settings, highlights the element that contains them all.

*Alternatives:*
- Hand-rolled coachmarks: placement, scroll and resize handling, and focus handling are code
  we would own for no product gain.
- react-joyride: it brings its own positioning dependency and styling system, which fight
  the theme tokens.

### 4. Tour state: a nullable JSON column, and an off switch from backend config

- **Storage:** `LocalSettingsTable.onboarding_tour: dict | None` holds
  `{"status": "in_progress" | "finished" | "exited", "step": "<id>"}`. `None` means never
  started, which is what an upgraded installation has, so it sees the tour once, as decided.
  It is added to `LocalSettingsPublic` and `LocalSettingsUpdate`, and written through the
  existing `PUT /settings`. A JSON column means new step ids never need a schema change.
- **Off switch:** `Settings.onboarding_tour: bool = True`, set by
  `VISTA_BACKEND_ONBOARDING_TOUR`, is reported read-only in `LocalSettingsPublic` as
  `onboarding_tour_enabled`. It belongs to the backend rather than the UI build because
  packaged UIs are prebuilt: a `NEXT_PUBLIC_*` flag would be baked in, and screencasts of a
  package could not turn the tour off.

*Alternative:* localStorage. The origin includes the UI's port, so the tour would reappear
whenever the port changed.

### 5. Settings opens through a small store

`ui/lib/settings-modal.ts` exposes `openSettings(section?)`, `closeSettings()` and
`useSettingsModal()`, the same snapshot-store pattern as `requestModelChoice` in
`agent-settings.ts`. `NavRail` renders the modal from the store instead of from local state.
Its cluster deep link becomes `openSettings(cluster)`. `UserSettingsModal` gains
`initialSection?: Section`, which generalises `initialCluster` and also sets
`pane="section"` in narrow windows.

The tour calls `openSettings("agent")`, the model provider section. It sees the credential
being saved through `refreshAgentSettings`, which the autosave already triggers.

### 6. Example files go through `FilePreview`'s new `src` prop

`FilePreview` gains `src?: string`. When it is set, preview and download use it instead of
`fileUrl()`. `useCappedText` and `<img>` already work with `data:` and `blob:` URLs. The three
examples live in `ui/lib/onboarding-examples.ts` as `ListedFile` entries plus their content
(markdown and CSV as strings, the chart as an SVG data URL, so no binary is committed).

The Files page merges them into its listing only while `tour.step === "files"`. Example rows
carry `example: true`, which hides Download, Delete and Show in chat and shows an "Example"
badge. Nothing is sent to the backend, so the agent never sees them. The report is markdown
with headings and a GFM table. Markdown previews show embedded images as links, so the chart
is its own file.

### 7. The first question reuses the composer and the existing error kinds

The step calls the chat page's existing prefill path, the one the openers use. Completion is a
`turn-succeeded` event, reported where the chat page already learns that a run finished
without error. A failure is classified from what the page already has: `refused-send`'s
`inference_not_configured`, `holdSendWithoutModel`, or a stream error. The coachmark then
points at the settings button or the model picker. This adds no new error classification.

### 8. Rail expansion is borrowed, not saved

While the tour is open, `NavRail` renders expanded and ignores its stored preference without
writing to it. The stored `vista.navRail.collapsed.v1` is untouched, so closing the tour
restores the researcher's choice, and ui-shell's "remember the collapse choice" is unchanged.

## Risks / Trade-offs

- **[Anchors rot as pages change]** → `data-tour` attributes are named for the tour, so a
  refactor keeps them. A Playwright test walks the whole tour on a fresh stack, with a fake
  model, and fails on a missing anchor.
- **[The tour fights other overlays]** (the model menu, the settings modal, skill detail) →
  each step that opens an overlay highlights inside it. driver.js's `stagePadding` and
  `popoverClass` give the modal enough z-index. Tested in e2e.
- **[An existing researcher is interrupted mid-work after upgrading]** → accepted, as decided.
  State-derived steps complete at once for them, and Exit is always one click.
- **[The suggested model is wrong for the provider]** → the suggestion is read from the
  backend's `default_model` and never hardcoded in the UI, so it follows `PROVIDER_PRESETS`.
- **[The starter question cites nothing on a development checkout without the AI Safety
  corpus]** → accepted (non-goal). Packages always ship the corpus.
- **[The screencast fake backend and Playwright fixtures start fresh]** → they set
  `onboarding_tour_enabled: false`, or the env var, in their setup. There is one task for
  each harness.

## Migration Plan

Additive only: the new nullable column is added by `_add_missing_columns` on first start. No
reset prompt. Rollback is reverting the change: an older build ignores the extra nullable
column (`schema_check` ignores unknown nullable columns).

## Open Questions

- The exact wording of each coachmark. It can be settled in review of the UI without
  changing the steps.
