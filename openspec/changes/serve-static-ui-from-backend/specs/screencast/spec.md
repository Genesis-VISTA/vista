## MODIFIED Requirements

### Requirement: Capturing a live session

The toolkit SHALL capture a session against a live VISTA backend by standing
between the UI and the backend. It SHALL record every backend response the UI
receives, in order, with its method, path (as the UI requested it, including its
`/api` prefix), query, status and body. For a streamed agent run it SHALL record
each event and the time it arrived. The researcher's own stack SHALL keep
running unchanged: capture SHALL serve its own prebuilt copy of the UI on its
own port, answer the UI's `/api` requests on that same origin, and forward them
to the backend the stack already runs.

#### Scenario: A captured agent run keeps its events
- **WHEN** the researcher asks a question in a capture session and the agent calls `run_bash`, then `display_file`, then answers
- **THEN** the capture holds that run's events in order, including both tool calls with their arguments and results, the figure, and the full answer text

#### Scenario: The researcher's stack is not disturbed
- **WHEN** a capture session starts while `./launch.sh logs` is running in the same checkout
- **THEN** the stack's UI on :3000 keeps working, and neither `ui/.next` nor `ui/out` is written

### Requirement: Hermetic replay

Replay SHALL serve the UI only from the script and the capture: no request
SHALL reach a real backend, MCP server, model provider or cluster. Agent runs
SHALL be served in the order the script lists them. Other requests SHALL be
answered from the captured responses for the same method and path. A request
with no captured answer SHALL fail the replay with an error naming the method
and path, rather than producing a video with an empty or broken panel.
Captures recorded before the API moved under `/api` hold unprefixed paths. They
SHALL NOT be translated, and replaying one SHALL fail with a missing-response
error like any other unanswered request.

#### Scenario: Replay without any VISTA services
- **WHEN** a script is replayed with no backend, MCP server or network access to model providers
- **THEN** the video is produced and shows the captured runs

#### Scenario: A missing response is named
- **WHEN** the replayed UI requests `GET /api/projects/molten-salt/uploads` and the capture has no answer for it
- **THEN** the replay stops with an error naming `GET /api/projects/molten-salt/uploads`

#### Scenario: Two similar prompts play the right runs
- **WHEN** a script asks two prompts that both mention "phase diagram"
- **THEN** each plays the run captured for it, in script order

#### Scenario: A capture from before the prefix
- **WHEN** a capture whose responses are keyed `GET /projects/...` is replayed
- **THEN** the replay stops with a missing-response error naming the first `/api/...` request it could not answer
