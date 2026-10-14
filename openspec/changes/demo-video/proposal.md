# Proposal

## Why

VISTA needs public demo videos (the GitHub README once the mirror is public,
social posts, talks), and today each one is a hand-made screen recording that
goes stale with the next UI change. A spike
(`~/proj/scratch/vista-demo-spike`, 2026-10-14) showed Claude can produce a
polished video from VISTA's real UI against a fake backend: scripted agent runs
streamed over SSE, a synthetic cursor, captions, camera zooms and title cards,
assembled with ffmpeg. That spike replays one fixed script with hand-written
answers and made-up figures. What's wanted is a toolkit Claude can use to make
*any* VISTA video a researcher asks for, built from real sessions.

## What Changes

- A new project skill, `.claude/skills/vista-demo/`, holding the instructions
  Claude follows to make a video and every script it uses. It is published with
  the repository (through the GitHub mirror) and is not run by CI.
- **Capture.** A capture proxy sits between the UI and a live backend and
  records every backend response, including agent runs as they stream. A small
  recorder in the page records what the researcher does by meaning (which
  element was clicked, what was typed), never as mouse coordinates. Either the
  researcher drives the live session by hand, or Claude drives it from a
  storyboard the researcher approved. Both produce the same thing: one editable
  **video script** of UI steps and agent runs in order.
- **Edit.** The script is plain text that the researcher or Claude edits: cut
  failed tool calls and retries, shorten answers, reorder or drop steps, add
  captions, camera zooms, pointing beats and pauses. Any edit is allowed; the
  original capture is kept beside the edited script so the difference stays on
  record.
- **Replay and record.** A replay backend serves the captured responses by path
  and the agent runs in recorded order, and refuses anything it has no answer
  for. The recorder plays the script in a headless browser: it finds each
  element afresh, draws an eased cursor between them, types at a human pace,
  and records frames from the browser's screencast.
- **Assemble.** Frames become a 1920×1080 H.264 MP4 framed in a neutral window
  mock-up on a gradient backdrop, plus a short README clip (GIF and WebM).
  Videos are silent with on-screen captions.
- **Framing.** The opening card is "VISTA" over what the name stands for, and
  cards carry the VISTA name and icon only, with no lab or programme branding. Each project
  shown is a named use case, and the default arc ends on an HPC run.
- Covered pages: Projects, Chat, Skills, Datasets and Knowledge Bases.
- Captures, scripts and videos live outside the repository, under
  `~/.vista/demo-captures/<video>/`, and are never committed. The one
  exception is the GIF the README shows, copied by hand to
  `docs/figures/readme-demo.gif`.

No change to VISTA's own behaviour: the UI, backend and MCP servers are
untouched, and the toolkit reaches them only through their existing
configuration (the UI's backend URL).

## Capabilities

### New Capabilities
- `demo-video`: making VISTA demo videos from real sessions. Covers capture
  (backend responses and researcher actions), the editable video script,
  hermetic replay, recording, assembly into the two output formats, how
  captures are kept out of the repository, and the framing rules for public
  videos.

### Modified Capabilities
<!-- none -->

## Impact

- New: `.claude/skills/vista-demo/` (SKILL.md, Node scripts, card and frame
  templates). Its own `package.json` pins Playwright to the version `ui/` uses,
  so the browsers already installed for the UI's tests are reused.
- Needs ffmpeg on the machine making videos (Homebrew on macOS). VISTA itself
  does not.
- Capture needs a live stack (`./launch.sh logs --no-electron`) for the backend
  and MCP server. The toolkit runs its own copy of the UI on another port,
  pointed at the capture proxy, so the researcher's stack keeps running.
- The toolkit builds the UI in a private copy of `ui/` and runs its standalone
  server, so it never writes `ui/.next` and can run beside a `./launch.sh`
  stack in the same checkout.
- No CI job: a broken toolkit shows up as a clear error during a video's
  rehearsal step.
