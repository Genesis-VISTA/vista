## Why

The campaign framework (`backend/src/vista_backend/agents/campaign/`) cannot close
a dispatch→collect loop against a real cluster. Two gaps, both invisible to the
current mock-domain tests:

1. **Encoding.** `CampaignPlanner.dispatch_candidate` sends
   `script_args=json.dumps(candidate)`, but every real sim job
   (`hpc_jobs/salt-chemistry-md/run_state_point.py`,
   `hpc_jobs/salt-neutronics-tbr/run_state_point.py`) parses **flat CLI flags**
   via `argparse`. A JSON blob bounces off `argparse` and the job fails at launch.
2. **Collection.** `build_collector` in `wiring.py` calls
   `planner.collect_job(session, job=job)` with no `files`, so `SubAgent.collect`
   leaves `raw_outputs=""` and the result parser sees only raw job *status* text.
   `results.json` never reaches it, so no metric is ever parsed.

These affect SPLASH exactly as much as any new domain, and they block the
alloy-design Tc campaign now being rebuilt on this framework.

## What Changes

- Extend the `campaign.yaml` subagent spec with an `args` block: an `encoding`
  (`flags` | `json`) and a per-role `map` from candidate variable to CLI flag,
  plus a fixed `extra` string for non-candidate options.
- Render `script_args` from that mapping in `CampaignPlanner.dispatch_candidate`,
  in manifest variable-declaration order so output is deterministic.
- Add `collect_files` to the subagent spec; thread it through `build_collector` →
  `CampaignPlanner.collect_job` → `SubAgent.collect` so the parser receives real
  job outputs.
- Keep the change **strictly additive**: a manifest with no `args` block and no
  `collect_files` dispatches and collects exactly as it does today, so no shipped
  campaign changes behavior.
- Cover both with hermetic unit tests; no cluster, LLM, or network required.

## Non-goals

- No change to the planner's LLM behavior, the scorer contract, or `ParsedResult`.
- No new domain skills (the alloy campaign lands separately).
- No edits to `splash-planner` or the SPLASH job wrappers; SPLASH adopts the new
  fields in its own change, when its owners choose to.
- No VISTAGuard work.
- Not changing how `submit_hpc_job` itself parses `script_args`.

## Capabilities

### New Capabilities

- `campaign-sim-contract`: the declarative candidate→`script_args` encoding and
  the collect-file declaration that bind a campaign role to a real HPC job.

### Modified Capabilities

- (none)

## Impact

- `backend/src/vista_backend/agents/campaign/{manifest,planner,wiring}.py`
- `backend/tests/{test_campaign_manifest,test_campaign_planner,test_campaign_wiring}.py`
- `docs/multi-agent-framework.md` (manifest schema sample)
- Prerequisite for the alloy-design campaign rebuild; see
  `docs/skill-onboarding.md` for the sim-skill dispatch/collect contract this makes real.
