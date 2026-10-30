# The video script

`script.yaml` is a list of steps the player runs in order. `selftest/script.yaml`
in this folder is a complete small example.

```yaml
video: my-video
pace: { typing: 1.0, stream_wps: 40, tool_min_s: 1.2, think_s: 0.6 }
steps:
  - card: intro
  - goto: /projects
  # …
```

`pace` sets how replay times things: `typing` multiplies human typing speed,
`stream_wps` is answer words per second, `tool_min_s` the shortest a tool call
is shown running, `think_s` the pause before the agent's first event. Timing is
never stored per event; change `pace` (or a tool's `duration_s`) instead.

`onboarding_tour: true` (top level, beside `pace`) lets the first-boot tour run in
the replay. Without it, replay forces `onboarding_tour_enabled: false` on every
`/settings` answer, so a capture taken on a fresh install never shows the tour.

## A scene

Most scenes take this shape; the comments say why each step sits where it does.

```yaml
  - chapter: Molten salt properties        # once per use case, before its first scene
  - caption: [1, "Ask the database", "MSTDB-TP, counted in the sandbox"]
  - type: { placeholder: "Ask a question", text: "what is the most studied salt?" }
  - click: { role: button, name: "/^(Start chat|Send)$/" }
  - run: [ ... ]                           # returns as soon as the run has started…
  - camera: { zoom: latest, scale: 1.3 }   # …so the zoom happens while it streams
  - wait: run                              # until the answer is fully drawn
  - pause: 2.0                             # time to read it
  - camera: off
  - caption: off                           # or the next scene's caption replaces it
```

Quote caption and other YAML strings that contain a comma or colon:
`[1, Ask VISTA a question, in plain language]` is four items, not three.
`camera` scales between 1.2 and 1.4 keep a chat exchange whole; larger ones
crop its edges (the rehearsal's contact sheet shows the crop).

## Steps

Each step has exactly one of these keys.

| Step | Value | What happens |
|---|---|---|
| `card` | `intro`, `outro`, or `{ kicker, title, sub }` | Full-screen title card. `intro` is "VISTA" over what it stands for. |
| `chapter` | text | "Use case" card titled with the text. |
| `goto` | a UI path, e.g. `/projects` | Opens that page. |
| `caption` | `[num, text, sub]`, `[text]`, or `off` | Shows or hides the caption box. It takes the top slot, or the bottom when the top would cover the focused field or the camera's target. |
| `click` | locator | Moves the cursor there and clicks. |
| `type` | locator + `text` | Clicks the field and types the text. Fails if the caption covers the field. |
| `press` | a key, e.g. `Enter` | Presses it (Enter in the composer sends). |
| `run` | list of run steps, or `live` | The agent's next turn (see below). The step waits until the UI has started it, so it comes after the click that sends the prompt. `live` (storyboards only): the live agent answers, and capture records it. |
| `camera` | `{ zoom: latest \| <locator>, scale }` or `off` | Eases the view in on the newest exchange (`latest`) or an element, or back out. |
| `wait` | `run` or seconds | `run`: until every started run has finished and the page has drawn it. |
| `pause` | seconds | Holds the frame. |
| `point` | locator | Moves the cursor onto an element without clicking. |
| `spends` | `hpc` | Marks that what follows spends allocation. A storyboard capture stops here unless `--allow-spend`; replay ignores it. |
| `readme` | `start` or `end` | Marks the README clip's bounds. |

## Locators

A locator names an element by what a person sees, resolved when its step runs:

- `{ role: button, name: Open }`: an ARIA role and accessible name; `name` is
  exact unless written as `/regex/flags`.
- `{ placeholder: "Ask a question" }`, `{ label: "Model" }`, `{ text: Frontier }`:
  the placeholder, the label, or the visible text.
- `{ css: .project-card, text: molten-salt }`: elements matching the selector
  that contain the text; `{ css: .project-card, has: { text: molten-salt } }`:
  those holding an element whose text is exactly that (use it when another
  card's description mentions the same words).
- `within: <locator>` scopes any of these to an ancestor; `nth: 1` picks the
  second match (default the first).

Prefer role and name, then placeholder or label, then text; reach for `css`
only to scope (as with project cards). A step's target has to be on screen when
the step runs: the resources rail (Odo, Frontier, …) appears only once a
project is open, so a `point` at Frontier goes after the click that opens it.

## Runs

A `run` is one agent turn, as tool calls and answers in order:

```yaml
  - run:
      - tool: run_bash
        args: { command: "python plot.py" }
        duration_s: 2.0            # optional; at least pace.tool_min_s
        result: "Wrote plot.png"
      - tool: display_file
        args: { path: plot.png }
        result: !asset plot.png    # a file in assets/: images show in Artifacts
      - tool: run_bash
        args: { command: "python plot.py --bad" }
        failed: true               # shown as a failed call (a retry prompt)
        result: "exit 2: unknown flag"
      - answer: Working on it…
        interim: true              # shown as a step, collapsed after
      - answer: |
          The final answer, streamed word by word. Markdown renders.
```

Runs are handed to the UI in script order, whatever the prompt says. A result
that is a string shows as the tool's output; `!asset` of an image (png, jpg,
svg, gif) becomes a figure, of anything else its text. A figure is just the
file: to show a different plot, make the image (matplotlib through
`uv run --with matplotlib`, or a hand-written SVG) and put it in `assets/`.

## capture/responses.jsonl

One JSON object per line, answering the UI's other requests to the backend.
Capture writes it, with `responses.original.jsonl` beside it as captured; edit
`responses.jsonl` to clean up what the video shows. A hand-written script
needs one too.

```json
{"seq": 1, "action": 0, "method": "GET", "path": "/projects", "query": "", "status": 200, "type": "application/json", "body": []}
```

`action` is how many UI actions (`goto`, `click`, `type`, `press`) came before
the answer; other steps don't count. A script made from a capture tags each
action step with its number from the capture (`action: 6` beside the `click:`),
so cutting steps never changes which answers replay picks, and an action you
add by hand (untagged) keeps the count where it is. In a hand-written script,
untagged actions are counted in order from 1. Replay
answers a request with the entry of the same method, path and query
(parameters in any order) whose `action` is the latest at or before the current
count, else the earliest. So a list that changes during the video gets two
entries, e.g. the chat-session list at `action: 0` (empty) and at the action
that sends the first prompt (holding the new conversation). In a hand-written
script, adding or removing an action before an entry shifts it. A binary answer (a PDF, an image) has
`"encoding": "base64"`. A request with no entry fails the rehearsal, naming it.
