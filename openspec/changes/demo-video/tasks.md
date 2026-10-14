# Tasks

Nothing here runs in PR CI (the toolkit is a skill, design D1). Tasks marked
**live** need a running stack (`./launch.sh logs --no-electron`) and a model
provider; tasks marked **hpc** also spend cluster allocation.

## 1. Replay toolkit (hand-written scripts)

- [x] 1.1 Create `.claude/skills/vista-demo/` with `package.json` (playwright pinned to `ui/package-lock.json`'s version, `yaml`) and `bin/vista-demo.mjs` listing the commands of design D1; verify `npm ci` in the skill folder succeeds and `node bin/vista-demo.mjs --help` lists them
- [x] 1.2 Implement `lib/ui.mjs` (design D5): private copy of `ui/`, linked `node_modules`, standalone build reused by tree hash; verify `vista-demo ui` serves `/projects` on :3100 while `./launch.sh logs --no-electron` runs on :3000 in the same checkout, and `git status` shows `ui/.next` untouched
- [x] 1.3 Implement `lib/script.mjs` parsing of the YAML script (design D3) into a replay plan, with `!asset` references and `pace`; verify on `selftest/script.yaml` that runs become the event sequence `run-renderer.ts` expects (unit test with `node --test`)
- [x] 1.4 Implement replay mode in `lib/server.mjs` (design D4): runs served in script order as SSE, other requests from `responses.jsonl` by method + path + query, the missing-response error; verify with `node --test` that an unanswered request fails naming method and path, and that two runs play in order
- [x] 1.5 Implement `lib/player.mjs` (design D7) for replay: overlay outside `<body>`, eased cursor, click ripple, human typing, camera, the two caption slots, and the step-naming error for a missing element; verify `vista-demo rehearse selftest` writes screenshots and a contact sheet, and a script with a bogus button name fails at that step
- [x] 1.6 Implement `lib/frames.mjs` and an MP4-only `lib/assemble.mjs` (design D8, without frame or README clip); verify `vista-demo record selftest && vista-demo assemble selftest` writes `out/video.mp4` at 1920×1080 with no audio stream (`ffprobe`)
- [x] 1.7 Port the spike's molten-salt scenes to `script.yaml` form as a second hand-written example (in `~/.vista/demo-captures/`, not the repo) and check every frame of a caption shown while typing leaves the composer visible; verify by reviewing the contact sheet
- [x] 1.8 Draft `SKILL.md` for the replay path (rehearse, record, assemble) following `writing-for-agents`; verify a fresh Claude session can produce the selftest video from the skill alone

## 2. Capture

- [x] 2.1 Implement `lib/scrub.mjs` (design D9) and its final scan; verify with `node --test` on fixtures containing an S3M-style JWT, an API key field and a cookie header that all are replaced and the scan fails on a planted one
- [x] 2.2 Implement capture mode in `lib/server.mjs`: forward to the backend and MCP URLs, pass streams through while recording events with arrival times, tag each response with the current step, scrub before writing; verify with `node --test` against a stub upstream that a streamed run is recorded event by event and forwarded unchanged
- [x] 2.3 Implement `lib/actions.mjs` (design D6) with the locator preference order and the uniqueness check; verify with `node --test` + Playwright on a fixture page that clicks on a card's button are recorded within the card and typing merges into one step
- [x] 2.4 Implement capture → `script.original.yaml` conversion in `lib/script.mjs` (runs to tool and answer steps, images to `assets/`, `log` and thinking dropped) and copy it to `script.yaml`; verify with `node --test` on a recorded run fixture that replaying the converted script yields the same tool calls, results and answer text
- [x] 2.5 **live** `vista-demo capture --by-hand <video>`: headed browser on the private UI with D6 injected; verify by capturing "what is the most studied salt in the database?" by hand and replaying it hermetically with the stack stopped
- [x] 2.6 **live** `vista-demo capture --storyboard <file>` with the `spends:` guard (design D7); verify a storyboard with a `spends: hpc` step stops before it without `--allow-spend`, and captures the whole storyboard when run again with `--again --allow-spend`
- [ ] 2.7 **live** **hpc** Capture the Frontier submit → track → download flow from a storyboard with `--allow-spend`; verify the replayed video shows the real job id and the downloaded chart
- [x] 2.8 Extend `SKILL.md` with both capture paths, the scrubbed-field report, the scratch-project advice and the factual-edit summary before `record`; verify by following it in a fresh session for 2.5's flow

## 3. Page coverage

- [ ] 3.1 Id mapping for fresh ids in queries and paths (design D4, "State over time"); verify with `node --test` that a replayed chat session id issued by `POST .../chat-sessions` is matched in later `chat-session?chat_session_id=` requests
- [ ] 3.2 **live** Capture and replay the Skills pages (Skill Hub and project Skills); verify the replay finishes with no missing-response error and the skill cards match the capture
- [ ] 3.3 **live** Capture and replay Datasets, including file listings and a figure output opened from it; verify as 3.2
- [ ] 3.4 **live** Capture and replay Knowledge Bases, including a publication PDF; verify as 3.2 and that the PDF opens in the replay
- [ ] 3.5 **live** Capture one tour across Projects, Chat (figure-producing run), Skills, Datasets and Knowledge Bases; verify it replays end to end with the stack stopped (the spec's "A tour of the covered pages")

## 4. Framing and export

- [ ] 4.1 `templates/card.html` with the intro card stating the positioning line verbatim, chapter cards for use cases, VISTA name and icon only (`electron/assets/icon.svg`); verify by rendering both and checking the text against the spec's "Framing public videos"
- [ ] 4.2 `templates/frame.html` and the framed MP4 in `lib/assemble.mjs` (design D8): neutral rounded frame titled "VISTA", gradient backdrop, cards full-bleed; verify by checking still frames from `video.mp4`
- [ ] 4.3 README clip from `readme:` markers (default first scene), ≤15 s, 960 px, as WebM (VP9) and GIF; verify sizes and durations with `ffprobe` and that the GIF plays in a GitHub markdown preview
- [ ] 4.4 `vista-demo selftest`: replays `selftest/`, checks the Playwright pin against `ui/`, and leaves `git status` clean; verify it passes on a clean checkout and fails with the right message after bumping the pin
- [ ] 4.5 **live** Make the first public video from a real capture, end to end through the skill, and check it against every requirement in `specs/demo-video/spec.md`; deliver `video.mp4`, `readme.webm` and `readme.gif` for review
