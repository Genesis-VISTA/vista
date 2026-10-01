## Context

`docs/multi-agent-framework.md` states that a campaign is defined *entirely* by the
`campaign.yaml` inside its planner skill, and that adding a domain requires no
backend changes. That holds for role→skill→job binding, but the manifest has no
way to say **how a candidate becomes job arguments** or **which files carry the
result** — so the backend guessed: `json.dumps(candidate)` on dispatch, and no
files on collect. Both guesses are wrong for every real `hpc_jobs/` entry, which
is why the gap survived: the mock-domain tests fake the HPC boundary and never
exercise an `argparse` CLI or a `results.json`.

`CampaignPlanner.collect_job` already accepts a `files` argument and
`backend/tests/test_campaign_planner.py:194` already passes one; only the live
`build_collector` seam drops it. No existing test asserts the JSON encoding, so
the blast radius is small.

## Goals / Non-Goals

**Goals:**

- Make candidate→`script_args` encoding declarative, per-role, and deterministic
- Let a role declare the output files its parser needs
- Keep the backend domain-agnostic — new domains still add only skills
- Unblock the alloy campaign, and give SPLASH the fields to adopt later
- Zero behavior change for any manifest that does not opt in
- Hermetic tests only (no cluster, no LLM, no network)

**Non-Goals:**

- Editing `splash-planner/campaign.yaml` or the SPLASH job wrappers
- Multi-objective scoring, planner prompt changes, or `ParsedResult` changes
- A general templating language for arguments
- Backfilling real-cluster integration tests (stays in the validation lane)
- VISTAGuard

## Decisions

1. **Per-subagent `args.map`, not per-variable.**
   - Rationale: the same candidate feeds roles that need different subsets under
     different flag names. In SPLASH, `temperature` is `--temperature` for chemistry
     and has no neutronics flag at all; `li6_enrichment` is `--li6` for neutronics
     and has no chemistry flag. A per-variable `arg:` field cannot express that. A
     variable absent from a role's `map` is simply not passed.

2. **`encoding: flags` | `json`, with an absent `args` block preserving today's
   `json.dumps(candidate)` behavior.**
   - Rationale: `flags` is what every real job actually needs, but defaulting to it
     would silently change dispatch for any manifest that has not opted in. Making
     "no `args` block" mean "exactly what happens today" keeps the change strictly
     additive: SPLASH is untouched and adopts the fields in its own change.

3. **Deterministic ordering by manifest variable declaration order.**
   - Rationale: makes rendered `script_args` assertable as a golden string in unit
     tests, and makes job logs diffable across cycles.

4. **`extra` is a fixed, non-candidate argument string per role.**
   - Rationale: real jobs need options that are not design variables
     (`--production-steps 250000`, `--allow-extrapolation`). Keeping them in the
     manifest avoids a second place to look.

5. **`collect_files` lives on the subagent spec in `campaign.yaml`, not in the sim
   skill's frontmatter.**
   - Rationale: the manifest is documented as the single place a campaign is
     defined, and `collect_files` sits naturally beside the `job:` it belongs to.
   - Trade-off: the file list is arguably a property of the *job* rather than the
     campaign, so two campaigns reusing one sim skill must repeat it. Accepted for
     now — see Open Questions.

6. **Empty/absent `args` block keeps a role working with no candidate arguments.**
   - Rationale: a role whose job takes only `extra` (or nothing) must not be forced
     to declare an empty map.

## Risks / Trade-offs

- [Silent behavior change for an existing manifest] → eliminated by design: absent
  `args` means today's JSON encoding, absent `collect_files` means today's empty
  outputs. Opting in is the only way to change behavior.
- [Flag maps drift from the job's actual `argparse` definition] → unit-test each
  shipped manifest's rendered `script_args` against its job wrapper's accepted flags.
- [`collect_files` duplicated across campaigns reusing one sim skill] → accepted;
  revisit via sim-skill frontmatter if a second campaign reuses a role.
- [Fetching large outputs into the parser prompt] → keep declared files small and
  structured (`results.json`), never logs or trajectories.

## Migration Plan

1. Add the manifest fields with defaults that keep `load_manifest` accepting every
   existing `campaign.yaml` unchanged and behaving identically.
2. Render `script_args` from the spec in `dispatch_candidate`.
3. Thread `collect_files` through `build_collector`.
4. Document the fields in the manifest sample in `docs/multi-agent-framework.md`.

SPLASH adoption is deliberately out of scope. The first manifest to exercise the
new fields is the alloy campaign's, which is written against them from the start.

No data migration: `CampaignRun`/`CampaignStep`/`HpcJob` rows are untouched.

## Open Questions

- Should `collect_files` eventually move to sim-skill frontmatter
  (`metadata.collect_files`) so it is declared once per job rather than per campaign?
- Should the renderer validate a candidate variable that has no entry in any role's
  `map` (likely a manifest typo) and warn, or stay silent? This is not hypothetical:
  SPLASH declares four design variables, but the job wrappers accept flags for only
  two of them. `salt-neutronics-tbr/run_state_point.py` exposes `--bef2`,
  `--be-multiplier`, `--li6`, `--nominal-bef2`, `--allow-extrapolation`, and
  `salt-chemistry-md/run_state_point.py` exposes `--mol-percent-bef2`,
  `--temperature` and run options. So `li6_enrichment` → `--li6` and
  `be_concentration` → `--be-multiplier` map cleanly, `temperature` reaches only
  chemistry, and **`blanket_thickness` has no flag in any wrapper** — the campaign
  would silently optimize over a variable no simulation ever sees. Deciding this
  requires a domain call (extend the neutronics wrapper, or drop the variable from
  the manifest) and is tracked as a follow-up for SPLASH's owners. This change does
  not touch SPLASH, so the gap is recorded here rather than acted on.
