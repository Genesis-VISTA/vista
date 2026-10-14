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

## Steps

Each step has exactly one of these keys.

| Step | Value | What happens |
|---|---|---|
| `card` | `intro`, `outro`, or `{ kicker, title, sub }` | Full-screen title card. `intro` states the positioning line. |
| `chapter` | text | "Use case" card titled with the text. |
| `goto` | a UI path, e.g. `/projects` | Opens that page. |
| `caption` | `[num, text, sub]`, `[text]`, or `off` | Shows or hides the caption box. It takes the top slot, or the bottom when the top would cover the focused field or the camera's target. |
| `click` | locator | Moves the cursor there and clicks. |
| `type` | locator + `text` | Clicks the field and types the text. Fails if the caption covers the field. |
| `run` | list of run steps | The agent's next turn (see below). The step waits until the UI has started it, so it comes after the click that sends the prompt. |
| `camera` | `{ zoom: latest \| <locator>, scale }` or `off` | Eases the view in on the newest exchange (`latest`) or an element, or back out. |
| `wait` | `run` or seconds | `run`: until every started run has finished and the page has drawn it. |
| `pause` | seconds | Holds the frame. |
| `point` | locator | Moves the cursor onto an element without clicking. |
| `spends` | `hpc` | Marks that the next run spends allocation; replay ignores it, capture stops there. |
| `readme` | `start` or `end` | Marks the README clip's bounds. |

## Locators

A locator names an element by what a person sees, resolved when its step runs:

- `{ role: button, name: Open }`: an ARIA role and accessible name; `name` is
  exact unless written as `/regex/flags`.
- `{ placeholder: "Ask a question" }`, `{ label: "Model" }`, `{ text: Frontier }`:
  the placeholder, the label, or the visible text.
- `{ css: .project-card, text: molten-salt }`: elements matching the selector
  that contain the text.
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

One JSON object per line, answering the UI's other requests to the backend:

```json
{"seq": 1, "step": 0, "method": "GET", "path": "/projects", "query": "", "status": 200, "type": "application/json", "body": []}
```

Replay answers a request with the entry of the same method, path and query
(parameters in any order) whose `step` is the latest at or before the step
being played, else the earliest. Steps count from 1 in script order, as
`rehearse` prints them; `step: 0` means "from the start". So a list that
changes during the video gets two entries, e.g. the chat-session list empty at
step 0 and holding the new conversation from the step that sends the first
prompt. Adding or removing steps before that one shifts its number: update the
`step` of the entries tied to it. A request with no entry fails the rehearsal,
naming it.
