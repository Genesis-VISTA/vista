# Design

## Context

See proposal.md for why. The spike (`~/proj/scratch/vista-demo-spike`) settled
the mechanics this design keeps:

- The browser talks only to the UI's `/api` routes, and every one of them is a
  thin proxy to `VISTA_BACKEND_URL` (`ui/app/api/_backend.ts`). Nothing in the
  UI calls the backend from a server component. So a server at the backend URL
  sees all traffic, and replacing it makes the UI hermetic.
- Agent runs are SSE with real `event:` lines, drawn by
  `ui/lib/run-renderer.ts`: `run_started`, `part_start` / `part_delta` /
  `part_end`, `final_result`, `function_tool_call` / `function_tool_result`,
  `agent_run_result`, `run_finished`. A tool return whose content holds a
  `binary` image is drawn in the Artifacts panel.
- Playwright's `page.route` cannot stream a response, and `recordVideo` is
  low-bitrate VP8. The spike used a Node server for streaming and the CDP
  screencast for frames, which looked sharp.
- `scripts/launch.sh:81` hard-codes `VISTA_BACKEND_URL`, so capture cannot
  redirect the stack's own UI. `ui/next.config.mjs` builds with
  `output: 'standalone'`, so a built UI runs as `node server.js` from any
  directory once `.next/static` and `public/` are copied beside it.
- A production build into `ui/.next` kills a running `next dev` in the same
  checkout (seen 2026-10-07).

## Goals / Non-Goals

**Goals:**
- One toolkit, driven by Claude through the skill's instructions, for any video
  of the covered pages.
- Capture and replay share one server and one on-disk format, so a capture
  replays with no conversion other than the editable script.
- Every failure (unanswered request, missing element, unscrubbed secret) is an
  error naming what is missing, raised during rehearsal.

**Non-Goals:**
- Recording the Electron window, or any operating system UI (file pickers,
  dialogs). Uploading a dataset appears as a scripted "the file is there" step.
- Audio, narration, music; square or vertical crops.
- Hypothesis Lab and Settings pages (debate streams, credential forms).
- Any change to the UI, backend, MCP servers or launch scripts.
- CI. The toolkit has a manual self-test only.

## Decisions

### D1. One skill folder with a single CLI

```
.claude/skills/vista-demo/
  SKILL.md               how Claude makes a video, step by step
  package.json           playwright (pinned to ui/'s version), yaml
  bin/vista-demo.mjs     the CLI
  lib/server.mjs         capture proxy and replay server
  lib/script.mjs         capture → script conversion, script → replay plan
  lib/actions.mjs        in-page action recorder (capture)
  lib/player.mjs         plays a script: cursor, typing, captions, camera
  lib/frames.mjs         CDP screencast → frames + ffconcat
  lib/assemble.mjs       ffmpeg: frame, cards, MP4, README clip
  lib/ui.mjs             private UI build and server
  lib/scrub.mjs          credential removal and final scan
  templates/card.html    title and chapter cards
  templates/frame.html   window frame and backdrop
  selftest/              one tiny hand-written, public-safe script
```

Commands: `ui` (build or reuse the private UI), `capture` (by hand or from a
storyboard), `script` (regenerate `script.original.yaml` from a capture),
`rehearse` (fast replay, screenshots and a contact sheet, no video), `record`,
`assemble`, `selftest`. Each video is a folder under
`~/.vista/demo-captures/<video>/`.

*Alternative:* scripts in `tools/demo-video/` with a thin skill. Rejected in
the grilling: one folder is easier to find and move, and CI coverage was not
wanted.

### D2. The video's folder

```
<video>/
  capture/               written once by capture, never edited
    responses.jsonl      every non-run response: seq, step, method, path, query, status, body
    runs/<n>.jsonl       one file per agent run: events with arrival times
    actions.jsonl        recorded UI actions (hand capture)
  assets/                figures and large tool results, referenced from the script
  script.original.yaml   generated from capture/, never edited
  script.yaml            the edited script (starts as a copy)
  out/                   frames/, video.mp4, readme.gif, readme.webm, contact.jpg
```

`git diff --no-index script.original.yaml script.yaml` shows every edit.
That's the record the editing policy relies on.

### D3. Script format: YAML steps

YAML because both people and Claude edit it, and it diffs well. A sketch:

```yaml
video: molten-salt-tour
pace: { typing: 1.0, stream_wps: 40, tool_min_s: 1.2 }
steps:
  - card: intro                         # positioning line, verbatim
  - goto: /projects
  - chapter: "Use case: molten salt properties"
  - caption: [1, "Query the evaluated database", "MSTDB-TP, counted in the sandbox"]
  - click: { role: button, name: Open, within: { text: molten-salt, css: .project-card } }
  - type: { placeholder: "Ask a question", text: "what is the most studied salt in the database?" }
  - click: { role: button, name: /^(Start chat|Send)$/ }
  - run:
      - tool: run_bash
        args: { command: "python -m mstdb count-compositions --top 5" }
        result: "system  compositions\nBeF2-NaF-UF4  81\n…"
      - answer: |
          Based on the number of composition entries …
  - camera: { zoom: latest, scale: 1.35 }
  - wait: run
  - pause: 2.0
  - point: { text: Frontier }
  - spends: hpc                          # the next run submits a job
```

A `run` step is the agent's turn: tool calls with arguments and results, and
answers as whole text (intermediate answers marked `interim: true`). Large
results and images are written to `assets/` and referenced (`result: !asset
phase.png`). Converting a run to steps drops `log` and thinking events and
merges text deltas; replay generates fresh events from the steps, with ids,
ordering and pacing from `pace`.

*Alternative:* store raw events and edit them. Rejected: unreadable, and
pacing would be stuck at whatever the live model did.

### D4. One server, two modes

`lib/server.mjs` listens where the private UI's `VISTA_BACKEND_URL` points.

- **capture:** forwards each request to the live backend (`:8001`) and the MCP
  URL. Streams pass straight through to the UI while they are recorded.
  Responses are scrubbed (D9) before they are written.
- **replay:** serves the script. An agent-run request (`POST
  /projects/<p>/agent/run`) takes the next `run` step and streams events made
  from it. Any other request is answered from `responses.jsonl`.

**State over time.** The same request can get different answers as a session
goes on (the chat-session list is empty, then holds one). Every captured
response is tagged with the **action count**: how many UI actions (goto,
click, type, press) came before it. The player reports the count before each
action (`POST /__demo/action`), and replay answers with the latest capture of
that method + path + query at or before the current count, then any capture of
it, then fails. Captions, pauses, camera moves and runs don't count, so adding
or removing them never shifts a response. A request a click triggers can reach
the capture server before the recorder reports the click, so at the end of a
capture the counts are recomputed from timestamps: each action carries the
browser's event time, each response and run its arrival time. Query parameters that carry fresh ids (the
`chat_session_id` the UI was given) are matched through an id map built from
the captured responses that created them.

*Alternative:* match prompts to runs by regular expression, as the spike did.
Rejected: two similar prompts would play the wrong run.

### D5. A private UI build

`lib/ui.mjs` copies `ui/` (minus `node_modules` and `.next`) to
`~/.vista/demo-captures/_ui/<tree-hash>/`, links `node_modules` from the
checkout, runs `next build --webpack` there, and copies `.next/static` and `public/` into
the standalone output. It runs `node server.js` with `PORT=3100`,
`HOSTNAME=127.0.0.1` and `VISTA_BACKEND_URL` set to the server. The tree hash
of `ui/` (`git rev-parse HEAD:ui` plus a dirty-file digest) names the build, so
an unchanged UI is reused and a changed one rebuilt.

Webpack, not the default Turbopack: Turbopack refuses a `node_modules` symlink
that points outside the project ("points out of the filesystem root"), and
copying `node_modules` (about 500 MB) per build is the costlier fix. Videos are
therefore recorded from a webpack build of the same source VISTA ships with
Turbopack; both render the same app code.

*Alternative:* `next start` in `ui/`. Rejected: it writes `ui/.next` and
breaks a running stack (Context).

### D6. Recording actions by meaning

`lib/actions.mjs` is injected into the capture browser. On each `click`,
`input` and Enter `keydown` it derives a locator for the target, preferring in
order: role and accessible name, label, placeholder, visible text, then
the same scoped within the nearest ancestor that makes it unique (a card, a
section). It checks that the locator resolves back to that one element before
recording it. Typing is merged into one `type` step per field, ending at blur
or Enter. Pointer movement is never recorded; the player generates it (D7).

*Alternative:* Playwright `codegen`. It produces a TypeScript test rather than
steps we can merge with agent runs, and its locator choice is not tunable.
Its preference order is the one D6 copies.

### D7. The player

`lib/player.mjs` runs a script in one of three ways: against the replay server
(`rehearse`, `record`), or against the capture server with a browser the
researcher drives (`capture --by-hand`, the player only injects D6), or one it
drives itself from a storyboard (`capture --storyboard`).

- **Overlay.** Cursor, click ripple, captions and cards are drawn in a layer
  appended to `<html>`, outside `<body>`. The camera is a CSS transform on
  `<body>` with an eased transition, so zooms stay sharp and the overlay is not
  scaled.
- **Captions.** A caption has two slots, top-centre and bottom-centre. The
  player puts it in whichever slot misses the element being typed into or
  clicked and the camera's target. The bottom-left placement the spike used hid
  the composer.
- **Element lookup.** Each step's locator is resolved when the step runs. If
  it doesn't resolve within a few seconds, the run fails, naming the step
  number and locator.
- **Spending guard.** In `capture --storyboard`, the player stops at a
  `spends:` step and exits (code 3) with a message unless `--allow-spend` is
  given. The skill tells Claude to ask the researcher, then capture again with
  `--again --allow-spend`. The re-run starts over rather than resuming:
  resuming would mean rebuilding the live page's state mid-session, and
  starting over repeats only the model turns before the stop, never an HPC
  step.

### D8. Frames and assembly

`lib/frames.mjs` records CDP screencast frames (JPEG, quality 92, at
1440×810 CSS pixels × 4/3 = 1920×1080) with their timestamps, and writes an
ffconcat list with per-frame durations. `lib/assemble.mjs`:

- renders `templates/frame.html` once to a 1920×1080 PNG (rounded, neutral
  frame titled "VISTA", soft gradient backdrop) and overlays the UI frames,
  scaled into it; cards stay full-bleed;
- writes `video.mp4` with libx264 (`-crf 17`, `yuv420p`, `+faststart`) and no
  audio stream;
- cuts the README clip from a `readme:` marker pair in the script (default: the
  first scene), at most 15 s and 960 px wide, as `readme.webm` (VP9) and
  `readme.gif` (two-pass palette).

### D9. Scrubbing

Before a response or run event is written, `lib/scrub.mjs` replaces values of
fields whose names match `token|key|secret|password|credential|cookie|
authorization` (case-insensitive) with `"<scrubbed>"`, and drops
authorization and cookie headers. After capture it scans the whole folder for
JWT-shaped strings (`eyJ…\.…\.…`) and long base64 or hex runs outside image
data, and fails the capture if any remain, naming the file and line. The skill
has Claude list the scrubbed fields to the researcher.

## Risks / Trade-offs

- [A UI or API change makes old captures replay wrongly] → Replay fails on
  any request it can't answer, and the player fails on a missing element. The
  fix is to re-capture; scripts are cheap to regenerate from a fresh capture
  and the old edits can be re-applied from the diff.
- [Scrubbing misses a secret under an unexpected field name] → The final
  pattern scan, and captures stay outside the repo (D2). Hypothesis Lab and
  Settings, where credentials are densest, are out of scope.
- [An edited video misstates what VISTA did] → The policy allows any edit; the
  kept original and the diff are the record. The skill shows the researcher a
  summary of factual edits (changed numbers, results or figures) before
  `record`.
- [Captured state depends on the researcher's data] (project names, chat
  history) → The skill tells Claude to capture in a scratch project, or with a
  copied database, before showing personal data in a public video.
- [Screencast frame timing is uneven under load] → Frames carry their own
  timestamps and the assembly uses per-frame durations, so slow frames cost
  smoothness, not sync.
- [Standalone output layout changes in a Next upgrade] → `lib/ui.mjs` checks
  for `server.js` and `.next/static` after the build and fails with what's
  missing.
- [Playwright pin drifts from `ui/`] → `selftest` checks that the pinned
  version matches `ui/package-lock.json` and says which one to change.
