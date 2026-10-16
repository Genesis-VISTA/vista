---
name: vista-screencast
description: >-
  Screencasts of VISTA: capture a real session, edit it as a script, and replay
  it into a polished recording. Use when asked for a demo or tutorial video,
  screen recording, walkthrough or README clip of VISTA, or to change one
  already made.
---

# VISTA screencasts

A video is a **script** (`script.yaml`) replayed against VISTA's real UI with a
fake backend, so every take is identical and nothing live is touched. The
script comes from a **capture** of a real session, which you then edit. Each
video is a folder under `~/.vista/screencasts/<video>/`:

```
capture/                what the live session recorded; never edited
  responses.jsonl       the backend's answers, replayed by path
  runs/<n>.jsonl        each agent run's events
  actions.jsonl         what the researcher did (hand captures)
  storyboard.yaml       the storyboard played (storyboard captures)
script.original.yaml    the script as captured; never edited
script.yaml             the script you edit (see SCRIPT.md)
assets/                 figures the runs returned
out/                    contact.jpg, frames/, video.mp4
```

Videos are public by default: they and their folders stay out of the repository,
which mirrors to a public GitHub repo. The tools refuse to write inside it.
The one exception is the README's own clip: when the researcher picks a video
for the README, copy its `out/readme.gif` over `docs/figures/readme-demo.gif`
and commit that file alone.
`vista-screencast` below means `node .claude/skills/vista-screencast/bin/vista-screencast.mjs`.

## Steps

1. **Set up once per machine.** `npm ci` in this skill's folder, `npm ci` in
   `ui/`, and ffmpeg on the PATH (macOS: `brew install ffmpeg`). Done when
   `vista-screencast selftest` writes `~/.vista/screencasts/_selftest/out/video.mp4`.
2. **Agree the storyboard with the researcher.** Scenes in order, each with its
   caption, the prompts typed, and what the agent should show. Apply the
   framing rules below. Done when the researcher has approved it.
3. **Capture it.** You run every command; the researcher's only part is
   clicking through VISTA in the window the capture opens. Start each
   long-running command as a background job so you're told when it exits.
   Capture runs against a live stack: start `./launch.sh logs --no-electron`
   in the background and wait until `http://127.0.0.1:8001/openapi.json`
   answers (the tools run their own UI on :3100, so the stack's own UI stays
   usable). Whatever the capture shows can end up in a public video, so
   capture in a scratch project or against a copied database, not the
   researcher's real history. Then either:
   - **the researcher drives:** start `vista-screencast capture <video>
     --by-hand` in the background. A first run builds the UI for about a
     minute; when it prints `use VISTA in the window`, a browser window is
     open on the researcher's screen. Tell them it's open, list the
     storyboard's clicks and prompts, and that closing the window ends the
     capture. The command exits when they close it;
   - **you drive:** write the storyboard as a script with `run: live` where
     the agent answers and `spends: hpc` before any prompt that submits a job
     (see [`SCRIPT.md`](SCRIPT.md)), and run
     `vista-screencast capture <video> --storyboard <file>`.

   A storyboard capture stops at a `spends:` step (exit code 3). Ask the
   researcher; only if they agree, run it again with `--again --allow-spend`.
   Done when capture prints `captured N run(s)` and its `scrubbed:` line; tell
   the researcher which fields were scrubbed. A capture that fails its
   credential scan names the file and line; nothing from it is used until the
   line is removed and `vista-screencast script <video>` passes.
4. **Edit the script.** Edit `script.yaml`: cut failed tool calls and retries,
   shorten slow tools (`duration_s`) and long answers, add `chapter:`,
   `caption:`, `camera:` and `pause:` steps (SCRIPT.md, "A scene", shows where
   each goes). Then compare with `git diff --no-index
   ~/.vista/screencasts/<video>/script.original.yaml
   ~/.vista/screencasts/<video>/script.yaml` and list every **factual edit**
   for the researcher: a changed number, result, figure or claim, including a
   cut that drops a caveat or qualifier, as opposed to timing, formatting and
   cuts that leave the meaning whole. Done when the researcher has seen that
   list.
5. **Rehearse until clean.** `vista-screencast rehearse <video>` plays fast and writes
   `out/contact.jpg`, one screenshot per step. Any failure names the step and
   what's missing: a locator nothing matches, a request the capture can't
   answer, a run the UI never started. Fix the script and rerun. Done when it
   finishes and every tile of the contact sheet shows what the storyboard from
   step 2 says.
6. **Record and assemble.** `vista-screencast record <video>`, then
   `vista-screencast assemble <video>`, which writes `out/video.mp4` (the UI in a
   neutral VISTA window on a gradient backdrop, cards full-bleed) and the
   README clip `out/readme.webm` and `out/readme.gif`: the first scene, or
   what lies between `readme: start` and `readme: end` steps, at most 15 s.
   `out/frames/marks.json` gives each step's
   start in seconds; pull a still a second or two into each `type` and
   `camera` step (`ffmpeg -ss <s> -i out/video.mp4 -frames:v 1 still.jpg`) and
   look at it. Done when every prompt being typed is readable and no zoom cuts
   off what the scene is about.
7. **Hand over.** Give the paths to `video.mp4`, the README clip and
   `script.yaml`, the scrubbed fields, and the factual edits from step 4.

**Without a live stack**, a script can be hand-written instead of captured:
copy the self-test (`cp -R .claude/skills/vista-screencast/selftest
~/.vista/screencasts/<video>`), then make every trace of it yours:
`script.yaml`, `capture/responses.jsonl` (project, conversation titles, each
entry's `action`) and `assets/`, until `grep -rniE 'sine|damped|"demo"'` finds
nothing left over. Every result, number and figure in such a script is a
placeholder: say so in a comment at its top and in the hand-over.

## Framing rules

- `card: intro` opens every video: "VISTA" as the headline and what it stands
  for ("Visual Intelligence for Scientific & Tooling Assistant") beneath. Keep
  it to that; the video itself shows what VISTA does.
- Each project shown is a use case: introduce it with `chapter: <use case>`
  (a "Use case" card with the use case as its title), never as what VISTA is.
- The default arc ends on an HPC step: VISTA carries scientists from a research
  question to running campaigns on OLCF and AmSC resources.
- Cards carry the VISTA name and icon only: no lab, programme or facility
  logos or names.

## Gotchas

- `VISTA_SCREENCAST_HOME` replaces `~/.vista/screencasts` as the home of every
  video (and of the self-test's output and the private UI build), e.g. a
  scratch folder for a trial run. `--help` prints the home in effect.
- The tools build their own copy of `ui/` under `~/.vista/screencasts/_ui/`
  (webpack, standalone) and run it on :3100 with their server on :8701
  (`VISTA_SCREENCAST_UI_PORT`, `VISTA_SCREENCAST_SERVER_PORT`). They never write `ui/.next`,
  so a running `./launch.sh` stack is safe. The first build of a changed `ui/`
  takes about a minute.
- Capture forwards to the backend at `VISTA_BACKEND_URL` or :8001 and the MCP
  server at `VISTA_MCP_URL` or :8000 (`--backend`, `--mcp` override them).
- `playwright` in this skill's `package.json` is pinned to the version in
  `ui/package-lock.json`, so the browsers installed for the UI's tests are
  reused. Keep them equal.
