## 1. Tour state in the backend

- [x] 1.1 Add `onboarding_tour: dict | None` (nullable JSON) to `LocalSettingsTable` in `backend/src/vista_backend/db/schemas.py`, and to `LocalSettingsPublic` and `LocalSettingsUpdate`. Validate writes to `{"status": "in_progress" | "finished" | "exited", "step": <str>}` and reject anything else with 422. Verify in `backend/tests/test_local_settings.py`: a fresh row reads `null`; a valid `PUT /settings` round-trips; a bad status returns 422. Verify in `backend/tests/test_schema_check.py` that a database made before the column passes `schema_problems(..., assume_additive=True)`
- [x] 1.2 Add `onboarding_tour: bool = True` to `Settings` in `backend/src/vista_backend/config.py`, set by `VISTA_BACKEND_ONBOARDING_TOUR`, and report it read-only as `onboarding_tour_enabled` in `LocalSettingsPublic`. Verify in `backend/tests/test_settings_defaults.py`: it defaults to true, `VISTA_BACKEND_ONBOARDING_TOUR=false` makes it false, and a `PUT` cannot change it
- [x] 1.3 Add both fields to the TypeScript types in `ui/lib/settings.ts`. Verify with `cd ui && npm run lint`

## 2. Opening Settings from anywhere

- [x] 2.1 Add `ui/lib/settings-modal.ts` (`openSettings(section?)`, `closeSettings()`, `useSettingsModal()`), following the snapshot-store pattern of `requestModelChoice` in `ui/lib/agent-settings.ts`. Move `NavRail`'s `settingsOpen` and `settingsCluster` state onto it, and turn the cluster deep link into `openSettings(cluster)`. Verify with a vitest unit test of the store, and confirm `ui/e2e-hermetic/shell.spec.ts` still passes (the rail button and the cluster cards open Settings as before)
- [x] 2.2 Add `initialSection?: Section` to `UserSettingsModal`, which generalises `initialCluster` and sets `pane="section"` in narrow windows. Verify with a vitest render test: `initialSection="agent"` shows "Model provider", and does so at a narrow width
- [x] 2.3 In the Research profile section, add **Replay tutorial** (writes `onboarding_tour: {status: "in_progress", step: "welcome"}`, closes Settings, and starts the tour) and a **Documentation** link to `https://genesis-vista.github.io/`. Check that `electron/src/routing.js` sends that origin to the system browser. Verify with a vitest test that Replay issues that `PUT` and closes the modal, and with `cd electron && npm test` that the routing test covers the docs origin

## 3. Step logic

- [ ] 3.1 Add `ui/lib/onboarding.ts` with the `TourStep` ids (`welcome`, `project`, `model`, `knowledge-bases`, `skills`, `files`, `hpc`, `first-question`), each step's sub-steps, the event names, and the pure function `nextTourStep(state)` (design.md §1). "Model chosen" is `model !== null && !modelIsDefault`; `providerDefault` gives the suggestion. Verify with `ui/tests/onboarding.test.ts` covering:
  - a null state returns `welcome`;
  - with an active project, `project` completes at once;
  - i2 with a credential but the default model stays on the model picker sub-step;
  - OLCF with a credential and a chosen model completes `model` at once;
  - skipping `knowledge-bases` moves to `skills`;
  - `files` needs both `example-image-opened` and `example-md-opened`;
  - `first-question` completes only on `turn-succeeded`;
  - `exited` and `finished` return `null`;
  - an in-progress state at `skills` resumes at `skills`.

## 4. Tour shell

- [ ] 4.1 Add `driver.js` to `ui/package.json`, pinned exactly to the newest release that is at least two weeks old (1.9.0 when this was written). Verify with `cd ui && npm install` and `npm run build`
- [ ] 4.2 Add `ui/components/TourProvider.tsx`, mounted around `.app-shell` in `ui/app/layout.tsx`:
  - it reads settings, inference state and the active project, holds the session's events, computes `nextTourStep`, and persists `step` and `status` through `updateSettings`;
  - it exposes `useTour()` and `useTourEvent()`, which do nothing when no tour is running;
  - it does not open when `onboarding_tour_enabled` is false, or when the status is finished or exited.

  Read the Next docs in `ui/node_modules/next/dist/docs/` on client components in layouts first. Verify with vitest: a null state renders the welcome step; `onboarding_tour_enabled: false` renders nothing; Exit writes `exited`
- [ ] 4.3 Render each step with `driver.highlight()` on its `data-tour` target (design.md §3). The popover body and its buttons (Next, Skip this step, Exit tutorial) are VISTA's, rendered through `onPopoverRender`; Escape exits; a missing target waits through a `MutationObserver` with a timeout; a step with no target uses a centred popover. Restyle driver.js in `ui/app/globals.css` with theme tokens. Verify with `node scripts/check-colors.mjs` (via `./scripts/ci-local.sh ui lint`) and `ui/e2e-hermetic/theme.spec.ts` in light and dark
- [ ] 4.4 Render `NavRail` expanded while the tour is open, without writing `vista.navRail.collapsed.v1`. Verify with vitest: with the stored value collapsed, the rail is expanded during the tour and collapsed after it, and the stored value is unchanged

## 5. Steps

- [ ] 5.1 Project step: add `data-tour` to the AI Safety project card's **Open** button in `ui/app/projects/page.tsx`. The provider navigates to `/projects` when the step starts. Verify in the e2e spec in 6.1
- [ ] 5.2 Model step: call `openSettings("agent")`; add `data-tour` to the provider radiogroup and the key field in `UserSettingsModal` and to `.model-picker-button` in `ModelMenu`. Name the suggestion from the provider's `default_model` (none for Custom). Verify with vitest: i2 and MAG suggest `claude-sonnet`, OLCF suggests `gpt-oss-120b`, Custom names none
- [ ] 5.3 Knowledge bases step: add `data-tour` to the rail entry for `/knowledge-bases`, the AI Safety Papers list item and **+ Add files**, and report `kb-opened` from `KbDetailView` in `ui/app/knowledge-bases/explorer.tsx`. Verify in the e2e spec in 6.1
- [ ] 5.4 Skills step: add `data-tour` to the rail Skills entry, the skill cards, **Load** and **Import…**, and report `skill-opened` from `openDetail` in `ui/app/skills/page.tsx`. Verify in the e2e spec that the project's skills are unchanged after the step
- [ ] 5.5 Files step:
  - add `src?: string` to `ui/components/FilePreview.tsx`, used for preview and download in place of `fileUrl()`;
  - add `ui/lib/onboarding-examples.ts`: `outputs/summary.md` with headings and a GFM table, `outputs/risk-scores.svg` as a data URL, and `uploads/risks.csv`;
  - merge the examples into the listing in `ui/app/files/page.tsx` only during the `files` step, with an "Example" badge and no Download, Delete or Show in chat;
  - report `example-image-opened` and `example-md-opened` when those examples are opened.

  Verify with new cases in `ui/tests/FilePreview.test.tsx` (a `src` data URL renders an image, and markdown text) and with vitest on the Files page: examples appear only during the step, carry no action buttons, and trigger no network request to `/api/files`
- [ ] 5.6 HPC step: add `data-tour` to the HPC cards and to the open card's settings row in `ui/components/HpcStatusSection.tsx`, and report `hpc-card-opened` from `toggle`. Verify in the e2e spec in 6.1
- [ ] 5.7 First question:
  - prefill the starter question from the spec through the chat page's existing composer prefill (`setInput`, `ui/app/page.tsx`), and add `data-tour` to Send;
  - report `turn-succeeded` when a run finishes without error;
  - on `inference_not_configured`, `holdSendWithoutModel` or a stream error, switch the coachmark to the fix and point at the settings button or the model picker;
  - on completion, show the finish coachmark with the documentation link and write `finished`.

  Verify with vitest of the failure mapping, and in the e2e spec in 6.1

## 6. Harnesses and end-to-end

- [ ] 6.1 Add `ui/e2e-hermetic/onboarding.spec.ts`, whose stubs return `onboarding_tour: null`, and walk every step against stubbed responses: welcome; open the AI Safety project; add a credential, then pick a model; open AI Safety Papers; open a skill; open the example image and report; open the Odo card; send the starter question and see the finish coachmark. Also cover skipping a step, Exit (a reload shows no tour), and resuming (a reload mid-tour reopens the same step). Verify with `cd ui && npm run test:e2e:hermetic`
- [ ] 6.2 Set `onboarding_tour_enabled: false` in the shared `SETTINGS` fixture in `ui/e2e-hermetic/fixtures.ts`, so the other specs never see the tour. Set `VISTA_BACKEND_ONBOARDING_TOUR=false` for `ui/e2e/smoke.spec.ts` and the Electron e2e. Verify that `npm run test:e2e:hermetic` passes in full, and that `cd electron && npm run test:e2e` passes
- [ ] 6.3 In `.claude/skills/vista-screencast/lib/server.mjs`, force `onboarding_tour_enabled: false` on replayed `/settings` answers unless the script asks for the tour, and say so in `SKILL.md`. Verify with the skill's `selftest`

## 7. Verification

- [ ] 7.1 Run `./scripts/ci-local.sh lint` and `./scripts/ci-local.sh backend ui electron test`; all pass
- [ ] 7.2 Manual (needs a live provider): with a fresh data directory, run `./launch.sh logs`, walk the whole tour on i2 or OLCF, and confirm the first answer cites the AI Safety papers. Quit mid-tour and relaunch to see it resume. Replay from Settings. Open Documentation and see it open in the system browser. Then relaunch with `VISTA_BACKEND_ONBOARDING_TOUR=false` and see no tour
