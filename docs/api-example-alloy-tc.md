# API example: estimating Tc with `alloy-thermo-mc`

A worked example of driving a real HPC simulation through the vista API, using the
prompt *"estimate the Tc for equimolar MoNbTaW"*.

Runnable script: [`backend/scripts/example_alloy_tc.py`](../backend/scripts/example_alloy_tc.py).
This doc walks the same flow in `curl` so you can port it to any client.

```bash
python backend/scripts/example_alloy_tc.py                              # equimolar
python backend/scripts/example_alloy_tc.py --composition 0.30,0.25,0.25,0.20
python backend/scripts/example_alloy_tc.py --submit-only                # don't wait
```

## What happens

| Step | Call | What the agent does |
|---|---|---|
| 1 | `POST /projects/alloy-design/agent/run` | reads the `alloy-thermo-mc` skill, turns the composition into `submit_hpc_job(job="alloy-thermo-mc", cluster="odo", script_args="--mo 0.25 …")`, returns the job id |
| 2 | `POST /projects/alloy-design/mcp/call` | poll `get_hpc_job_status` until the job leaves the queue |
| 3 | `POST /projects/alloy-design/agent/run` | `get_hpc_job_outputs` fetches `results.json`; the agent reports Tc with its caveats |

**Why two agent turns.** The MC job runs a 112-replica ladder and sits in the Slurm
queue, so it does not complete inside one turn. Turn 1 submits; turn 2 (later) collects.
Polling in between goes through the **MCP call endpoint**, not the agent — there is no
model in that loop, so waiting costs no tokens.

## Prerequisites

1. **A running backend** — `./launch.sh` (default `http://localhost:8001`).
2. **A model** — `VISTA_BACKEND_MODEL`. The agent needs one to plan the tool call.
3. **HPC credentials on your user record.** Without them the agent stops mid-turn and
   *elicits* them interactively, which a non-streaming client cannot answer:

   ```bash
   curl -X PUT http://localhost:8001/users/me \
        -H 'Content-Type: application/json' \
        -d '{"s3m_token": "<your OLCF S3M token>"}'
   ```

   `s3m_token` covers Odo and Frontier; Perlmutter uses `nersc_iri_token`. Check with
   `curl 'http://localhost:8001/users/me?config=true'`. The script preflights this and
   exits with instructions rather than hanging.

**Auth** is the dev-only `X-Vista-User-Email` header. Omit it to get the default dev
user. In production the API returns **501** until SSO lands.

## The calls

### 0. A chat session (so turn 2 remembers turn 1)

```bash
SESSION=$(curl -s -X POST http://localhost:8001/projects/alloy-design/chat-sessions \
  -H 'Content-Type: application/json' -d '{"title":"Tc of equimolar MoNbTaW"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
```

### 1. Ask — the agent submits the job

```bash
curl -s -X POST http://localhost:8001/projects/alloy-design/agent/run \
  -H 'Content-Type: application/json' \
  -d "{\"user_prompt\": \"Estimate the Tc for equimolar MoNbTaW (Mo=0.25, Nb=0.25, Ta=0.25, W=0.25). Submit a single alloy-thermo-mc job on odo with default screening settings — do not start a campaign. Report the job id and stop.\",
       \"stream\": false, \"chat_session_id\": \"$SESSION\"}"
```

The response is a `ProjectAgentResult`: `new_messages` (PydanticAI `ModelMessage`s),
`usage`, and `logs`. The Slurm job id is in the `tool-return` part of `submit_hpc_job`,
whose body is the ground-truth summary:

```
job_id: 44521
cluster: odo
nodes: 2
duration: 0:10:00
```

Telling the agent to **stop after submitting** matters. Left to itself it may poll
`get_hpc_job_status` in-turn, burning requests against the project's 600-request limit
while the job sits in the queue.

### 2. Poll without the model

```bash
curl -s -X POST http://localhost:8001/projects/alloy-design/mcp/call \
  -H 'Content-Type: application/json' \
  -d '{"name":"get_hpc_job_status","arguments":{"job_id":"44521","cluster":"odo"}}'
```

This returns MCP's raw `CallToolResult` envelope, **not** a plain string:

```json
{"content": [{"type": "text", "text": "... STATE=RUNNING ..."}], "isError": false}
```

Unwrap the `content[].text` parts and check `isError`; don't pattern-match the JSON.
Watch for `STATE=COMPLETED` (or `FAILED` / `CANCELLED` / `TIMEOUT`).

### 3. Collect and interpret

```bash
curl -s -X POST http://localhost:8001/projects/alloy-design/agent/run \
  -H 'Content-Type: application/json' \
  -d "{\"user_prompt\": \"The job finished. Fetch its results.json with get_hpc_job_outputs and report the Tc from the specific-heat peak, the susceptibility cross-check, whether they agree, whether the ladder bracketed the peak, and the SRO parameter.\",
       \"stream\": false, \"chat_session_id\": \"$SESSION\"}"
```

## Reading the answer critically

`results.json` carries more than a number, and the skill tells the agent to report it:

- **`Tc_cv_K`** — the headline value (specific-heat peak).
- **`Tc_chi_K`** / **`estimators_agree`** — susceptibility cross-check within 15%.
- **`peak_bracketed`** — **if false, the Tc is not a measurement.** The ladder missed the
  transition and the peak was reported at its high endpoint. Re-run with a widened
  `--t-init` / `--t-final`.
- **`sro_alpha1`** — Warren-Cowley short-range order. A Cv bump with `α ≈ 0` is a random
  solid solution, not an order-disorder transition.
- **`swap_accept_mean`** — healthy ≈ 0.2–0.4; much lower means the replica ladder is too
  coarse to equilibrate.

A single lattice size cannot pin Tc precisely — the peak shifts and sharpens with `N`.
Treat one job as a screening estimate, not a converged number.

## Streaming and elicitation

Set `"stream": true` to get Server-Sent Events instead: PydanticAI agent events, plus
vista's `log` events and `mcp_form_elicitation` / `mcp_url_elicitation`. If the agent
needs credentials it cannot find, it emits an elicitation event and waits — answer with
`POST /projects/{project}/elicitation`. Pre-setting `s3m_token` (above) avoids this
entirely, which is why the non-streaming example can work at all.

## Running a search instead of one point

One composition is one job. To *search* compositions for the highest Tc, ask for a
campaign instead — the `alloy-tc-planner` skill drives `start_campaign` →
`set_campaign_spec` → `save_campaign_plan` → `dispatch_cycle`, fanning out one job per
candidate and scoring each cycle. Those campaign tools are registered on the agent
(PydanticAI tools), so they do **not** appear in `GET /projects/{p}/mcp/tools`, which
lists MCP tools only.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `{"detail":"Unauthorized"}` | `X-Vista-User-Email` names a user that doesn't exist. Omit the header for the default dev user. |
| `{"detail":"..."}` 501 | Production mode — SSO is not implemented yet. |
| Agent describes the job but no `job_id` | It planned without calling the tool, or submission failed. Read the `tool-return` parts and `logs`. |
| `Multiple HPC clusters configured` | Pass `cluster` explicitly to any HPC tool. |
| Turn stalls with no output | Missing credentials → the agent is waiting on an elicitation nothing will answer. Preflight `/users/me?config=true`. |

## Reference

- Sim skill: [`db/skills/alloy-thermo-mc/SKILL.md`](../backend/src/vista_backend/db/skills/alloy-thermo-mc/SKILL.md)
- HPC job: [`hpc_jobs/alloy-thermo-mc/`](../hpc_jobs/alloy-thermo-mc)
- Campaign planner: [`db/skills/alloy-tc-planner/SKILL.md`](../backend/src/vista_backend/db/skills/alloy-tc-planner/SKILL.md)
- Multi-agent framework: [`multi-agent-framework.md`](./multi-agent-framework.md)
