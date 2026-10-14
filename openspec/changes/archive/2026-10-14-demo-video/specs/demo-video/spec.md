# Spec Delta

## Purpose

Let Claude make a VISTA demo video of whatever a researcher asks for, from a real
session: capture it, edit it as a script, and replay it hermetically into a
polished, repeatable recording.

## ADDED Requirements

### Requirement: Capturing a live session

The toolkit SHALL capture a session against a live VISTA backend by standing
between the UI and the backend. It SHALL record every backend response the UI
receives, in order, with its method, path, query, status and body. For a
streamed agent run it SHALL record each event and the time it arrived. The
researcher's own stack SHALL keep running unchanged: capture SHALL use its own
UI on its own port and forward to the backend the stack already runs.

#### Scenario: A captured agent run keeps its events
- **WHEN** the researcher asks a question in a capture session and the agent calls `run_bash`, then `display_file`, then answers
- **THEN** the capture holds that run's events in order, including both tool calls with their arguments and results, the figure, and the full answer text

#### Scenario: The researcher's stack is not disturbed
- **WHEN** a capture session starts while `./launch.sh logs` is running in the same checkout
- **THEN** the stack's UI on :3000 keeps working, and `ui/.next` is not written

### Requirement: Capturing what the researcher does

During capture the toolkit SHALL record the researcher's actions by meaning:
the element acted on, identified by its role and accessible name, its label, or
its visible text, and the text typed into it. It SHALL NOT record pointer
coordinates or pointer paths.

#### Scenario: A click is recorded by what was clicked
- **WHEN** the researcher clicks the "Open" button on the `molten-salt` project card
- **THEN** the script holds a click step naming that button within that card, and no screen coordinates

#### Scenario: Typing is recorded as text
- **WHEN** the researcher types a prompt into the composer and presses Send
- **THEN** the script holds one typing step with the full prompt and one click step for Send

### Requirement: Two ways to drive a capture

A capture SHALL be drivable either by the researcher in a visible browser, or
by Claude from a storyboard the researcher has approved. Both SHALL produce the
same kind of video script. When Claude drives, it SHALL ask before any step
that submits an HPC job or otherwise spends allocation, and SHALL show the
storyboard and wait for approval before the first live step.

#### Scenario: Claude asks before spending allocation
- **WHEN** an approved storyboard includes a scene that submits a job to Frontier
- **THEN** Claude stops before that step and asks, and submits nothing until the researcher agrees

### Requirement: The video script

A capture SHALL produce a video script: a plain-text file of steps in order,
each a UI action, an agent run, or a production step (caption, chapter card,
camera move, pointing at an element, pause). An agent run SHALL appear as
readable steps (each tool call with its arguments and result, each answer as
one block of text), not as the raw event stream. Timing SHALL NOT be stored per
event; it SHALL be generated at replay from pace settings. Any step MAY be
edited, removed, reordered or added. The original capture SHALL be kept
unchanged beside the edited script, so the two can be compared.

#### Scenario: Removing a failed tool call
- **WHEN** a captured run contains a failed `run_bash` call and its retry, and the failed call is deleted from the script
- **THEN** the replayed video shows only the retry

#### Scenario: The original is kept
- **WHEN** the script has been edited and replayed
- **THEN** the original capture is still present and unchanged, and a diff between it and the script shows every edit

### Requirement: Hermetic replay

Replay SHALL serve the UI only from the script and the capture: no request
SHALL reach a real backend, MCP server, model provider or cluster. Agent runs
SHALL be served in the order the script lists them. Other requests SHALL be
answered from the captured responses for the same method and path. A request
with no captured answer SHALL fail the replay with an error naming the method
and path, rather than producing a video with an empty or broken panel.

#### Scenario: Replay without any VISTA services
- **WHEN** a script is replayed with no backend, MCP server or network access to model providers
- **THEN** the video is produced and shows the captured runs

#### Scenario: A missing response is named
- **WHEN** the replayed UI requests `GET /projects/molten-salt/uploads` and the capture has no answer for it
- **THEN** the replay stops with an error naming `GET /projects/molten-salt/uploads`

#### Scenario: Two similar prompts play the right runs
- **WHEN** a script asks two prompts that both mention "phase diagram"
- **THEN** each plays the run captured for it, in script order

### Requirement: Recording a replay

The recorder SHALL play the script in a headless browser at 1920×1080. It SHALL
look each step's element up when the step runs, and fail with an error naming
the step when the element is missing. It SHALL draw its own cursor, moving it
along an eased path to each element and marking each click. It SHALL type at a
human pace. Captions SHALL NOT cover the element being typed into or clicked,
or the answer the camera is focused on.

#### Scenario: A renamed button fails the rehearsal
- **WHEN** the UI's "Open" button has been renamed and an old script is replayed
- **THEN** the run stops at that click step with an error naming it, and no video is written

#### Scenario: Typing stays visible
- **WHEN** a caption is shown while a prompt is typed into the composer
- **THEN** every frame shows the composer and the text typed so far, uncovered

### Requirement: Output formats

Each recording SHALL be assembled into: a 1920×1080 H.264 MP4 that plays in
browsers and video players, showing the UI inside a neutral window frame (no
operating system's window controls) on a gradient backdrop; and a short clip
for the GitHub README in GIF and WebM. Videos SHALL have no audio track.

#### Scenario: Both formats are produced
- **WHEN** a recording finishes
- **THEN** the video's folder holds the MP4 and the README clip in both formats, and none has an audio stream

### Requirement: Captures stay out of the repository

Captures, scripts, frames and videos SHALL be written under
`~/.vista/demo-captures/<video>/` and never inside the repository. Before
writing a capture, the toolkit SHALL remove credentials from it: request
authorization headers and cookies, and the values of token, key and secret
fields in responses. A capture SHALL never hold such a value in clear. The
README's GIF is the one output kept in the repository: a researcher copies it
by hand to `docs/figures/readme-demo.gif`; the toolkit never writes it there.

#### Scenario: A settings response is scrubbed
- **WHEN** a capture session opens Settings and the backend's response holds an inference API key and S3M tokens
- **THEN** the stored capture holds placeholders for them, and searching the capture folder for the real values finds nothing

#### Scenario: Nothing lands in the checkout
- **WHEN** a video is captured, edited, replayed and assembled
- **THEN** `git status` in the checkout shows no new or changed files

### Requirement: Framing public videos

The opening card SHALL show "VISTA" as its headline and, beneath it, what the
name stands for: "Visual Intelligence for Scientific & Tooling Assistant".
Cards SHALL use the VISTA name and icon only, with no lab, programme or
facility logos or names. A project shown in a video SHALL be introduced as a
named use case, never as what VISTA is.

#### Scenario: A single-project video
- **WHEN** a video shows only the `molten-salt` project
- **THEN** its opening card reads "VISTA" over "Visual Intelligence for Scientific & Tooling Assistant", and the project appears as a use case such as "Use case: molten salt properties"

### Requirement: Pages a replay covers

Captures of the Projects, Chat, Skills, Datasets and Knowledge Bases pages
SHALL replay hermetically, including chat runs that call tools and show
figures, file lists and publication PDFs.

#### Scenario: A tour of the covered pages
- **WHEN** a captured session that visits Projects, Chat (with a figure-producing run), Skills, Datasets and Knowledge Bases is replayed
- **THEN** the replay finishes without any missing-response error and every page shows its captured content
