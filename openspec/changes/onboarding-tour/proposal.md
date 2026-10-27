## Why

Hackathon feedback asked for a "Quick-start tour with automated configuration guide" (#84).
A researcher opening VISTA for the first time gets no guidance: there is no active project, no
inference credential, and the only prompt is the model picker opening after a send has already
failed. They have to find Settings, the project list, knowledge bases, skills and files on
their own.

## What Changes

- On first launch, a guided tour opens over the application: floating coachmarks with the
  rest of the window dimmed, each with **Skip this step** and **Exit tutorial**.
- Each step waits for the researcher to do the thing it teaches, rather than only explaining
  it:
  1. **Welcome to VISTA**.
  2. **Open a project**: open the AI Safety in Autonomous Labs project, which lands in chat.
  3. **Connect a model**: choose a provider and add its credential in Settings, then pick a
     model in the chat's model picker. The coachmark suggests the provider's default
     (`claude-sonnet` on AmSC i2 and MAG, `gpt-oss-120b` on OLCF Inference, none on Custom).
     Any model the researcher picks completes the step.
  4. **Knowledge bases**: open AI Safety Papers, then a pointer to adding files.
  5. **Skills**: open a skill to read it, then pointers to Load and Import….
  6. **Files**: example files, shown only during the tour and marked "Example" (a markdown
     report, a chart image, a CSV), opened in the real previewer.
  7. **HPC**: open the rail's HPC status card, then a pointer to where clusters are connected
     in Settings.
  8. **First question**: a starter question prefilled in the composer, which completes when the
     answer finishes. If the turn fails, the coachmark points at the fix.
- Tour progress is kept in VISTA's local settings. Quitting mid-tour resumes at the same step;
  **Exit tutorial** and finishing are final. Every installation sees the tour once, existing
  ones included.
- Settings gains **Replay tutorial** and a **Documentation** link to
  <https://genesis-vista.github.io/>. The last tour step links to the same documentation.
- Settings can be opened from outside the rail at a named section.
- `VISTA_BACKEND_ONBOARDING_TOUR=false` turns the tour off, for automated tests and screencasts.

## Capabilities

### New Capabilities

- `onboarding-tour`: when the tour opens, what it stores, its steps and what completes each,
  skipping, exiting, resuming and replaying, example files, and turning it off.

### Modified Capabilities

- `settings-modal`: Settings opens at a named section when asked to by another part of the
  application, and gains the replay and documentation entries.

## Impact

- Backend: a nullable JSON column for tour state on `local_settings` (additive, so it passes
  `db/schema_check.py`), exposed through `/settings`; a `Settings` flag for turning the tour
  off; tests.
- UI:
  - a new tour module, with its step logic as a pure function, and a tour component on the
    app shell;
  - `UserSettingsModal` opens at a requested section, and its open state moves out of
    `NavRail` so other parts of the application can open it;
  - `FilePreview` takes an optional source URL, so example files preview without a backend;
  - `data-tour` anchors on the rail, the project card, the model picker, the knowledge base,
    skill and file controls, and the composer;
  - vitest and Playwright tests.
- Dependency: `driver.js`, pinned to an exact version, restyled to VISTA's theme tokens so
  `scripts/check-colors.mjs` passes.
- Test harnesses: Playwright fixtures and the `vista-screencast` fake backend turn the tour off.

## Non-goals

- Syncing the repository's `docs/` with genesis-vista.github.io, or per-step "Read more"
  links. The documentation is not yet granular enough for them.
- The Hypothesis Lab and the Skill Hub, which need setup that a fresh install lacks.
- Adapting the tour when the AI Safety Papers knowledge base is missing (a development
  checkout without vista-data). Packages always include it.
- An Electron Help menu.
