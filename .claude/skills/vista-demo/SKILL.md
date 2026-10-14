---
name: vista-demo
description: >-
  Demo videos of VISTA: make, re-record or edit one by replaying a scripted session
  of the real UI. Use when asked for a demo video, screen recording, walkthrough or
  README clip of VISTA, or to change one already made.
---

# VISTA demo videos

A video is a **script** (`script.yaml`) replayed against VISTA's real UI with a
fake backend, so every take is identical and nothing live is touched. Each video
is a folder under `~/.vista/demo-captures/<video>/`:

```
script.yaml             what happens, step by step (see SCRIPT.md)
capture/responses.jsonl the backend's answers the pages need
assets/                 figures and files the script's runs return
out/                    contact.jpg, frames/, video.mp4 (written by the tools)
```

Videos are public by default: they and their folders stay out of the repository,
which mirrors to a public GitHub repo. The tools refuse to write inside it.

## Steps

1. **Set up once per machine.** `npm ci` in this skill's folder, `npm ci` in
   `ui/`, and ffmpeg on the PATH (macOS: `brew install ffmpeg`). Done when
   `node .claude/skills/vista-demo/bin/vista-demo.mjs selftest` writes
   `~/.vista/demo-captures/_selftest/out/video.mp4`.
2. **Agree the storyboard with the researcher.** Scenes in order, each with its
   caption, the prompts typed, and what the agent shows. Apply the framing
   rules below. Done when the researcher has approved it.
3. **Write the script.** Start the video's folder as a copy of the self-test:
   `cp -R .claude/skills/vista-demo/selftest ~/.vista/demo-captures/<video>`.
   Then make every trace of the self-test yours: `script.yaml` (its `video:`
   name, prompts, runs) per [`SCRIPT.md`](SCRIPT.md); `capture/responses.jsonl`
   (project name and description, conversation titles, and each entry's `step`
   after you add or remove steps); and `assets/` (replace `wave.svg` with the
   figures your runs return). Done when `grep -rniE 'sine|damped|"demo"'` in
   the folder finds nothing left over from the self-test. Every result, number and figure that isn't
   from a real run is a placeholder: note it in a comment at the top of the
   script.
4. **Rehearse until clean.** `vista-demo rehearse <video>` plays fast and writes
   `out/contact.jpg`, one screenshot per step. Any failure names the step and
   what's missing: a locator nothing matches, a request the capture can't
   answer, a run the UI never started. Fix the script or `responses.jsonl` and
   rerun. Done when it finishes and every tile of the contact sheet shows what
   the storyboard says.
5. **Record and assemble.** `vista-demo record <video>`, then
   `vista-demo assemble <video>`. `out/frames/marks.json` gives each step's
   start in seconds; pull a still a second or two into each `type` and
   `camera` step (`ffmpeg -ss <s> -i out/video.mp4 -frames:v 1 still.jpg`) and
   look at it. Done when every prompt being typed is readable and no zoom cuts
   off what the scene is about.
6. **Hand over.** Give the paths to `video.mp4`, the script, and the list of
   placeholders from step 3.

## Framing rules

- `card: intro` opens every video. It states VISTA's public positioning
  verbatim and is the only description of VISTA a video gives.
- Each project shown is a use case: introduce it with `chapter: <use case>`
  (a "Use case" card with the use case as its title), never as what VISTA is.
- The default arc ends on an HPC step, since the positioning promises campaigns
  on OLCF and AmSC resources.
- Cards carry the VISTA name and icon only: no lab, programme or facility
  logos or names.

## Gotchas

- `VISTA_DEMO_HOME` replaces `~/.vista/demo-captures` as the home of every
  video (and of the self-test's output and the private UI build), e.g. a
  scratch folder for a trial run. `--help` prints the home in effect.
- The tools build their own copy of `ui/` under `~/.vista/demo-captures/_ui/`
  (webpack, standalone) and run it on :3100 with the replay server on :8701
  (`VISTA_DEMO_UI_PORT`, `VISTA_DEMO_SERVER_PORT`). They never write `ui/.next`,
  so a running `./launch.sh` stack is safe. The first build of a changed `ui/`
  takes about a minute.
- `playwright` in this skill's `package.json` is pinned to the version in
  `ui/package-lock.json`, so the browsers installed for the UI's tests are
  reused. Keep them equal.
- Capture of live sessions (`capture`, `script`) arrives with task group 2 of
  the `demo-video` OpenSpec change; until then scripts are hand-written.
